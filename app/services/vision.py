"""Juri visual: AI benar-benar MELIHAT thumbnail kandidat footage (bukan hanya membaca tag).

Thumbnail digabung jadi satu "lembar kontak" bernomor (hemat: 1 gambar per scene), lalu model vision
Groq memilih nomor yang paling cocok dengan narasi, era, dan budaya topik."""
import io

import httpx

from . import llm

CELL = 160  # px per sel; grid 4x2 → gambar 640x320 (kecil = hemat token gambar di batas per menit Groq)
COLS = 4

PICK_SCHEMA = llm.strict({"index": llm.INT, "fit": {"type": "string", "enum": ["cocok", "lumayan", "lemah"]},
                          "reason": llm.STR})


def contact_sheet(thumb_urls: list[str]) -> bytes | None:
    """Unduh thumbnail & susun grid bernomor 0..n-1. None bila tidak ada yang bisa diunduh."""
    from PIL import Image, ImageDraw, ImageFont

    urls = thumb_urls[:COLS * 2]
    rows = (len(urls) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * CELL, rows * CELL), (20, 20, 24))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    ok = 0
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        for i, url in enumerate(urls):
            x, y = (i % COLS) * CELL, (i // COLS) * CELL
            try:
                r = c.get(url)
                r.raise_for_status()
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                im.thumbnail((CELL - 6, CELL - 6))
                sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))
                ok += 1
            except Exception:  # noqa: BLE001 — sel kosong tetap diberi nomor
                pass
            draw.rectangle([x + 2, y + 2, x + 50, y + 48], fill=(255, 230, 0))
            draw.text((x + 12, y + 4), str(i), fill=(0, 0, 0), font=font)
    if not ok:
        return None
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def pick(topic: str, narration: str, thumb_urls: list[str], avoid: list[str]) -> dict | None:
    """Kembalikan {index, fit, reason} atau None bila penilaian visual tidak tersedia."""
    img = contact_sheet(thumb_urls)
    if img is None:
        return None
    n = min(len(thumb_urls), COLS * 2)
    prompt = f"""Kamu editor video. Gambar ini berisi {n} thumbnail kandidat stok footage, bernomor 0-{n - 1}
(nomor di kotak kuning kiri atas tiap sel).
Topik video: "{topic}"
Narasi scene: "{narration}"
Pilih SATU nomor yang isinya paling cocok dengan narasi. Nilai: (1) makna sesuai narasi, (2) budaya/lokasi
sesuai topik, (3) era sesuai (topik sejarah jangan visual modern). Hindari: {', '.join(avoid[:15]) or '-'}.
PENTING: tempat/bangunan yang jelas milik budaya lain (piramida Meksiko/Mesir, Colosseum, pagoda Jepang, kastil
Eropa) = "lemah" untuk topik Indonesia, walaupun suasananya mirip. Lebih baik pilih b-roll netral yang pas.
"fit": cocok (ketiganya terpenuhi) | lumayan (suasana pas, objek tidak persis) | lemah (melanggar salah satu).
"reason": alasan singkat bahasa Indonesia (maks 15 kata) — sebutkan apa yang TERLIHAT di gambar.
Balas JSON: {{"index": 0, "fit": "cocok", "reason": "..."}}"""
    res = llm.vision_json(prompt, img, PICK_SCHEMA)
    idx = res.get("index")
    if not isinstance(idx, int) or not 0 <= idx < n:
        return None
    return {"index": idx, "fit": res.get("fit") or "lumayan", "reason": str(res.get("reason") or "")}
