"""
State proyek + gerbang Human-in-the-Loop.

Sumber kebenaran saat bekerja = data/projects/<id>/project.json (cepat, offline).
Setiap "Setujui & Lanjut" -> disinkron ke Google Sheets (indeks/metadata) dan
project.json di-backup ke Google Drive (lihat services/sync.py).

FORMAT DATA KANONIK (dipakai lintas modul — jangan diubah sembarangan):
  Word      = {"text": str, "start": float detik, "end": float detik, "emphasis": bool}
  Scene     = {"id": "s1", "text": str (boleh berisi *penekanan*), "emotion": str,
               "mood": str, "visual_query": str, "sfx": str,
               "role": str opsional (hook|setup|eskalasi|twist|payoff|loop),
               "on_screen_text": str opsional (teks hook di layar, scene pertama)}
  Clip      = {"id": "c1", "start": float, "end": float, "title": str, "hook": str,
               "reason": str, "score": int}
  Semua path file di JSON = RELATIF terhadap folder proyek (portabel ke Drive).
"""
import json
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config

STAGES = ["research", "script", "assets", "audio", "subtitles", "render", "publish"]
STAGE_LABELS = {
    "research": "1. Ide & Riset",
    "script": "2. Kerangka & Naskah",
    "assets": "3. Aset Visual",
    "audio": "4. Voice Over & SFX",
    "subtitles": "5. Subtitle",
    "render": "6. Render Video",
    "publish": "7. Upload & Jadwal",
}
# Jalur Clipper memakai tahap internal yang sama, tetapi isinya beda → nama & penjelasan sendiri.
STAGE_LABELS_CLIPPER = {
    "research": "1. Video Sumber",
    "script": "2. Transkrip & Momen",
    "assets": "3. Potong & Bingkai",
    "audio": "4. Musik & SFX",
    "subtitles": "5. Subtitle",
    "render": "6. Render Klip",
    "publish": "7. Upload & Jadwal",
}
STAGE_HELP = {
    "scratch": {
        "research": "Cari ide dari tren YouTube, pilih satu angle.",
        "script": "AI menulis naskah per scene berdasar sumber fakta; cek fakta & edit.",
        "assets": "Pilih footage stok / foto / gambar AI untuk tiap scene.",
        "audio": "Buat voice over, atur ritme, kamus pelafalan, musik & SFX.",
        "subtitles": "Subtitle per kata dengan penekanan kata kunci.",
        "render": "Gabungkan visual + suara + subtitle jadi video final.",
        "publish": "Judul, deskripsi, tag, thumbnail, lalu upload / jadwalkan.",
    },
    "clipper": {
        "research": "Tempel link YouTube atau unggah video yang punya izin.",
        "script": "Transkrip per kata, lalu AI mencari momen viral tanpa memotong kalimat.",
        "assets": "Potong klip terpilih dan atur bingkai 9:16 (ikuti wajah / blur / crop).",
        "audio": "Audio asli dipakai; tambahkan musik latar (auto-ducking) & SFX bila perlu.",
        "subtitles": "Subtitle dari transkrip asli; rapikan kata yang salah dengar.",
        "render": "Gabungkan klip + subtitle + musik jadi video final.",
        "publish": "Judul, deskripsi (cantumkan kredit sumber), thumbnail, upload / jadwal.",
    },
}

VOICE_PRESETS = {
    # karakter suara -> parameter dasar Edge-TTS
    "naratif":   {"speed": 1.0,  "pitch_hz": 0,  "pause_ms": 350, "voice": "id-ID-ArdiNeural"},
    "antusias":  {"speed": 1.15, "pitch_hz": 6,  "pause_ms": 200, "voice": "id-ID-GadisNeural"},
    "edukatif":  {"speed": 0.95, "pitch_hz": 0,  "pause_ms": 400, "voice": "id-ID-GadisNeural"},
    "sinematik": {"speed": 0.88, "pitch_hz": -6, "pause_ms": 550, "voice": "id-ID-ArdiNeural"},
}

# emosi naskah -> modifikasi suara (Edge-TTS gratis tidak menerima SSML/style,
# jadi emosi diterjemahkan ke rate/pitch per kalimat)
EMOTION_VOICE = {
    "netral":   {"rate_delta": 0,   "pitch_delta": 0},
    "ceria":    {"rate_delta": 6,   "pitch_delta": 5},
    "tegang":   {"rate_delta": 8,   "pitch_delta": -2},
    "dramatis": {"rate_delta": -10, "pitch_delta": -4},
    "sedih":    {"rate_delta": -12, "pitch_delta": -6},
    "misteri":  {"rate_delta": -8,  "pitch_delta": -5},
    "heroik":   {"rate_delta": 2,   "pitch_delta": 3},
    "lucu":     {"rate_delta": 5,   "pitch_delta": 6},
}
MOODS = ["tegang", "ceria", "dramatis", "sedih", "misteri", "santai", "epik", "lucu"]

_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def project_dir(pid: str, create: bool = False) -> Path:
    """Folder proyek. Hanya dibuat saat create=True (menyimpan), supaya GET ke id
    sembarang tidak menghasilkan folder kosong."""
    if not re.fullmatch(r"[a-z0-9-]+", pid):
        raise ValueError("project id tidak valid")
    d = config.PROJECTS_DIR / pid
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def new_project(mode: str, fmt: str, title: str, keyword: str = "", language: str = "") -> dict:
    if mode not in ("scratch", "clipper"):
        raise ValueError("mode harus scratch|clipper")
    if fmt not in ("short", "long"):
        raise ValueError("format harus short|long")
    pid = time.strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6]
    preset = "antusias" if fmt == "short" else "naratif"
    p = {
        "id": pid,
        "title": title or keyword or "Proyek baru",
        "mode": mode,
        "format": fmt,
        "language": language or config.DEFAULT_LANGUAGE,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "current_stage": 0,
        "stage_status": {s: "pending" for s in STAGES},
        "voice": {"preset": preset, **VOICE_PRESETS[preset], "emphasis_boost": True},
        "research": {"keyword": keyword, "ideas": [], "selected": None, "source": None},
        "script": {"scenes": [], "transcript": None, "clips": [], "selected_clip": None},
        "assets": {"scenes": {}, "clip_path": None, "crop_mode": "blur" if fmt == "short" else "none"},
        "audio": {"pronunciation": {}, "vo_path": None, "words": [], "sentences": [],
                  "scene_timings": [], "music": {"enabled": mode == "scratch", "file": None,
                  "volume": 0.12, "mood": None}, "sfx": [], "duration": 0},
        "subtitles": {"words": [], "lines": [], "style": default_sub_style(fmt)},
        "render": {"output_path": None, "drive_file_id": None},
        "publish": {"title": "", "description": "", "tags": [], "privacy": "private",
                    "publish_at": "", "category_id": "22", "made_for_kids": False,
                    "video_id": None, "url": None, "status": None},
        "drive": {"folder_id": None, "files": {}},
        "log": [],
    }
    p["stage_status"]["research"] = "draft"
    save(p)
    return p


def default_sub_style(fmt: str) -> dict:
    short = fmt == "short"
    return {
        "preset": "pop" if short else "klasik",
        "font": "Montserrat ExtraBold" if short else "Arial",
        "size": 78 if short else 54,
        "primary": "#FFFFFF",
        "highlight": "#FFE600",      # warna kata yang sedang diucapkan (karaoke)
        "emphasis": "#FF3B6B",       # warna kata kunci (ditandai *...*)
        "emphasis_scale": 125,       # % ukuran saat kata kunci diucapkan
        "active_scale": 108 if short else 100,  # % ukuran kata aktif biasa
        "animation": "pop" if short else "none",  # pop = kata aktif "meletup" saat diucapkan
        "outline": 5 if short else 3,
        "position": "middle" if short else "bottom",
        "words_per_line": 3 if short else 7,
        "karaoke": True,
        "uppercase": short,
        "hook_text": True,           # teks hook besar di 2.5 dtk pertama
    }


# Preset gaya subtitle (ditimpa ke style saat dipilih di UI). Semua font OFL, diunduh otomatis saat render.
SUB_PRESETS = {
    "pop":     {"font": "Montserrat ExtraBold", "size": 78, "words_per_line": 3, "animation": "pop",
                "active_scale": 108, "highlight": "#FFE600", "emphasis": "#FF3B6B", "uppercase": True,
                "karaoke": True, "outline": 5},
    "tebal2":  {"font": "Anton", "size": 110, "words_per_line": 2, "animation": "pop", "active_scale": 112,
                "highlight": "#39FF14", "emphasis": "#FFE600", "uppercase": True, "karaoke": True, "outline": 6},
    "elegan":  {"font": "Poppins ExtraBold", "size": 70, "words_per_line": 4, "animation": "none",
                "active_scale": 100, "highlight": "#7CC6FF", "emphasis": "#FFB020", "uppercase": False,
                "karaoke": True, "outline": 4},
    "klasik":  {"font": "Arial", "size": 58, "words_per_line": 7, "animation": "none", "active_scale": 100,
                "highlight": "#FFFFFF", "emphasis": "#FFE600", "uppercase": False, "karaoke": False, "outline": 3},
}


def load(pid: str) -> dict:
    f = project_dir(pid) / "project.json"
    if not f.exists():
        raise FileNotFoundError(pid)
    with _lock:
        return json.loads(f.read_text(encoding="utf-8"))


def save(p: dict) -> dict:
    p["updated_at"] = now_iso()
    f = project_dir(p["id"], create=True) / "project.json"
    with _lock:
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(f)
    return p


def update(pid: str, fn) -> dict:
    """Read-modify-write atomik (dipakai job background)."""
    with _lock:
        p = load(pid)
        fn(p)
        return save(p)


def list_projects() -> list[dict]:
    out = []
    for f in sorted(config.PROJECTS_DIR.glob("*/project.json"), reverse=True):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: p[k] for k in ("id", "title", "mode", "format", "current_stage",
                                          "stage_status", "updated_at")})
        except Exception:
            continue
    return out


def log(p: dict, msg: str) -> None:
    p.setdefault("log", []).append(f"[{now_iso()}] {msg}")
    p["log"] = p["log"][-200:]


# ---------- Gerbang Human-in-the-Loop ----------

def save_stage(p: dict, stage: str, data: dict) -> dict:
    """Edit Manual: simpan data tahap. Tahap sesudahnya yang sudah disetujui ditandai 'stale'
    (perlu dicek ulang), datanya TIDAK dihapus."""
    idx = STAGES.index(stage)
    if idx > p["current_stage"]:
        raise PermissionError("Tahap ini belum terbuka. Setujui tahap sebelumnya dulu.")
    p[stage] = data
    touch_stage(p, stage)
    log(p, f"Edit manual: {stage}")
    return p


def touch_stage(p: dict, stage: str) -> None:
    """Dipanggil setiap kali data tahap berubah (edit manual ATAU hasil AI/job)."""
    idx = STAGES.index(stage)
    if p["stage_status"][stage] in ("pending", "approved", "stale"):
        p["stage_status"][stage] = "draft"
    for later in STAGES[idx + 1:]:
        if p["stage_status"][later] in ("approved", "draft"):
            p["stage_status"][later] = "stale"
    # Mundur ke tahap ini: tahap berikutnya harus disetujui ulang (data tetap ada)
    p["current_stage"] = idx


def approve(p: dict, stage: str) -> dict:
    idx = STAGES.index(stage)
    if idx > p["current_stage"]:
        raise PermissionError("Tahap ini belum terbuka.")
    _validate(p, stage)
    p["stage_status"][stage] = "approved"
    if idx + 1 < len(STAGES):
        nxt = STAGES[idx + 1]
        p["current_stage"] = max(p["current_stage"], idx + 1)
        if p["stage_status"][nxt] == "pending":
            p["stage_status"][nxt] = "draft"
    log(p, f"Disetujui: {stage}")
    return p


def _validate(p: dict, stage: str) -> None:
    """Validasi minimum sebelum boleh lanjut, supaya data antar-tahap kompatibel."""
    m = p["mode"]
    err = None
    if stage == "research":
        if m == "scratch" and p["research"].get("selected") is None:
            err = "Pilih satu ide dulu."
        if m == "clipper" and not (p["research"].get("source") or {}).get("path"):
            err = "Video sumber belum diunduh/diunggah."
    elif stage == "script":
        if m == "scratch" and not p["script"].get("scenes"):
            err = "Naskah masih kosong."
        if m == "clipper" and p["script"].get("selected_clip") is None:
            err = "Pilih satu klip dulu."
    elif stage == "assets":
        if m == "scratch":
            missing = [s["id"] for s in p["script"]["scenes"]
                       if not (p["assets"]["scenes"].get(s["id"]) or {}).get("selected")]
            if missing:
                err = f"Scene tanpa visual: {', '.join(missing)}"
        elif not p["assets"].get("clip_path"):
            err = "Klip belum dipotong."
    elif stage == "audio":
        if m == "scratch" and not p["audio"].get("vo_path"):
            err = "Voice over belum dirender."
    elif stage == "subtitles":
        if not p["subtitles"].get("words"):
            err = "Subtitle masih kosong."
    elif stage == "render":
        if not p["render"].get("output_path"):
            err = "Video belum dirender."
    if err:
        raise ValueError(err)
