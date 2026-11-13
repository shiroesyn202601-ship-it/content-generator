"""Konfigurasi terpusat. Semua nilai dibaca dari .env (lihat .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


DATA_DIR = ROOT / "data"
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"          # cache library audio dari Drive
for _d in (PROJECTS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

LLM_PROVIDER = _env("LLM_PROVIDER", "gemini").lower()
# Provider cadangan bila provider utama gagal (dilewati otomatis kalau API key-nya kosong).
LLM_FALLBACK_PROVIDERS = [p.strip().lower() for p in _env("LLM_FALLBACK_PROVIDERS", "groq").split(",") if p.strip()]
GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.8-flash")
# Dicoba berurutan bila model utama sibuk (503/429) atau sudah dipensiunkan (404).
GEMINI_FALLBACK_MODELS = [m.strip() for m in _env("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-flash-latest")
                          .split(",") if m.strip()]
GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5:7b")

PEXELS_API_KEY = _env("PEXELS_API_KEY")
PIXABAY_API_KEY = _env("PIXABAY_API_KEY")

WHISPER_MODEL = _env("WHISPER_MODEL", "small")
WHISPER_DEVICE = _env("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = _env("WHISPER_COMPUTE", "int8")

GOOGLE_CLIENT_SECRET = ROOT / _env("GOOGLE_CLIENT_SECRET", "credentials/client_secret.json")
GOOGLE_TOKEN = ROOT / _env("GOOGLE_TOKEN", "credentials/token.json")
SHEET_ID = _env("SHEET_ID")
DRIVE_ROOT_FOLDER_ID = _env("DRIVE_ROOT_FOLDER_ID")
DRIVE_MUSIC_FOLDER_ID = _env("DRIVE_MUSIC_FOLDER_ID")
DRIVE_SFX_FOLDER_ID = _env("DRIVE_SFX_FOLDER_ID")

FFMPEG = _env("FFMPEG_BIN", "ffmpeg")
FFPROBE = _env("FFPROBE_BIN", "ffprobe")
DEFAULT_LANGUAGE = _env("DEFAULT_LANGUAGE", "id")
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8000"))
# Origin yang boleh memanggil API (UI versi GitHub Pages). Pisahkan dengan koma.
CORS_ORIGINS = [o.strip() for o in _env("CORS_ORIGINS", "https://shiroesyn202601-ship-it.github.io").split(",") if o.strip()]

# Resolusi output per format
RESOLUTIONS = {"short": (1080, 1920), "long": (1920, 1080)}
FPS = 30


def google_enabled() -> bool:
    """Integrasi Google aktif kalau token OAuth sudah ada (hasil `python run.py --setup-google`)."""
    return GOOGLE_TOKEN.exists()
