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

STAGES = ["research", "script", "assets", "audio", "subtitles", "render", "thumbnail", "publish"]
STAGE_LABELS = {
    "research": "1. Ide & Riset",
    "script": "2. Kerangka & Naskah",
    "assets": "3. Aset Visual",
    "audio": "4. Voice Over & SFX",
    "subtitles": "5. Subtitle",
    "render": "6. Render Video",
    "thumbnail": "7. Thumbnail",
    "publish": "Upload & Jadwal",
}
# Jalur Clipper memakai tahap internal yang sama, tetapi isinya beda → nama & penjelasan sendiri.
# Tahap "assets" (potong klip) digabung ke tahap "script" di UI Clipper (lihat CLIPPER_MERGED).
STAGE_LABELS_CLIPPER = {
    "research": "1. Video Sumber",
    "script": "2. Momen & Potong",
    "assets": "2. Potong & Bingkai",
    "audio": "3. Musik & SFX",
    "subtitles": "4. Subtitle",
    "render": "5. Render Klip",
    "thumbnail": "6. Thumbnail",
    "publish": "Upload & Jadwal",
}
CLIPPER_MERGED = {"assets": "script"}  # tahap Clipper yang tidak tampil sendiri → ikut tahap ini
STAGE_HELP = {
    "scratch": {
        "research": "Cari ide dari tren YouTube, pilih satu angle.",
        "script": "AI menulis naskah per scene berdasar sumber fakta; cek fakta & edit.",
        "assets": "Pilih footage stok / foto / gambar AI untuk tiap scene.",
        "audio": "Buat voice over, atur ritme, kamus pelafalan, musik & SFX.",
        "subtitles": "Subtitle per kata: pilih template atau atur gaya, warna, animasi & posisi sendiri.",
        "render": "Gabungkan visual + suara + subtitle jadi video final.",
        "thumbnail": "Pilih frame, tulis teks besar, atur gaya — thumbnail siap dipasang saat upload.",
        "publish": "Judul, deskripsi, tag, lalu upload / jadwalkan.",
    },
    "clipper": {
        "research": "Tempel link YouTube atau unggah video yang punya izin.",
        "script": "Transkrip, AI mencari momen viral, lalu potong satu klip — atau produksi banyak klip sekaligus.",
        "assets": "Potong klip terpilih dan atur bingkai 9:16 (ikuti wajah / blur / crop).",
        "audio": "Audio asli dipakai; tambahkan musik latar (auto-ducking) & SFX bila perlu.",
        "subtitles": "Subtitle dari transkrip asli: pilih template atau atur gaya, warna, animasi & posisi.",
        "render": "Gabungkan klip + subtitle + musik jadi video final.",
        "thumbnail": "Pilih frame, tulis teks besar, atur gaya — thumbnail siap dipasang saat upload.",
        "publish": "Judul, deskripsi (cantumkan kredit sumber), upload / jadwal.",
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
        "assets": {"scenes": {}, "clip_path": None, "crop_mode": ("face" if mode == "clipper" else "blur") if fmt == "short" else "none"},
        "audio": {"pronunciation": {}, "vo_path": None, "words": [], "sentences": [],
                  "scene_timings": [], "music": {"enabled": mode == "scratch", "file": None,
                  "volume": 0.12, "mood": None}, "sfx": [], "duration": 0},
        "subtitles": {"words": [], "lines": [], "style": default_sub_style(fmt)},
        "render": {"output_path": None, "drive_file_id": None},
        "thumbnail": default_thumbnail(),
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
    t = dict(SUB_TEMPLATES["pop" if fmt == "short" else "klasik"]["style"])
    t["template"] = "pop" if fmt == "short" else "klasik"
    return t


# Template gaya subtitle ala CapCut (riset 15 Nov 2026: Submagic, OpenClip, CapCut). Field yang tidak disebut
# memakai subtitles.STYLE_DEFAULTS. Semua font OFL/Apache (bebas komersial), diunduh otomatis.
_T = lambda label, desc, **st: {"label": label, "desc": desc, "style": st}  # noqa: E731
SUB_TEMPLATES = {
    "pop": _T("Pop Kuning", "3 kata, kata aktif kuning & meletup — aman untuk semua Shorts",
              font="Montserrat ExtraBold", size=80, case="upper", highlight="#FFE600", emphasis="#FF3B6B",
              highlight_mode="color", highlight_scale=110, stroke_w=6, words_per_line=3, anim_in="pop",
              active_anim="pop", pos_y=60),
    "tegas": _T("Tegas (ala Hormozi)", "2 kata, huruf hitam tebal, kata kunci kuning, stroke tebal",
                font="Montserrat Black", size=110, case="upper", highlight="#F7C204", emphasis="#F7C204",
                highlight_mode="color", highlight_scale=115, stroke_w=8, shadow_alpha=60, shadow_y=4,
                words_per_line=2, anim_in="pop", active_anim="pop", pos_y=60),
    "tegas_hijau": _T("Tegas Hijau", "Seperti Tegas, highlight hijau neon",
                      font="Montserrat Black", size=110, case="upper", highlight="#02FB23", emphasis="#02FB23",
                      highlight_mode="color", highlight_scale=115, stroke_w=8, shadow_alpha=60, shadow_y=4,
                      words_per_line=2, anim_in="pop", active_anim="pop", pos_y=60),
    "komik": _T("Komik (ala MrBeast)", "Font komik, stroke sangat tebal, bayangan keras, sedikit miring",
                font="Bangers", size=120, case="upper", highlight="#FFE600", emphasis="#FF3B3B",
                highlight_mode="color", highlight_scale=118, stroke_w=10, shadow_alpha=100, shadow_x=4,
                shadow_y=7, words_per_line=2, anim_in="bounce", active_anim="bounce", rotation=-3, pos_y=58),
    "karaoke": _T("Karaoke", "Satu kalimat, warna menyapu kata demi kata",
                  font="Poppins Bold", size=78, case="none", highlight="#3CFF6B", highlight_mode="karaoke",
                  stroke_w=5, words_per_line=5, max_lines=2, anim_in="fade", active_anim="none", pos_y=62),
    "minimalis": _T("Minimalis (ala Iman)", "Huruf kecil, tanpa stroke, bayangan halus — terkesan premium",
                    font="Poppins SemiBold", size=72, case="lower", highlight="#FFFFFF", highlight_scale=104,
                    highlight_mode="color", stroke_w=0, shadow_alpha=70, shadow_y=2, blur=2,
                    words_per_line=4, anim_in="fade", active_anim="none", pos_y=62),
    "pill": _T("Pill Ungu", "Kata aktif diberi latar pill berwarna",
               font="Montserrat ExtraBold", size=88, case="upper", highlight_mode="pill", highlight_bg="#7B2FF7",
               highlight_text="#FFFFFF", stroke_w=4, box_pad=14, words_per_line=3, anim_in="pop",
               active_anim="pop", pos_y=60),
    "kotak_merah": _T("Kotak Merah", "Kata aktif di kotak merah tegas",
                      font="Anton", size=100, case="upper", highlight_mode="box", highlight_bg="#E50914",
                      highlight_text="#FFFFFF", stroke_w=5, box_pad=12, words_per_line=3, anim_in="pop",
                      active_anim="none", pos_y=60),
    "tiktok": _T("Kotak Putih (TikTok)", "Teks hitam di kotak putih per baris",
                 font="Poppins Bold", size=66, case="none", primary="#111111", highlight="#111111",
                 highlight_mode="none", box="box", box_color="#FFFFFF", box_alpha=100, box_pad=14, stroke_w=0,
                 shadow_alpha=0, words_per_line=5, anim_in="slide_up", active_anim="none", pos_y=62),
    "neon": _T("Neon Glow", "Putih dengan cahaya biru, tanpa stroke keras",
               font="Poppins Bold", size=88, case="upper", highlight="#00E5FF", highlight_mode="color",
               glow="#00E5FF", glow_w=10, stroke_w=0, shadow_alpha=0, words_per_line=3, anim_in="fade",
               active_anim="pop", pos_y=60),
    "sinematik": _T("Sinematik", "Kata abu-abu, kata aktif putih tebal, latar gelap transparan",
                    font="Poppins SemiBold", size=66, case="none", primary="#A8A8A8", highlight="#FFFFFF",
                    highlight_scale=110, highlight_mode="color", box="box", box_color="#000000", box_alpha=45,
                    box_pad=16, stroke_w=0, shadow_alpha=0, words_per_line=4, anim_in="fade", active_anim="none",
                    pos_y=62),
    "ketik": _T("Mesin Ketik", "Kata muncul satu per satu seperti diketik",
                font="Space Mono Bold", size=64, case="none", highlight="#FFE600", highlight_mode="color",
                highlight_scale=100, stroke_w=4, words_per_line=6, max_lines=2, anim_in="typewriter",
                active_anim="none", pos_y=62),
    "bebas": _T("Judul Besar", "Bebas Neue raksasa 1 kata — untuk video cepat & energik",
                font="Bebas Neue", size=150, case="upper", highlight="#FFFFFF", emphasis="#FFE600",
                highlight_mode="color", highlight_scale=100, stroke_w=7, words_per_line=1, anim_in="pop",
                active_anim="none", pos_y=55),
    "klasik": _T("Klasik (video panjang)", "Satu baris bawah, tanpa animasi — nyaman untuk video 16:9",
                 font="Poppins SemiBold", size=56, case="none", highlight="#FFFFFF", highlight_mode="none",
                 stroke_w=3, shadow_alpha=60, shadow_y=2, words_per_line=7, max_lines=2, anim_in="fade",
                 active_anim="none", pos_y=88),
}
SUB_PRESETS = {k: v["style"] for k, v in SUB_TEMPLATES.items()}  # kompatibilitas lama
USER_TEMPLATES_FILE = config.DATA_DIR / "sub_templates.json"


def user_templates() -> dict:
    try:
        return json.loads(USER_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_user_template(name: str, style: dict) -> dict:
    with _lock:
        t = user_templates()
        key = "u_" + "".join(c for c in name.lower() if c.isalnum())[:30]
        if key == "u_":
            raise ValueError("Nama template kosong.")
        t[key] = {"label": name.strip()[:40], "desc": "Template buatan sendiri",
                  "style": {k: v for k, v in style.items() if k != "template"}}
        USER_TEMPLATES_FILE.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        return t


def delete_user_template(key: str) -> dict:
    with _lock:
        t = user_templates()
        t.pop(key, None)
        USER_TEMPLATES_FILE.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
        return t


def default_thumbnail() -> dict:
    return {"frames": [], "frame": None, "thumbs": [], "selected": 0, "design": {}, "uploaded": None}


def _migrate(p: dict) -> dict:
    """Proyek lama (sebelum ada tahap Thumbnail): sisipkan tahap & pindahkan data thumbnail dari publish."""
    st = p.get("stage_status") or {}
    if "thumbnail" not in st:
        old_pub = STAGES.index("thumbnail")  # dulu indeks publish
        if p.get("current_stage", 0) >= old_pub:
            p["current_stage"] += 1
        st["thumbnail"] = "approved" if st.get("render") == "approved" else "pending"
        p["stage_status"] = {k: st.get(k, "pending") for k in STAGES}
    if "thumbnail" not in p:
        pub = p.get("publish") or {}
        p["thumbnail"] = {**default_thumbnail(), "thumbs": pub.get("thumbs") or [],
                          "selected": pub.get("thumb_selected") or 0, "uploaded": pub.get("thumb_uploaded"),
                          "design": {"text": pub["thumb_text"]} if pub.get("thumb_text") else {}}
    return p


def load(pid: str) -> dict:
    f = project_dir(pid) / "project.json"
    if not f.exists():
        raise FileNotFoundError(pid)
    with _lock:
        return _migrate(json.loads(f.read_text(encoding="utf-8")))


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
            p = _migrate(json.loads(f.read_text(encoding="utf-8")))
            item = {k: p[k] for k in ("id", "title", "mode", "format", "current_stage",
                                      "stage_status", "updated_at")}
            # Ringkasan untuk tab "Upload & Jadwal" (dipakai bersama jalur Dari Nol & Clipper).
            pub, rnd = p.get("publish") or {}, p.get("render") or {}
            item["rendered"] = bool(rnd.get("output_path"))
            item["publish"] = {k: pub.get(k) for k in ("title", "publish_at", "privacy", "video_id", "url", "status")}
            out.append(item)
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
        else:
            clips, sel = p["script"].get("clips") or [], p["script"].get("selected_clip")
            rng = p["assets"].get("clip_range")
            if sel is not None and sel < len(clips) and rng and (
                    abs(rng[0] - clips[sel]["start"]) > 0.05 or abs(rng[1] - clips[sel]["end"]) > 0.05):
                err = "Klip terpilih berubah sejak dipotong — klik Potong lagi."
    elif stage == "audio":
        if m == "scratch" and not p["audio"].get("vo_path"):
            err = "Voice over belum dirender."
    elif stage == "subtitles":
        if not p["subtitles"].get("words"):
            err = "Subtitle masih kosong."
    elif stage == "render":
        if not p["render"].get("output_path"):
            err = "Video belum dirender."
    elif stage == "thumbnail":
        if not (p.get("thumbnail") or {}).get("thumbs"):
            err = "Buat thumbnail dulu (atau unggah gambar sendiri)."
    if err:
        raise ValueError(err)
