"""Tahap Thumbnail: editor ala CapCut/Canva dengan Pillow + OpenCV saja (tanpa torch/onnxruntime).

Alur: kandidat frame (dari video TANPA subtitle: klip Clipper / aset scene) dinilai otomatis (ketajaman +
wajah) → pilih frame → template layout → teks besar dengan kata highlight, stroke, bayangan, rotasi →
latar (gelap, gradien, blur, vignette, saturasi/kontras) → opsional potong subjek (grabCut dari kotak wajah)
dengan outline → stiker panah/lingkaran. Hasil JPG ±1280x720 (video panjang) atau 1080x1920 (Shorts).

Data proyek: thumbnail = {frames: [{path, t, score, face}], frame, thumbs: [path], designs: [design],
selected, uploaded}. Dipasang ke YouTube (thumbnails.set) setelah video selesai diproses."""
import time

from .. import config, store
from . import ffmpeg_utils as ff
from . import fonts

SIZES = {"short": (1080, 1920), "long": (1280, 720)}

DESIGN_DEFAULTS = {
    "template": "bawah", "text": "", "font": "Anton", "size": 11.0, "case": "upper",
    "text_color": "#FFFFFF", "highlight_color": "#FFD400", "highlight_bg": "",
    "stroke_color": "#000000", "stroke_w": 8, "shadow": 60,
    "text_x": 50.0, "text_y": 75.0, "align": "center", "rotation": 0, "max_width": 85,
    "darken": 0, "gradient": "bottom", "gradient_strength": 75, "saturation": 1.25, "contrast": 1.1,
    "bg_blur": 0, "vignette": 0, "focus": "face", "subject_x": 50.0, "zoom": 1.0,
    "cutout": False, "outline_color": "#FFFFFF", "outline_w": 10,
    "sticker": "none", "sticker_x": 70.0, "sticker_y": 40.0, "sticker_color": "#FF2D2D",
}
_D = lambda label, desc, **kw: {"label": label, "desc": desc, "design": kw}  # noqa: E731
THUMB_TEMPLATES = {
    "bawah": _D("Teks bawah", "Frame penuh, gradasi gelap bawah, teks besar baris pertama kuning",
                gradient="bottom", text_y=76, align="center", font="Anton", size=11),
    "atas": _D("Teks atas (Shorts)", "Teks di atas — aman dari tombol & judul YouTube di bawah",
               gradient="top", text_y=18, align="center", font="Anton", size=12),
    "kiri": _D("Wajah kanan + teks kiri", "Gradasi hitam dari kiri, subjek di kanan dengan outline putih",
               gradient="left", gradient_strength=85, text_x=6, text_y=50, align="left", max_width=52, size=10,
               subject_x=70, cutout=True, outline_w=10),
    "kotak": _D("Kotak highlight", "Frame digelapkan, kata kunci di kotak merah miring",
                darken=30, gradient="none", text_y=70, highlight_bg="#E50914", highlight_color="#FFFFFF",
                rotation=-3, size=11),
    "sinematik": _D("Sinematik", "Latar blur + vignette, satu kata raksasa",
                    bg_blur=10, vignette=70, gradient="none", font="Bebas Neue", size=22, text_y=52,
                    stroke_w=0, shadow=90, highlight_color="#FFFFFF"),
    "angka": _D("Angka besar", "Angka/kata pertama raksasa kuning, subjek di sisi berlawanan",
                gradient="left", gradient_strength=70, text_x=6, text_y=48, align="left", max_width=55,
                size=15, stroke_w=12, subject_x=72, cutout=True),
    "panah": _D("Panah penunjuk", "Frame tajam + panah merah & lingkaran ke objek, teks di pojok atas",
                gradient="top", gradient_strength=60, text_y=16, size=9, sticker="arrow", sticker_x=62,
                sticker_y=48),
}


# ---------------- kandidat frame ----------------

def _score(img) -> tuple[float, list | None]:
    """Skor frame: ketajaman (variansi Laplacian) + bonus wajah besar & jelas; penalti terlalu gelap/terang."""
    import cv2
    from . import reframe
    small = cv2.resize(img, (480, int(480 * img.shape[0] / img.shape[1])))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sharp = min(1.0, cv2.Laplacian(gray, cv2.CV_64F).var() / 600)
    bright = gray.mean() / 255
    face = None
    try:
        det = _score.det = getattr(_score, "det", None) or reframe._Faces()
        faces = det(small)
        if faces:
            x, y, w, h, sc = max(faces, key=lambda f: f[2] * f[3])
            H, W = small.shape[:2]
            face = [round((x + w / 2) / W, 3), round((y + h / 2) / H, 3), round(w / W, 3), round(h / H, 3)]
    except Exception:  # noqa: BLE001 — skor tanpa wajah tetap jalan
        pass
    s = sharp * 0.5 + (min(1.0, face[2] * 4) * 0.5 if face else 0) - abs(bright - 0.5) * 0.4
    return round(s, 3), face


def _source(p: dict) -> list[tuple[str, float | None]]:
    """Daftar (file relatif, detik) sumber frame TANPA subtitle."""
    d = store.project_dir(p["id"])
    if p["mode"] == "clipper" and p["assets"].get("clip_path"):
        clip = p["assets"]["clip_path"]
        dur = ff.duration(d / clip) or 30
        n = 20
        return [(clip, round(dur * (k + 0.5) / n, 2)) for k in range(n)]
    out = []
    for s in p["script"].get("scenes") or []:
        sel = ((p["assets"].get("scenes") or {}).get(s["id"]) or {}).get("selected") or {}
        path = sel.get("path")
        if path and (d / path).is_file():
            is_img = path.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp")
            out.append((path, None if is_img else max(0.3, (ff.duration(d / path) or 2) * 0.4)))
    return out


def frames(pid: str, params: dict, progress) -> dict:
    """Ambil kandidat frame & nilai otomatis. params.t (detik, Clipper) → tambah satu frame pilihan sendiri."""
    import cv2
    p = store.load(pid)
    d = store.project_dir(pid)
    (d / "thumb").mkdir(exist_ok=True)
    if params.get("t") is not None and p["mode"] == "clipper" and p["assets"].get("clip_path"):
        srcs = [(p["assets"]["clip_path"], float(params["t"]))]
    else:
        srcs = _source(p)
    if not srcs:
        raise RuntimeError("Belum ada sumber gambar (klip / aset scene). Unggah gambar sendiri bila perlu.")
    found = []
    stamp = int(time.time()) % 100000
    for k, (src, t) in enumerate(srcs):
        progress(f"Mengambil frame {k + 1}/{len(srcs)}…")
        rel = f"thumb/f_{stamp}_{k}.jpg"
        if t is None:
            img = cv2.imread(str(d / src))
            if img is None:
                continue
            cv2.imwrite(str(d / rel), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        else:
            ff.run(["-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-q:v", "2", rel], cwd=d)
            img = cv2.imread(str(d / rel))
            if img is None:
                continue
        sc, face = _score(img)
        found.append({"path": rel, "t": t, "score": sc, "face": face, "src": src})
    if params.get("t") is None:  # simpan 8 terbaik (urut waktu), hapus sisanya
        keep = sorted(sorted(found, key=lambda f: -f["score"])[:8], key=lambda f: (f["src"], f["t"] or 0))
        for f in found:
            if f not in keep:
                (d / f["path"]).unlink(missing_ok=True)
        found = keep

    def apply(q):
        th = q["thumbnail"]
        old = th.get("frames") or []
        th["frames"] = (old + found) if params.get("t") is not None else found
        if params.get("t") is not None or not th.get("frame"):
            th["frame"] = (found[0] if params.get("t") is not None else max(found, key=lambda f: f["score"]))["path"] \
                if found else th.get("frame")
        store.touch_stage(q, "thumbnail")
    store.update(pid, apply)
    return {"frames": len(found)}


def save_upload(pid: str, filename: str, fileobj) -> dict:
    """Gambar latar sendiri (mis. foto ekspresi wajah) → jadi kandidat frame."""
    import cv2
    import numpy as np
    d = store.project_dir(pid)
    (d / "thumb").mkdir(exist_ok=True)
    data = np.frombuffer(fileobj.read(), np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("File bukan gambar (pakai JPG/PNG/WEBP).")
    rel = f"thumb/up_{int(time.time())}.jpg"
    cv2.imwrite(str(d / rel), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    sc, face = _score(img)

    def apply(q):
        q["thumbnail"].setdefault("frames", []).append({"path": rel, "t": None, "score": sc, "face": face,
                                                         "src": "upload"})
        q["thumbnail"]["frame"] = rel
        store.touch_stage(q, "thumbnail")
    return store.update(pid, apply)


# ---------------- komposisi ----------------

def _rgb(h: str, default=(255, 255, 255)) -> tuple:
    h = (h or "").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else default


def normalize(design: dict | None, fmt: str) -> dict:
    d = dict(design or {})
    base = dict(DESIGN_DEFAULTS)
    tpl = THUMB_TEMPLATES.get(d.get("template") or base["template"])
    if tpl:
        base.update(tpl["design"])
    if fmt == "short" and (d.get("template") or base["template"]) == "bawah":
        base["text_y"] = 62  # Shorts: bagian bawah tertutup judul & tombol
    base.update({k: v for k, v in d.items() if v is not None})
    for k in ("size", "text_x", "text_y", "max_width", "saturation", "contrast", "subject_x", "zoom",
              "sticker_x", "sticker_y"):
        try:
            base[k] = float(base[k])
        except (TypeError, ValueError):
            base[k] = float(DESIGN_DEFAULTS[k])
    for k in ("stroke_w", "shadow", "rotation", "darken", "gradient_strength", "bg_blur", "vignette", "outline_w"):
        try:
            base[k] = int(round(float(base[k])))
        except (TypeError, ValueError):
            base[k] = DESIGN_DEFAULTS[k]
    base["cutout"] = bool(base["cutout"])
    return base


def _cover(im, W, H, focus_x: float, focus_y: float, place_x: float, zoom: float):
    """Isi kanvas W×H; titik fokus (0..1 di gambar) diletakkan di place_x (0..1 di kanvas)."""
    from PIL import Image
    s = max(W / im.width, H / im.height) * max(1.0, zoom)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    left = int(focus_x * im.width - place_x * W)
    top = int(focus_y * im.height - 0.42 * H)
    left = max(0, min(im.width - W, left))
    top = max(0, min(im.height - H, top))
    return im.crop((left, top, left + W, top + H)), (left, top, s)


def _cutout_mask(im, face_box):
    """Mask subjek (orang) dengan grabCut yang diinisialisasi dari kotak wajah. None bila gagal."""
    import cv2
    import numpy as np
    from PIL import Image
    W, H = im.size
    if not face_box:
        return None
    cx, cy, fw, fh = face_box
    sc = 480 / W
    small = cv2.cvtColor(np.array(im.resize((480, int(H * sc)))), cv2.COLOR_RGB2BGR)
    sh, sw = small.shape[:2]
    x0 = int(max(0, (cx - fw * 1.6) * sw))
    x1 = int(min(sw - 1, (cx + fw * 1.6) * sw))
    y0 = int(max(0, (cy - fh * 1.0) * sh))
    rect = (x0, y0, max(10, x1 - x0), max(10, sh - y0 - 1))
    mask = np.zeros((sh, sw), np.uint8)
    try:
        cv2.grabCut(small, mask, rect, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 4,
                    cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    # ambil komponen terbesar yang menyentuh wajah, haluskan tepi
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return None
    fx, fy = int(cx * sw), int(cy * sh)
    keep = lab[min(sh - 1, fy), min(sw - 1, fx)] or (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))
    m = np.where(lab == keep, 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    if m.mean() < 255 * 0.03:
        return None
    return Image.fromarray(m).resize((W, H), Image.LANCZOS)


def _font(name: str, px: int):
    from PIL import ImageFont
    fp = fonts.path(name) or fonts.path("Anton")
    try:
        return ImageFont.truetype(str(fp), px) if fp else ImageFont.truetype("arialbd.ttf", px)
    except OSError:
        return ImageFont.load_default()


def _text_layer(ds: dict, W: int, H: int):
    """Lapisan teks transparan (sudah termasuk bayangan & rotasi) + posisi tempel."""
    from PIL import Image, ImageDraw, ImageFilter
    raw = ds["text"].strip()
    if ds["case"] == "upper":
        raw = raw.upper()
    elif ds["case"] == "lower":
        raw = raw.lower()
    if not raw:
        return None, (0, 0)
    max_w = W * ds["max_width"] / 100

    def wrap(font):  # bungkus per lebar maks (pertahankan baris baru manual)
        out = []
        for para in raw.split("\n"):
            cur = []
            for w in para.split():
                test = " ".join(x.strip("*") for x in cur + [w])
                if cur and font.getlength(test) > max_w:
                    out.append(cur)
                    cur = [w]
                else:
                    cur.append(w)
            if cur:
                out.append(cur)
        return out

    # auto-fit: kecilkan huruf sampai blok teks muat (tinggi ≤ 70% kanvas, kata terpanjang ≤ lebar maks)
    px = max(12, int(W * ds["size"] / 100))
    while True:
        font = _font(ds["font"], px)
        lines = wrap(font)
        too_tall = len(lines) * px * 1.06 > H * 0.7
        too_wide = any(font.getlength(w.strip("*")) > max_w for ln in lines for w in ln)
        if not (too_tall or too_wide) or px <= 16:
            break
        px = int(px * 0.92)
    lines = lines[:6]
    has_star = "*" in raw
    longest = max((w.strip("*") for ln in lines for w in ln), key=len, default="")
    lh = int(px * 1.06)
    sw = ds["stroke_w"]
    pad = sw + px // 3
    space = font.getlength(" ")
    widths = [sum(font.getlength(w.strip("*")) for w in ln) + space * (len(ln) - 1) for ln in lines]
    bw, bh = int(max(widths) + pad * 2), int(lh * len(lines) + pad * 2)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    hl, col = _rgb(ds["highlight_color"]), _rgb(ds["text_color"])
    hbg = _rgb(ds["highlight_bg"], None) if ds["highlight_bg"] else None
    for li, (ln, lw) in enumerate(zip(lines, widths)):
        x = pad + {"left": 0, "right": max(widths) - lw}.get(ds["align"], (max(widths) - lw) / 2)
        y = pad + li * lh
        for w in ln:
            word = w.strip("*")
            is_hl = (w.startswith("*") and w.endswith("*")) if has_star else (
                li == 0 if ds["template"] in ("bawah", "atas", "angka", "panah") else word == longest)
            ww = font.getlength(word)
            if is_hl and hbg:
                bpad = px * 0.12
                dr.rounded_rectangle([x - bpad, y + px * 0.02, x + ww + bpad, y + px * 1.02], radius=int(px * 0.12),
                                     fill=hbg + (255,))
            dr.text((x, y), word, font=font, fill=(hl if is_hl else col) + (255,),
                    stroke_width=0 if (is_hl and hbg) else sw, stroke_fill=_rgb(ds["stroke_color"], (0, 0, 0)))
            x += ww + space
    if ds["shadow"] > 0:  # bayangan lembut di bawah teks
        a = layer.getchannel("A").point(lambda v: int(v * ds["shadow"] / 100))
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        sh.putalpha(a.filter(ImageFilter.GaussianBlur(px * 0.06)))
        base = Image.new("RGBA", (bw + px // 6, bh + px // 6), (0, 0, 0, 0))
        base.alpha_composite(sh, (px // 10, px // 8))
        base.alpha_composite(layer, (0, 0))
        layer = base
    if ds["rotation"]:
        layer = layer.rotate(ds["rotation"], resample=Image.BICUBIC, expand=True)
    tx = W * ds["text_x"] / 100
    ty = H * ds["text_y"] / 100
    ox = {"left": tx - pad, "right": tx - layer.width + pad}.get(ds["align"], tx - layer.width / 2)
    return layer, (int(ox), int(ty - layer.height / 2))


def _sticker(im, ds: dict):
    from PIL import ImageDraw
    if ds["sticker"] == "none":
        return
    W, H = im.size
    dr = ImageDraw.Draw(im)
    c = _rgb(ds["sticker_color"], (255, 45, 45))
    x, y = W * ds["sticker_x"] / 100, H * ds["sticker_y"] / 100
    u = min(W, H) * 0.09
    if ds["sticker"] in ("circle", "arrow"):
        r = u * 1.3
        if ds["sticker"] == "circle" or ds["sticker"] == "arrow":
            dr.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255), width=int(u * 0.22))
            dr.ellipse([x - r, y - r, x + r, y + r], outline=c, width=int(u * 0.13))
    if ds["sticker"] == "arrow":  # panah dari kiri-bawah menunjuk lingkaran
        bx, by = x - u * 3.6, y + u * 2.4
        tip = (x - u * 1.45, y + u * 0.95)
        import math
        ang = math.atan2(tip[1] - by, tip[0] - bx)
        def rot(px_, py_):
            return (tip[0] + px_ * math.cos(ang) - py_ * math.sin(ang), tip[1] + px_ * math.sin(ang) + py_ * math.cos(ang))
        L = math.hypot(tip[0] - bx, tip[1] - by)
        body = [rot(-L, -u * 0.22), rot(-u * 0.9, -u * 0.22), rot(-u * 0.9, -u * 0.6), rot(0, 0),
                rot(-u * 0.9, u * 0.6), rot(-u * 0.9, u * 0.22), rot(-L, u * 0.22)]
        dr.polygon(body, fill=c, outline=(255, 255, 255), width=int(u * 0.1))


def compose(frame_path, frame_info: dict | None, design: dict, fmt: str, out_path) -> None:
    import numpy as np
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    W, H = SIZES[fmt]
    ds = normalize(design, fmt)
    src = Image.open(frame_path).convert("RGB")
    face = (frame_info or {}).get("face")
    fx, fy = (face[0], face[1]) if face and ds["focus"] == "face" else (0.5, 0.45)
    im, (left, top, s) = _cover(src, W, H, fx, fy, ds["subject_x"] / 100, ds["zoom"])
    # posisi wajah di kanvas (untuk potong subjek)
    face_c = None
    if face:
        face_c = [(face[0] * src.width * s - left) / W, (face[1] * src.height * s - top) / H,
                  face[2] * src.width * s / W, face[3] * src.height * s / H]
    subject = im.copy()
    im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(ds["saturation"])).enhance(ds["contrast"])
    subject = ImageEnhance.Contrast(ImageEnhance.Color(subject).enhance(ds["saturation"])).enhance(ds["contrast"])
    if ds["bg_blur"]:
        im = im.filter(ImageFilter.GaussianBlur(ds["bg_blur"] * W / 1280))
    if ds["darken"]:
        im = Image.blend(im, Image.new("RGB", (W, H), (0, 0, 0)), ds["darken"] / 100)
    if ds["gradient"] != "none":
        g = np.zeros((H, W), np.float32)
        k = ds["gradient_strength"] / 100
        if ds["gradient"] in ("bottom", "top"):
            ramp = np.clip((np.arange(H) / H - 0.45) / 0.55, 0, 1) if ds["gradient"] == "bottom" else \
                np.clip((0.55 - np.arange(H) / H) / 0.55, 0, 1)
            g[:] = ramp[:, None]
        else:
            ramp = np.clip((0.75 - np.arange(W) / W) / 0.75, 0, 1) if ds["gradient"] == "left" else \
                np.clip((np.arange(W) / W - 0.25) / 0.75, 0, 1)
            g[:] = ramp[None, :]
        mask = Image.fromarray((g ** 1.2 * 255 * k).astype(np.uint8))
        im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, mask)
    if ds["vignette"]:
        yy, xx = np.mgrid[0:H, 0:W]
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        v = np.clip((r - 0.6) / 0.8, 0, 1) * ds["vignette"] / 100
        im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, Image.fromarray((v * 255).astype(np.uint8)))
    if ds["cutout"] and face_c:
        m = _cutout_mask(subject, face_c)
        if m is not None:
            import cv2
            if ds["outline_w"] > 0:
                k_ = max(3, int(ds["outline_w"] * W / 1280) * 2 + 1)
                dil = cv2.dilate(np.array(m), np.ones((k_, k_), np.uint8))
                im = Image.composite(Image.new("RGB", (W, H), _rgb(ds["outline_color"])), im,
                                     Image.fromarray(dil).filter(ImageFilter.GaussianBlur(1.2)))
            im = Image.composite(subject, im, m.filter(ImageFilter.GaussianBlur(1.0)))
    im = im.convert("RGBA")
    layer, pos = _text_layer(ds, W, H)
    if layer is not None:
        # jaga blok teks tetap di dalam kanvas (margin 2%)
        mx, my = int(W * 0.02), int(H * 0.02)
        x = max(mx, min(W - layer.width - mx, pos[0])) if layer.width < W - 2 * mx else (W - layer.width) // 2
        y = max(my, min(H - layer.height - my, pos[1])) if layer.height < H - 2 * my else (H - layer.height) // 2
        im.alpha_composite(layer, (x, y))
    im = im.convert("RGB")
    _sticker(im, ds)
    im.save(out_path, "JPEG", quality=90, optimize=True)


# ---------------- aksi ----------------

def default_text(p: dict) -> str:
    """Teks thumbnail: hook klip / teks layar scene pertama / judul — dipendekkan (1-5 kata ideal)."""
    t = ""
    if p["mode"] == "clipper":
        clips, sel = p["script"].get("clips") or [], p["script"].get("selected_clip")
        if sel is not None and sel < len(clips):
            t = clips[sel].get("hook") or clips[sel].get("title") or ""
    else:
        t = ((p["script"].get("scenes") or [{}])[0]).get("on_screen_text") or ""
    t = (t or p["publish"].get("title") or p["title"]).replace("*", "")
    words = t.split()
    return " ".join(words[:6])


def render_design(pid: str, params: dict, progress) -> dict:
    """Render satu desain → slot thumbnail (params.slot; default slot terpilih / baru)."""
    p = store.load(pid)
    d = store.project_dir(pid)
    th = p["thumbnail"]
    frame = params.get("frame") or th.get("frame")
    if not frame or not (d / frame).is_file():
        raise RuntimeError("Pilih frame dulu (klik \"Ambil kandidat frame\").")
    info = next((f for f in th.get("frames") or [] if f["path"] == frame), None)
    design = dict(params.get("design") or {})
    design.setdefault("text", default_text(p))
    slot = params.get("slot")
    thumbs, designs = list(th.get("thumbs") or []), list(th.get("designs") or [])
    while len(designs) < len(thumbs):
        designs.append({})
    if slot is None or slot >= len(thumbs):
        slot = len(thumbs)
    progress(f"Menyusun thumbnail {slot + 1}…")
    rel = f"thumb/thumb_{slot}_{int(time.time()) % 100000}.jpg"
    compose(d / frame, info, design, p["format"], d / rel)
    old = thumbs[slot] if slot < len(thumbs) else None
    if old and old.startswith("thumb/") and old != rel:
        (d / old).unlink(missing_ok=True)
    if slot < len(thumbs):
        thumbs[slot], designs[slot] = rel, {**design, "frame": frame}
    else:
        thumbs.append(rel)
        designs.append({**design, "frame": frame})

    def apply(q):
        q["thumbnail"].update({"thumbs": thumbs, "designs": designs, "selected": slot, "frame": frame})
        store.touch_stage(q, "thumbnail")
        store.log(q, f"Thumbnail {slot + 1} dirender ({design.get('template', 'bawah')})")
    store.update(pid, apply)
    return {"slot": slot, "path": rel}


def remove(pid: str, params: dict, progress) -> dict:
    slot = int(params.get("slot", -1))

    def apply(q):
        th = q["thumbnail"]
        if 0 <= slot < len(th.get("thumbs") or []):
            (store.project_dir(pid) / th["thumbs"][slot]).unlink(missing_ok=True)
            th["thumbs"].pop(slot)
            if slot < len(th.get("designs") or []):
                th["designs"].pop(slot)
            th["selected"] = max(0, min(th.get("selected", 0), len(th["thumbs"]) - 1))
            store.touch_stage(q, "thumbnail")
    store.update(pid, apply)
    return {}


def auto(pid: str, params: dict, progress) -> dict:
    """Otomatis: kandidat frame → frame terbaik → 3 variasi template (untuk A/B test). Bisa diedit kemudian."""
    p = store.load(pid)
    if not (p["thumbnail"].get("frames")):
        frames(pid, {}, progress)
        p = store.load(pid)
    th = p["thumbnail"]
    best = th.get("frame") or max(th["frames"], key=lambda f: f["score"])["path"]
    tpls = ["atas", "kotak", "sinematik"] if p["format"] == "short" else ["kiri", "kotak", "bawah"]
    text = params.get("text") or default_text(p)
    for slot, tpl in enumerate(tpls):
        render_design(pid, {"frame": best, "slot": slot, "design": {"template": tpl, "text": text}}, progress)
    store.update(pid, lambda q: q["thumbnail"].update({"selected": 0}))
    return {"thumbs": len(tpls)}


def upload(pid: str, params: dict, progress) -> dict:
    """Pasang thumbnail terpilih ke video YouTube yang sudah diunggah."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from .google_auth import service

    if not config.google_enabled():
        raise RuntimeError("Google belum terhubung (python run.py --setup-google).")
    p = store.load(pid)
    pub, th = p["publish"], p["thumbnail"]
    if not pub.get("video_id"):
        raise RuntimeError("Upload video ke YouTube dulu, baru pasang thumbnail.")
    thumbs = th.get("thumbs") or []
    sel = int(params.get("index", th.get("selected") or 0))
    if not thumbs or sel >= len(thumbs):
        raise RuntimeError("Buat thumbnail dulu (tahap Thumbnail).")
    yt = service("youtube", "v3")
    progress("Memeriksa status pemrosesan video di YouTube…")
    items = yt.videos().list(part="processingDetails", id=pub["video_id"]).execute().get("items") or []
    st = ((items[0].get("processingDetails") or {}).get("processingStatus")) if items else None
    if st and st != "succeeded":
        raise RuntimeError(f"Video masih diproses YouTube (status: {st}). Coba pasang thumbnail beberapa menit lagi.")
    progress("Mengunggah thumbnail…")
    try:
        yt.thumbnails().set(videoId=pub["video_id"],
                            media_body=MediaFileUpload(str(store.project_dir(pid) / thumbs[sel]), mimetype="image/jpeg")
                            ).execute()
    except HttpError as e:
        hint = (" Channel perlu diverifikasi (nomor HP) di youtube.com/verify untuk thumbnail kustom."
                if e.resp.status == 403 else "")
        raise RuntimeError(f"YouTube menolak thumbnail ({e.resp.status}).{hint}") from e

    def apply(q):
        q["thumbnail"]["selected"] = sel
        q["thumbnail"]["uploaded"] = store.now_iso()
        store.log(q, f"Thumbnail {sel + 1} dipasang ke YouTube")
    store.update(pid, apply)
    return {"ok": True}
