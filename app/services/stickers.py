"""Stiker PNG (objek tanpa latar) untuk B-roll Clipper — tampil di atas video tanpa menutupi wajah pembicara.

Sumber, urut:
1. Microsoft Fluent Emoji 3D (lisensi MIT, ±1300 objek 3D PNG transparan) — daftar nama diambil sekali dari GitHub
   (disimpan 30 hari di data/cache), file lewat CDN jsDelivr. Dicocokkan dari nama objek bahasa Inggris.
2. Gambar AI objek "terisolasi di latar putih polos" (Cloudflare FLUX / Pollinations ber-key, lihat story._text2img)
   → latar dipotong (`cutout_white`: flood fill latar terang dari tepi + grabCut) → PNG transparan.
Tanpa onnxruntime/rembg (diblokir Smart App Control) — cukup OpenCV 4.x.
"""
import json
import re
import time
from pathlib import Path

import httpx

from .. import config

FLUENT_TREE = "https://api.github.com/repos/microsoft/fluentui-emoji/git/trees/main?recursive=1"
FLUENT_CDN = "https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/"
_STOP = {"a", "an", "the", "of", "on", "in", "with", "and", "for", "at", "to", "single", "concert", "stage"}


def _fluent_index() -> list[str]:
    """Path semua PNG 3D Fluent Emoji (cache 30 hari)."""
    cache = config.DATA_DIR / "cache" / "fluent3d.json"
    try:
        if cache.is_file() and time.time() - cache.stat().st_mtime < 30 * 86400:
            return json.loads(cache.read_text(encoding="utf-8"))
    except ValueError:
        pass
    r = httpx.get(FLUENT_TREE, timeout=60, headers={"User-Agent": "shiroe-content-studio"})
    r.raise_for_status()
    paths = [x["path"] for x in r.json().get("tree", []) if x["path"].endswith("_3d.png")]
    if paths:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(paths), encoding="utf-8")
    return paths


def _words(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", (t or "").lower()) if w not in _STOP]


def fluent_match(obj: str, n: int = 2) -> list[dict]:
    """Kandidat Fluent 3D yang namanya cocok dengan nama objek (mis. "studio microphone", "money bag")."""
    want = _words(obj)
    if not want:
        return []
    try:
        paths = _fluent_index()
    except Exception:  # noqa: BLE001
        return []
    scored = []
    for p in paths:
        name = p.split("/")[1]
        have = _words(name)
        if not have:
            continue
        hit = sum(1 for w in want if w in have or (w.endswith("s") and w[:-1] in have))
        if hit == 0:
            continue
        # semua kata objek harus ada bila objeknya 1-2 kata; nama emoji pendek lebih diutamakan
        if hit < min(len(want), 2):
            continue
        scored.append((hit / len(want) - 0.05 * max(0, len(have) - hit), name, p))
    scored.sort(reverse=True)
    return [{"provider": "fluent", "url": FLUENT_CDN + p.replace(" ", "%20"), "title": f"{name} (Fluent Emoji 3D)",
             "author": "Microsoft", "license": "MIT", "page_url": "https://github.com/microsoft/fluentui-emoji",
             "requires_attribution": False} for _, name, p in scored[:n]]


def cutout_white(src: Path, dst: Path) -> bool:
    """Gambar objek berlatar putih/terang polos → PNG transparan. Latar = piksel terang & rendah saturasi yang
    tersambung ke tepi (flood fill), dirapikan grabCut, tepi dihaluskan, dipangkas ke objek. False bila objek tidak
    terpisah jelas (latar tidak polos / objek terlalu kecil atau memenuhi gambar)."""
    import cv2
    import numpy as np
    img = cv2.imread(str(src))
    if img is None:
        return False
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    light = ((hsv[..., 2] > 215) & (hsv[..., 1] < 40)).astype(np.uint8)
    _, lab = cv2.connectedComponents(light, connectivity=4)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge)) & (light > 0)
    if not 0.04 < 1 - bg.mean() < 0.85:
        return False
    gc = np.where(bg, cv2.GC_BGD, cv2.GC_PR_FGD).astype(np.uint8)
    core = cv2.erode((~bg).astype(np.uint8), np.ones((9, 9), np.uint8))
    gc[core > 0] = cv2.GC_FGD
    try:
        cv2.grabCut(img, gc, None, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 3,
                    cv2.GC_INIT_WITH_MASK)
        mask = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    except cv2.error:
        mask = np.where(bg, 0, 255).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return False
    big = stats[1:, cv2.CC_STAT_AREA].max()
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 0.08 * big]
    mask = cv2.GaussianBlur(np.isin(lab, keep).astype(np.uint8) * 255, (5, 5), 0)
    ys, xs = np.where(mask > 20)
    pad = int(0.03 * max(w, h))
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = min(w, xs.max() + 1 + pad), min(h, ys.max() + 1 + pad)
    cv2.imwrite(str(dst), np.dstack([img, mask])[y0:y1, x0:x1])
    return True


def outline(path: Path, px: int = 0) -> None:
    """Gaya stiker: garis tepi putih tebal + bayangan halus di sekeliling objek (kontras di latar gelap/ramai)."""
    from PIL import Image, ImageFilter
    im = Image.open(path).convert("RGBA")
    px = px or max(8, int(max(im.size) * 0.03))
    w, h = im.width + 4 * px, im.height + 4 * px
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(im, (2 * px, 2 * px))
    a = canvas.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    ring = a.filter(ImageFilter.MaxFilter(px * 2 + 1)).filter(ImageFilter.GaussianBlur(1))
    shadow = a.filter(ImageFilter.MaxFilter(px * 2 + 1)).filter(ImageFilter.GaussianBlur(px))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (w, h), (0, 0, 0, 110)), (0, int(px * 0.6)), shadow)
    out.paste(Image.new("RGBA", (w, h), (255, 255, 255, 255)), (0, 0), ring)
    out.alpha_composite(canvas)
    out.save(path)


def ai_sticker(desc: str, dst: Path, seed: int = 7, progress=lambda m: None) -> dict | None:
    """Gambar AI satu objek di latar putih → PNG transparan. None bila gagal dibuat / latar tidak bisa dipotong."""
    from . import story
    raw = dst.with_suffix(".raw.jpg")
    prompt = (f"{desc}, single object, studio product photo, isolated on a plain pure white background, "
              f"centered, full object visible, no text")
    try:
        raw.write_bytes(story._text2img(prompt, 768, 768, seed, progress))
        ok = cutout_white(raw, dst)
    except Exception:  # noqa: BLE001
        ok = False
    finally:
        raw.unlink(missing_ok=True)
    if not ok:
        return None
    return {"provider": "ai", "url": "", "title": desc, "author": "Gambar AI", "license": "Gambar buatan AI",
            "requires_attribution": False}
