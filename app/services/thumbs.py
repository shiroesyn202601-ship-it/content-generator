"""Thumbnail: 3 kandidat dari frame video final + teks hook besar, lalu dipasang ke YouTube (thumbnails.set).

Shorts: thumbnails.set untuk Shorts didukung sejak Nov 2026 (cek revision_history). Channel harus sudah
terverifikasi (nomor HP) agar boleh memasang thumbnail kustom."""
import textwrap

from .. import config, store
from . import ffmpeg_utils as ff
from . import fonts

SIZES = {"short": (1080, 1920), "long": (1280, 720)}
FONT = "Anton"


def _font(size: int):
    from PIL import ImageFont
    fonts.ensure(FONT)
    for f in sorted(fonts.FONT_DIR.glob("Anton*.ttf")) + sorted(fonts.FONT_DIR.glob("Montserrat-ExtraBold*.ttf")):
        return ImageFont.truetype(str(f), size)
    try:
        return ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _text(p: dict) -> str:
    sc = (p["script"].get("scenes") or [{}])[0] if p["mode"] == "scratch" else {}
    t = sc.get("on_screen_text") or p["publish"].get("title") or p["title"]
    return t.replace("*", "").upper()


def compose(frame_path, text: str, out_path, fmt: str, variant: int) -> None:
    from PIL import Image, ImageDraw, ImageEnhance
    W, H = SIZES[fmt]
    im = Image.open(frame_path).convert("RGB")
    s = max(W / im.width, H / im.height)  # isi penuh lalu crop tengah
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1))
    left, top = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((left, top, left + W, top + H))
    im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(1.25)).enhance(1.1)  # lebih "pop" di feed
    # gradasi gelap di area teks supaya terbaca
    shade = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shade)
    band_top = int(H * (0.55 if variant != 1 else 0.0))
    for y in range(H):
        inside = (y >= band_top) if variant != 1 else (y <= int(H * 0.45))
        if inside:
            k = (y - band_top) / max(H - band_top, 1) if variant != 1 else 1 - y / (H * 0.45)
            d.line([(0, y), (W, y)], fill=int(170 * min(1.0, 0.25 + k)))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, shade)
    draw = ImageDraw.Draw(im)
    size = int(W * (0.13 if fmt == "short" else 0.09))
    font = _font(size)
    lines = textwrap.wrap(text, width=12 if fmt == "short" else 18)[:4]
    colors = [(255, 230, 0), (255, 255, 255), (255, 59, 107)]
    y = int(H * (0.62 if variant != 1 else 0.08))
    for k, ln in enumerate(lines):
        w = draw.textlength(ln, font=font)
        fill = colors[variant % 3] if k == 0 else (255, 255, 255)
        draw.text(((W - w) / 2, y), ln, font=font, fill=fill, stroke_width=max(4, size // 14), stroke_fill=(0, 0, 0))
        y += int(size * 1.08)
    im.save(out_path, "JPEG", quality=88, optimize=True)


def make(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    video = p["render"].get("output_path")
    if not video or not (d / video).is_file():
        raise RuntimeError("Render video dulu (tahap 6) — thumbnail diambil dari video final.")
    dur = ff.duration(d / video)
    text = params.get("text") or _text(p)
    out = []
    for k, frac in enumerate((0.15, 0.45, 0.75)):
        progress(f"Thumbnail {k + 1}/3: mengambil frame detik {dur * frac:.1f}…")
        frame = f"render/thumb_src_{k}.jpg"
        ff.run(["-ss", f"{dur * frac:.2f}", "-i", video, "-frames:v", "1", "-q:v", "2", frame], cwd=d)
        rel = f"render/thumb_{k}.jpg"
        compose(d / frame, text, d / rel, p["format"], k)
        (d / frame).unlink(missing_ok=True)
        out.append(rel)

    def apply(q):
        q["publish"]["thumbs"] = out
        q["publish"]["thumb_text"] = text
        q["publish"].setdefault("thumb_selected", 0)
        store.log(q, "Thumbnail: 3 kandidat dibuat")
    store.update(pid, apply)
    return {"thumbs": out}


def upload(pid: str, params: dict, progress) -> dict:
    """Pasang thumbnail terpilih ke video YouTube yang sudah diunggah."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from .google_auth import service

    if not config.google_enabled():
        raise RuntimeError("Google belum terhubung (python run.py --setup-google).")
    p = store.load(pid)
    pub = p["publish"]
    if not pub.get("video_id"):
        raise RuntimeError("Upload video ke YouTube dulu, baru pasang thumbnail.")
    thumbs = pub.get("thumbs") or []
    sel = int(params.get("index", pub.get("thumb_selected") or 0))
    if not thumbs or sel >= len(thumbs):
        raise RuntimeError("Buat thumbnail dulu.")
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
        q["publish"]["thumb_selected"] = sel
        q["publish"]["thumb_uploaded"] = store.now_iso()
        store.log(q, f"Thumbnail {sel + 1} dipasang ke YouTube")
    store.update(pid, apply)
    return {"ok": True}
