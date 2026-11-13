"""Font subtitle. libass (filter `ass=` FFmpeg) diam-diam memakai font cadangan bila font tidak
ditemukan, sehingga subtitle Shorts tampil dengan Arial biasa. Modul ini memastikan font yang
dipakai gaya subtitle ada di data/fonts (diunduh otomatis untuk font open-license populer)."""
import os
from pathlib import Path

import httpx

from .. import config

FONT_DIR = config.DATA_DIR / "fonts"

# Semua berlisensi SIL Open Font License (bebas dipakai komersial, termasuk video YouTube).
CATALOG = {
    "montserrat extrabold": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-ExtraBold.ttf",
    "anton": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "poppins extrabold": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-ExtraBold.ttf",
    "bebas neue": "https://raw.githubusercontent.com/google/fonts/main/ofl/bebasneue/BebasNeue-Regular.ttf",
}


def _norm(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def _system_dirs() -> list[Path]:
    dirs = [FONT_DIR]
    if os.name == "nt":
        dirs += [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    return [d for d in dirs if d.is_dir()]


def available(name: str) -> bool:
    """Perkiraan berdasarkan nama file (mis. 'Montserrat ExtraBold' ↔ Montserrat-ExtraBold.ttf)."""
    key = _norm(name)
    for d in _system_dirs():
        for f in d.iterdir():
            if f.suffix.lower() in (".ttf", ".otf") and _norm(f.stem).startswith(key):
                return True
    return False


def ensure(name: str) -> str | None:
    """Pastikan font tersedia. Kembalikan pesan peringatan bila tidak bisa dipastikan, else None."""
    if not name:
        return None
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    if available(name):
        return None
    url = CATALOG.get(name.strip().lower())
    if not url:
        return (f"Font subtitle '{name}' tidak ditemukan — subtitle akan memakai font cadangan. "
                f"Taruh file .ttf-nya di {FONT_DIR} atau pilih: {', '.join(n.title() for n in CATALOG)}.")
    try:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        (FONT_DIR / url.rsplit("/", 1)[-1]).write_bytes(r.content)
    except httpx.HTTPError as e:
        return f"Gagal mengunduh font '{name}' ({e}) — subtitle memakai font cadangan."
    return None
