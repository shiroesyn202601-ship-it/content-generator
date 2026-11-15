"""Konfigurasi terpusat. Semua nilai dibaca dari .env (lihat .env.example)."""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True: saat auto-reload, proses server baru mewarisi env lama dari proses induk;
# tanpa override, perubahan .env tidak akan pernah terbaca sampai restart manual.
load_dotenv(ROOT / ".env", override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _first_key(base: str) -> str:
    """Key pertama dari kolam (KEY=a,b atau KEY_2=…) — hanya untuk cek "sudah diisi?". Pemakaian nyata
    lewat services/keys.py yang merotasi semua key."""
    for name in [base] + [f"{base}_{i}" for i in range(2, 21)]:
        for k in os.getenv(name, "").replace(";", ",").split(","):
            if k.strip():
                return k.strip()
    return ""


def _list(key: str, default: str) -> list[str]:
    return [m.strip() for m in _env(key, default).split(",") if m.strip()]


DATA_DIR = ROOT / "data"
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"          # cache library audio dari Drive
for _d in (PROJECTS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

LLM_PROVIDER = _env("LLM_PROVIDER", "gemini").lower()
# Provider cadangan bila provider utama gagal (dilewati otomatis kalau API key-nya kosong).
# Layanan cloud lain yang key-nya terisi otomatis ikut di belakang rantai ini (lihat llm.chain).
LLM_FALLBACK_PROVIDERS = [p.lower() for p in _list("LLM_FALLBACK_PROVIDERS", "groq,gemini,cerebras,openrouter,mistral")]
GEMINI_API_KEY = _first_key("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.8-flash")
# Dicoba berurutan bila model utama sibuk (503/429) atau sudah dipensiunkan (404).
GEMINI_FALLBACK_MODELS = [m.strip() for m in _env("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-flash-latest")
                          .split(",") if m.strip()]
GROQ_API_KEY = _first_key("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODELS = [m.strip() for m in _env("GROQ_FALLBACK_MODELS", "qwen/qwen3.8-27b,openai/gpt-oss-20b")
                        .split(",") if m.strip()]
# Cadangan gratis tambahan (kompatibel OpenAI). Kosongkan key = tidak dipakai. Model dicoba berurutan.
CEREBRAS_MODELS = _list("CEREBRAS_MODELS", "gpt-oss-120b,llama-3.3-70b,qwen-3-32b")
OPENROUTER_MODELS = _list("OPENROUTER_MODELS", "openrouter/free,meta-llama/llama-3.3-70b-instruct:free")
MISTRAL_MODELS = _list("MISTRAL_MODELS", "mistral-small-latest,open-mistral-nemo")
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

def _find_bin(name: str, configured: str) -> str:
    """Cari ffmpeg/ffprobe: nilai .env → PATH → lokasi instal umum di Windows (winget, C:\\ffmpeg).
    Terminal yang dibuka sebelum ffmpeg di-install tidak punya PATH terbaru, jadi kita cari sendiri."""
    if configured and (Path(configured).is_file() or shutil.which(configured)):
        return shutil.which(configured) or configured
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if os.name == "nt" else "")
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [local / "Microsoft/WinGet/Links" / exe, Path("C:/ffmpeg/bin") / exe,
                  Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ffmpeg/bin" / exe]
    candidates += sorted((local / "Microsoft/WinGet/Packages").glob(f"Gyan.FFmpeg*/**/bin/{exe}"))
    for c in candidates:
        if c.is_file():
            return str(c)
    return configured or name


# Mode tablet/LAN (HOST=0.0.0.0): perangkat lain wajib PIN. Kosong + HOST non-lokal → PIN acak tiap start.
APP_PIN = _env("APP_PIN")
# Mode "Cerita AI": key gratis dari https://enter.pollinations.ai → gambar scene memakai referensi karakter
POLLINATIONS_KEY = _first_key("POLLINATIONS_KEY")
STORY_IMAGE_MODEL = _env("STORY_IMAGE_MODEL", "black-forest-labs/flux.2-klein-4b")
VIDEO_ENCODER = _env("VIDEO_ENCODER", "auto")
# Transkrip: auto = Whisper lokal, bila diblokir/tidak ada → Whisper Groq | local | groq
STT_PROVIDER = _env("STT_PROVIDER", "auto")  # auto | local | groq (groq = tidak pernah memuat Whisper lokal)
FFMPEG = _find_bin("ffmpeg", _env("FFMPEG_BIN", "ffmpeg"))
FFPROBE = _find_bin("ffprobe", _env("FFPROBE_BIN", "ffprobe"))


def find_deno() -> str | None:
    """yt-dlp butuh runtime JavaScript (Deno) untuk YouTube sejak Nov 2025.
    Install: winget install DenoLand.Deno. Dicari juga di lokasi winget bila PATH terminal belum diperbarui."""
    found = shutil.which("deno")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    exe = "deno.exe" if os.name == "nt" else "deno"
    cands = [local / "Microsoft/WinGet/Links" / exe, Path.home() / ".deno/bin" / exe]
    cands += sorted((local / "Microsoft/WinGet/Packages").glob(f"DenoLand.Deno*/{exe}"))
    return next((str(c) for c in cands if c.is_file()), None)
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
