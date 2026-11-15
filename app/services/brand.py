"""Branding Clipper: background overlay + icon + logo yang otomatis dipasang di SEMUA video hasil Clipper.

Pengaturan bersifat global (bukan per proyek) supaya tetap terpakai ketika membuat klip dari
video sumber lain: data/brand.json + file di data/brand/ (logo.<ext>, icon_<id>.<ext>).

  tint  : lapisan warna seluruh layar — polos (transparansi diturunkan) atau gradien ke transparan
          (bawah/atas/atas+bawah/kiri/kanan/vignette/tengah). Dibuat sebagai PNG RGBA saat render.
  icons : daftar gambar/video (bisa lebih dari satu, mis. animasi tombol Subscribe + stiker) —
          "full" (bingkai seukuran layar) atau di salah satu posisi. Bisa layar hijau (chroma),
          tampil sepanjang video, hanya N detik pertama, atau N detik terakhir.
  logo  : gambar (PNG transparan disarankan) di salah satu posisi, ukuran = % lebar video
Urutan lapisan: video → background overlay → icon (urut daftar) → logo → subtitle.
"""
import hashlib
import json
import re
import threading
import uuid

from .. import config

DIR = config.DATA_DIR / "brand"
FILE = config.DATA_DIR / "brand.json"
IMG_EXT = ("png", "jpg", "jpeg", "webp")
VID_EXT = ("gif", "mp4", "webm", "mov")
POSITIONS = ("tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br")
TINT_STYLES = ("solid", "bottom", "top", "both", "left", "right", "vignette", "center")
MAX_ICONS = 8
_lock = threading.Lock()

LOGO_DEFAULT = {"file": None, "pos": "tl", "size": 16, "opacity": 0.9, "margin": 4}
ICON_DEFAULT = {"id": "", "file": None, "pos": "br", "size": 30, "opacity": 1.0, "margin": 4,
                "chroma": False, "show": "always", "secs": 5}
TINT_DEFAULT = {"on": False, "color": "#000000", "opacity": 0.6, "style": "bottom", "reach": 50}


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _file_ok(f, prefix: str) -> bool:
    return isinstance(f, str) and f.rsplit(".", 1)[0] == prefix and (DIR / f).is_file()


def _norm_place(s: dict, d: dict, allow_full: bool) -> dict:
    return {
        "pos": s.get("pos") if s.get("pos") in POSITIONS or (allow_full and s.get("pos") == "full") else d["pos"],
        "size": _clamp(s.get("size"), 3, 100, d["size"]),
        "opacity": _clamp(s.get("opacity"), 0.05, 1, d["opacity"]),
        "margin": _clamp(s.get("margin"), 0, 20, d["margin"]),
    }


def normalize(b: dict | None) -> dict:
    b = b or {}
    out = {"enabled": bool(b.get("enabled", True))}
    s = {**LOGO_DEFAULT, **(b.get("logo") or {})}
    out["logo"] = {"file": s["file"] if _file_ok(s["file"], "logo") else None, **_norm_place(s, LOGO_DEFAULT, False)}

    raw_icons = b.get("icons")
    if raw_icons is None and isinstance(b.get("overlay"), dict):  # format lama: satu "overlay"
        raw_icons = [{**b["overlay"], "id": "overlay"}]
    icons = []
    for it in (raw_icons or [])[:MAX_ICONS]:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "")
        if not re.fullmatch(r"[a-z0-9]{1,16}", iid):
            continue
        s = {**ICON_DEFAULT, **it}
        prefix = "overlay" if iid == "overlay" else f"icon_{iid}"
        if not _file_ok(s.get("file"), prefix):
            continue  # icon tanpa file tidak disimpan
        icons.append({"id": iid, "file": s["file"], **_norm_place(s, ICON_DEFAULT, True),
                      "chroma": bool(s.get("chroma")),
                      "show": s["show"] if s.get("show") in ("always", "first", "last") else "always",
                      "secs": _clamp(s.get("secs"), 1, 60, ICON_DEFAULT["secs"])})
    out["icons"] = icons

    t = {**TINT_DEFAULT, **(b.get("tint") or {})}
    out["tint"] = {
        "on": bool(t.get("on")),
        "color": t["color"] if isinstance(t.get("color"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", t["color"]) else "#000000",
        "opacity": _clamp(t.get("opacity"), 0.05, 1, TINT_DEFAULT["opacity"]),
        "style": t["style"] if t.get("style") in TINT_STYLES else "bottom",
        "reach": _clamp(t.get("reach"), 10, 100, TINT_DEFAULT["reach"]),
    }
    return out


def load() -> dict:
    try:
        return normalize(json.loads(FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize(None)


def _write(b: dict) -> dict:
    out = normalize(b)
    FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def save(b: dict) -> dict:
    """Simpan pengaturan dari UI. Nama file tidak bisa diubah lewat sini (hanya lewat unggah)."""
    with _lock:
        cur = load()
        if "enabled" in b:
            cur["enabled"] = bool(b["enabled"])
        if isinstance(b.get("logo"), dict):
            cur["logo"] = {**cur["logo"], **{k: v for k, v in b["logo"].items() if k != "file"}}
        if isinstance(b.get("tint"), dict):
            cur["tint"] = {**cur["tint"], **b["tint"]}
        if isinstance(b.get("icons"), list):
            by_id = {i["id"]: i for i in cur["icons"]}
            icons = []
            for it in b["icons"]:  # urutan dari UI, hanya icon yang sudah ada
                if isinstance(it, dict) and it.get("id") in by_id:
                    icons.append({**by_id[it["id"]], **{k: v for k, v in it.items() if k not in ("file", "id")}})
            cur["icons"] = icons
        return _write(cur)


def files() -> set[str]:
    b = load()
    return {f for f in [b["logo"]["file"], *(i["file"] for i in b["icons"])] if f}


def store_file(slot: str, filename: str, fileobj) -> dict:
    """slot: "logo" | "icon" (tambah icon baru) | "icon-<id>" (ganti file icon)."""
    import shutil
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    allowed = IMG_EXT if slot == "logo" else IMG_EXT + VID_EXT
    if slot != "logo" and not (slot == "icon" or slot.startswith("icon-")):
        raise ValueError("slot harus logo | icon | icon-<id>")
    if ext not in allowed:
        raise ValueError(f"Format tidak didukung (pakai {', '.join(allowed)}).")
    DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        b = load()
        if slot == "logo":
            prefix, target = "logo", b["logo"]
        elif slot == "icon":
            if len(b["icons"]) >= MAX_ICONS:
                raise ValueError(f"Maksimal {MAX_ICONS} icon.")
            iid = uuid.uuid4().hex[:8]
            prefix, target = f"icon_{iid}", {**ICON_DEFAULT, "id": iid}
            b["icons"].append(target)
        else:
            iid = slot[5:]
            target = next((i for i in b["icons"] if i["id"] == iid), None)
            if not target:
                raise ValueError("Icon tidak ditemukan.")
            prefix = target["file"].rsplit(".", 1)[0]
        for old in DIR.glob(f"{prefix}.*"):
            old.unlink(missing_ok=True)
        with open(DIR / f"{prefix}.{ext}", "wb") as f:
            shutil.copyfileobj(fileobj, f)
        target["file"] = f"{prefix}.{ext}"
        b["enabled"] = True  # baru unggah = jelas ingin dipasang
        return _write(b)


def remove_file(slot: str) -> dict:
    with _lock:
        b = load()
        if slot == "logo":
            prefix = "logo"
        elif slot.startswith("icon-"):
            it = next((i for i in b["icons"] if i["id"] == slot[5:]), None)
            if not it:
                raise ValueError("Icon tidak ditemukan.")
            prefix = it["file"].rsplit(".", 1)[0]
            b["icons"] = [i for i in b["icons"] if i is not it]
        else:
            raise ValueError("slot harus logo | icon-<id>")
        for old in DIR.glob(f"{prefix}.*"):
            old.unlink(missing_ok=True)
        return _write(b)


def active(b: dict | None = None) -> bool:
    b = b or load()
    return b["enabled"] and bool(b["logo"]["file"] or b["icons"] or b["tint"]["on"])


# ---------------------------------------------------------------- background overlay (PNG RGBA)
def tint_alpha(style: str, reach: float, W: int, H: int):
    """Peta alfa 0..1 (numpy HxW). Gradien linear ke transparan, sama dengan pratinjau CSS di UI."""
    import numpy as np
    r = max(0.01, reach / 100)
    y = (np.arange(H, dtype=np.float32) + 0.5)[:, None] / H
    x = (np.arange(W, dtype=np.float32) + 0.5)[None, :] / W
    ramp = lambda d: np.clip(1 - d / r, 0, 1)  # noqa: E731 — d = jarak dari tepi berwarna (0..1)
    if style == "solid":
        a = np.ones((H, W), np.float32)
    elif style == "bottom":
        a = np.broadcast_to(ramp(1 - y), (H, W))
    elif style == "top":
        a = np.broadcast_to(ramp(y), (H, W))
    elif style == "both":
        a = np.broadcast_to(np.maximum(ramp(y), ramp(1 - y)), (H, W))
    elif style == "left":
        a = np.broadcast_to(ramp(x), (H, W))
    elif style == "right":
        a = np.broadcast_to(ramp(1 - x), (H, W))
    else:
        d = np.sqrt(((x - 0.5) / 0.5) ** 2 + ((y - 0.5) / 0.5) ** 2) / np.sqrt(2)  # 0 tengah, 1 sudut
        a = np.clip((d - (1 - r)) / r, 0, 1) if style == "vignette" else ramp(d)
    return np.ascontiguousarray(a, dtype=np.float32)


def tint_png(t: dict, W: int, H: int):
    """Buat (atau pakai cache) PNG background overlay seukuran video."""
    import numpy as np
    from PIL import Image
    key = hashlib.md5(json.dumps([t, W, H], sort_keys=True).encode()).hexdigest()[:12]
    path = DIR / "cache" / f"tint_{key}.png"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for old in path.parent.glob("tint_*.png"):
        old.unlink(missing_ok=True)
    c = int(t["color"][1:], 16)
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = c >> 16, (c >> 8) & 255, c & 255
    img[..., 3] = np.round(tint_alpha(t["style"], t["reach"], W, H) * t["opacity"] * 255).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(path)
    return path


# ---------------------------------------------------------------- FFmpeg
def _xy(pos: str, m: int) -> tuple[str, str]:
    x = {"l": f"{m}", "c": "(main_w-overlay_w)/2", "r": f"main_w-overlay_w-{m}"}
    y = {"t": f"{m}", "m": "(main_h-overlay_h)/2", "b": f"main_h-overlay_h-{m}"}
    if pos == "c":
        return x["c"], y["m"]
    return x[pos[1] if pos[1] in "lcr" else "c"], y[pos[0]]


def graph(first_input: int, src_label: str, W: int, H: int, total: float, b: dict | None = None):
    """Argumen input FFmpeg + baris filter_complex untuk menempelkan background overlay, icon & logo.
    Mengembalikan (input_args, filter_lines, label_keluaran). Tanpa branding aktif: ([], [], src_label)."""
    b = b or load()
    if not active(b):
        return [], [], src_label
    layers = []
    if b["tint"]["on"]:
        layers.append(("tint", str(tint_png(b["tint"], W, H)), {"pos": "full", "opacity": 1.0}))
    layers += [("icon", str(DIR / i["file"]), i) for i in b["icons"]]
    if b["logo"]["file"]:
        layers.append(("logo", str(DIR / b["logo"]["file"]), b["logo"]))
    args, lines, cur, idx = [], [], src_label, first_input
    for kind, path, it in layers:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in IMG_EXT:
            args += ["-loop", "1", "-framerate", str(config.FPS), "-i", path]
        else:
            if ext == "webm":
                args += ["-c:v", "libvpx-vp9"]  # dekoder libvpx supaya kanal alpha WebM terbaca
            args += ["-stream_loop", "-1", "-i", path]
        chain = []
        if it.get("chroma"):
            chain.append("colorkey=0x00FF00:0.30:0.10")
        if it["pos"] == "full":
            chain.append(f"scale={W}:{H}")
        else:
            chain.append(f"scale={max(2, round(W * it['size'] / 100 / 2) * 2)}:-2")
        chain.append(f"fps={config.FPS}")
        chain.append("format=rgba")
        if it["opacity"] < 1:
            chain.append(f"colorchannelmixer=aa={it['opacity']:.2f}")
        lines.append(f"[{idx}:v]{','.join(chain)}[br{idx}]")
        x, y = ("0", "0") if it["pos"] == "full" else _xy(it["pos"], round(W * it["margin"] / 100))
        enable = ""
        if kind == "icon" and it["show"] == "first":
            enable = f":enable='lte(t,{it['secs']:.2f})'"
        elif kind == "icon" and it["show"] == "last":
            enable = f":enable='gte(t,{max(0.0, total - it['secs']):.2f})'"
        out = f"[bv{idx}]"
        lines.append(f"{cur}[br{idx}]overlay={x}:{y}:shortest=1:format=auto{enable}{out}")
        cur, idx = out, idx + 1
    return args, lines, cur
