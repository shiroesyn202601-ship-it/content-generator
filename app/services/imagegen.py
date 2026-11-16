"""Gambar AI gratis: Cloudflare Workers AI (FLUX.1 schnell) sebagai sumber utama.

Kenapa: sejak 10 Jul 2026 Pollinations tidak lagi mengisi ulang kredit gratis per jam, dan jalur tanpa key
dibatasi ±1 gambar per 45 dtk + logo. Cloudflare memberi 10rb Neuron/hari gratis (±200+ gambar FLUX schnell
4 langkah) dan tidak memakai data untuk melatih model. Pollinations tetap dipakai sebagai cadangan
(dan untuk mode referensi karakter, yang belum ada di FLUX schnell).

Key: CLOUDFLARE_API_KEY=<account_id>:<token> (boleh banyak akun, dipisah koma) atau
CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN. Token cukup izin "Workers AI: Read".
"""
import base64
import io

import httpx

from .. import config
from . import keys, usage

URL = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"


def available() -> bool:
    return keys.has("cloudflare")


def _fit(img: bytes, w: int, h: int) -> bytes:
    """FLUX schnell di Cloudflare selalu 1024×1024 → potong tengah ke rasio video (9:16 / 16:9)."""
    from PIL import Image
    im = Image.open(io.BytesIO(img)).convert("RGB")
    tw = min(im.width, int(im.height * w / h))
    th = min(im.height, int(im.width * h / w))
    x, y = (im.width - tw) // 2, (im.height - th) // 2
    out = io.BytesIO()
    im.crop((x, y, x + tw, y + th)).save(out, "JPEG", quality=92)
    return out.getvalue()


def cf_image(prompt: str, w: int, h: int, seed: int = 1, progress=lambda m: None) -> bytes:
    """Satu gambar FLUX schnell dari Cloudflare (rotasi akun; 429 → akun didinginkan, coba akun lain)."""
    import time
    model = config.CF_IMAGE_MODEL
    orient = "vertical portrait composition, subject centered" if h > w else "wide horizontal composition"
    body = {"prompt": f"{prompt}, {orient}"[:2000], "steps": 4, "seed": int(seed) % 2_000_000_000 or 1}
    errors = []
    for _ in range(max(1, len(keys.pool("cloudflare"))) + 1):
        key = keys.take("cloudflare", model)
        if key is None:
            break
        acct, _, tok = key.partition(":")
        progress(f"Membuat gambar AI (Cloudflare FLUX){' · ' + keys.label('cloudflare', key) if keys.label('cloudflare', key) else ''}…")
        t0 = time.time()
        try:
            r = httpx.post(URL.format(acct=acct, model=model), headers={"Authorization": f"Bearer {tok}"},
                           json=body, timeout=120)
        except httpx.HTTPError as e:
            errors.append(f"jaringan: {e}")
            continue
        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
            b64 = ((data or {}).get("result") or {}).get("image") or (data or {}).get("image")
            raw = base64.b64decode(b64) if b64 else (r.content if data is None else b"")
            if raw:
                usage.ai("Gambar", "Cloudflare", model, time.time() - t0, "gambar scene")
                return _fit(raw, w, h)
            errors.append("respons tanpa gambar")
            continue
        if r.status_code == 429:  # Neuron harian habis / batas per menit
            keys.cool("cloudflare", key, model, 3600 if "daily" in r.text.lower() or "neuron" in r.text.lower() else 60)
        elif r.status_code in (401, 403) or (r.status_code == 404 and "7003" in r.text):  # token / Account ID salah
            keys.kill("cloudflare", key, f"{r.status_code} {r.text[:80]}")
        errors.append(f"{r.status_code} {r.text[:120]}")
    raise RuntimeError("Cloudflare gagal membuat gambar: " + ("; ".join(errors) or "semua akun sedang kena batas"))
