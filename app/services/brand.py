"""Branding Clipper: logo + overlay yang otomatis dipasang di SEMUA video hasil Clipper.

Pengaturan bersifat global (bukan per proyek) supaya tetap terpakai ketika membuat klip dari
video sumber lain: data/brand.json + file di data/brand/ (logo.<ext>, overlay.<ext>).

  logo    : gambar (PNG transparan disarankan) di salah satu posisi, ukuran = % lebar video
  overlay : gambar/video di atas klip — "full" (bingkai/tekstur seukuran layar) atau seperti logo
            (mis. animasi tombol Subscribe). Bisa layar hijau (chroma), tampil sepanjang video,
            hanya N detik pertama, atau N detik terakhir.
Urutan lapisan: video → overlay → logo → subtitle (subtitle selalu paling atas supaya terbaca).
"""
import json
import threading

from .. import config

DIR = config.DATA_DIR / "brand"
FILE = config.DATA_DIR / "brand.json"
SLOTS = ("logo", "overlay")
IMG_EXT = ("png", "jpg", "jpeg", "webp")
VID_EXT = ("gif", "mp4", "webm", "mov")
POSITIONS = ("tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br")
_lock = threading.Lock()

DEFAULTS = {
    "enabled": True,
    "logo": {"file": None, "pos": "tl", "size": 16, "opacity": 0.9, "margin": 4},
    "overlay": {"file": None, "pos": "full", "size": 40, "opacity": 1.0, "margin": 4,
                "chroma": False, "show": "always", "secs": 5},
}


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def normalize(b: dict | None) -> dict:
    b = b or {}
    out = {"enabled": bool(b.get("enabled", DEFAULTS["enabled"]))}
    for slot in SLOTS:
        d, s = DEFAULTS[slot], {**DEFAULTS[slot], **(b.get(slot) or {})}
        f = s.get("file")
        item = {
            "file": f if isinstance(f, str) and f.split(".")[0] == slot and (DIR / f).is_file() else None,
            "pos": s["pos"] if s["pos"] in POSITIONS or (slot == "overlay" and s["pos"] == "full") else d["pos"],
            "size": _clamp(s.get("size"), 3, 100, d["size"]),
            "opacity": _clamp(s.get("opacity"), 0.05, 1, d["opacity"]),
            "margin": _clamp(s.get("margin"), 0, 20, d["margin"]),
        }
        if slot == "overlay":
            item["chroma"] = bool(s.get("chroma"))
            item["show"] = s["show"] if s.get("show") in ("always", "first", "last") else "always"
            item["secs"] = _clamp(s.get("secs"), 1, 60, d["secs"])
        out[slot] = item
    return out


def load() -> dict:
    try:
        return normalize(json.loads(FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalize(None)


def save(b: dict) -> dict:
    cur = load()
    merged = {**cur, **{k: v for k, v in b.items() if k == "enabled"}}
    for slot in SLOTS:
        if isinstance(b.get(slot), dict):
            merged[slot] = {**cur[slot], **{k: v for k, v in b[slot].items() if k != "file"}}
    out = normalize(merged)
    with _lock:
        FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def store_file(slot: str, filename: str, fileobj) -> dict:
    """Simpan file logo/overlay. Nama file di disk selalu <slot>.<ext> (ekstensi di-whitelist)."""
    import shutil
    if slot not in SLOTS:
        raise ValueError("slot harus logo|overlay")
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    allowed = IMG_EXT if slot == "logo" else IMG_EXT + VID_EXT
    if ext not in allowed:
        raise ValueError(f"Format tidak didukung untuk {slot} (pakai {', '.join(allowed)}).")
    DIR.mkdir(parents=True, exist_ok=True)
    for old in DIR.glob(f"{slot}.*"):
        old.unlink(missing_ok=True)
    with open(DIR / f"{slot}.{ext}", "wb") as f:
        shutil.copyfileobj(fileobj, f)
    with _lock:
        b = load()
        b[slot]["file"] = f"{slot}.{ext}"
        FILE.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    return load()


def remove_file(slot: str) -> dict:
    if slot not in SLOTS:
        raise ValueError("slot harus logo|overlay")
    for old in DIR.glob(f"{slot}.*"):
        old.unlink(missing_ok=True)
    return save({})


def active(b: dict | None = None) -> bool:
    b = b or load()
    return b["enabled"] and any(b[s]["file"] for s in SLOTS)


def _xy(pos: str, m: int) -> tuple[str, str]:
    x = {"l": f"{m}", "c": "(main_w-overlay_w)/2", "r": f"main_w-overlay_w-{m}"}
    y = {"t": f"{m}", "m": "(main_h-overlay_h)/2", "b": f"main_h-overlay_h-{m}"}
    if pos == "c":
        return x["c"], y["m"]
    return x[pos[1] if pos[1] in "lcr" else "c"], y[pos[0]]


def graph(first_input: int, src_label: str, W: int, H: int, total: float, b: dict | None = None):
    """Argumen input FFmpeg + baris filter_complex untuk menempelkan overlay & logo.
    Mengembalikan (input_args, filter_lines, label_keluaran). Tanpa branding aktif: ([], [], src_label)."""
    b = b or load()
    if not active(b):
        return [], [], src_label
    args, lines, cur, idx = [], [], src_label, first_input
    for slot in ("overlay", "logo"):
        it = b[slot]
        if not it["file"]:
            continue
        path = str(DIR / it["file"])
        ext = it["file"].rsplit(".", 1)[-1].lower()
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
        if it["pos"] == "full":
            x, y = "0", "0"
        else:
            x, y = _xy(it["pos"], round(W * it["margin"] / 100))
        enable = ""
        if slot == "overlay" and it["show"] == "first":
            enable = f":enable='lte(t,{it['secs']:.2f})'"
        elif slot == "overlay" and it["show"] == "last":
            enable = f":enable='gte(t,{max(0.0, total - it['secs']):.2f})'"
        out = f"[bv{idx}]"
        lines.append(f"{cur}[br{idx}]overlay={x}:{y}:shortest=1:format=auto{enable}{out}")
        cur, idx = out, idx + 1
    return args, lines, cur
