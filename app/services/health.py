"""Halaman "Cek Kesiapan": satu tempat untuk melihat apa yang sudah siap & apa yang perlu dibereskan."""
import json
import platform
import shutil
import subprocess
import time
from importlib import metadata

from .. import config
from . import fonts


def _item(group: str, name: str, status: str, detail: str, fix: str = "") -> dict:
    """status: ok | warn | fail"""
    return {"group": group, "name": name, "status": status, "detail": detail, "fix": fix}


def _ver(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return ""


def _ffmpeg_version() -> str:
    try:
        out = subprocess.run([config.FFMPEG, "-version"], capture_output=True, text=True, timeout=15).stdout
        return out.splitlines()[0] if out else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def check(ping_llm: bool = False) -> list[dict]:
    out = []
    # --- sistem
    out.append(_item("Sistem", "Python", "ok", f"{platform.python_version()} ({platform.system()})"))
    v = _ffmpeg_version()
    out.append(_item("Sistem", "FFmpeg", "ok" if v else "fail", v or "tidak ditemukan",
                     "" if v else "winget install Gyan.FFmpeg, lalu restart aplikasi"))
    if v:
        from . import ffmpeg_utils as ff
        enc = ff.video_encoder()
        out.append(_item("Sistem", "Encoder video", "ok",
                         f"{enc} ({'GPU — render lebih cepat' if enc != 'libx264' else 'CPU'})"))
    deno = config.find_deno()
    out.append(_item("Sistem", "Deno (unduh YouTube)", "ok" if deno else "warn", deno or "belum terpasang",
                     "" if deno else "winget install DenoLand.Deno — wajib untuk Clipper dari link YouTube"))
    free_gb = shutil.disk_usage(config.DATA_DIR).free / 1e9
    out.append(_item("Sistem", "Ruang disk", "ok" if free_gb > 5 else "warn", f"{free_gb:.1f} GB kosong",
                     "" if free_gb > 5 else "Kosongkan disk: render & footage butuh beberapa GB"))
    from . import stt
    ok_local, why = stt.local_available(probe=False)  # tanpa impor → tidak memicu notifikasi Windows
    if ok_local:
        out.append(_item("Sistem", "Transkrip (Whisper)", "ok", "Whisper lokal siap"))
    elif config.GROQ_API_KEY:
        out.append(_item("Sistem", "Transkrip (Whisper)", "ok",
                         "Whisper lokal diblokir/tidak ada → memakai Whisper Groq (cloud, gratis)",
                         "Penyebab umum: Smart App Control memblokir DLL ctranslate2. Tidak perlu tindakan."))
    else:
        out.append(_item("Sistem", "Transkrip (Whisper)", "fail", f"Whisper lokal gagal: {why[:120]}",
                         "Isi GROQ_API_KEY agar transkrip memakai Whisper Groq"))
    try:  # OpenCV 5 (mis. ikut terpasang oleh paket lain) diblokir Smart App Control → reframe mati
        import cv2
        yn = hasattr(cv2, "FaceDetectorYN")
        model = (config.DATA_DIR / "models" / "face_detection_yunet_2023mar.onnx").is_file()
        out.append(_item("Sistem", "Auto-reframe (Clipper)", "ok" if yn else "warn",
                         f"OpenCV {cv2.__version__} · {'YuNet' if yn else 'Haar (lama)'}"
                         + ("" if model or not yn else " · model wajah diunduh otomatis saat pertama dipakai")))
    except Exception as e:
        out.append(_item("Sistem", "Auto-reframe (Clipper)", "warn", f"OpenCV tidak bisa dimuat: {str(e)[:100]}",
                         'python -m pip uninstall -y opencv-python && python -m pip install --force-reinstall '
                         '"opencv-python-headless>=4.8,<5" — jangan pasang paket yang menarik OpenCV 5'))
    for pkg in ("edge-tts", "yt-dlp"):
        ver = _ver(pkg)
        out.append(_item("Sistem", pkg, "ok" if ver else "fail", ver or "belum terpasang",
                         "" if ver else "python -m pip install -r requirements.txt"))

    # --- AI: rantai cadangan + jumlah key per layanan (tanpa membuka isi key)
    from . import keys as kp
    from . import llm
    st = kp.status()
    signup = {"groq": "console.groq.com/keys", "gemini": "aistudio.google.com/apikey",
              "cerebras": "cloud.cerebras.ai", "openrouter": "openrouter.ai/keys", "mistral": "console.mistral.ai"}
    model_of = {"gemini": config.GEMINI_MODEL, "groq": config.GROQ_MODEL, "cerebras": ",".join(config.CEREBRAS_MODELS[:1]),
                "openrouter": ",".join(config.OPENROUTER_MODELS[:1]), "mistral": ",".join(config.MISTRAL_MODELS[:1])}
    rantai = llm.chain()
    for prov in [*rantai, *[p for p in llm.CLOUD if p not in rantai]]:
        role = "utama" if rantai and prov == rantai[0] else ("cadangan" if prov in rantai else "belum diisi")
        if prov == "ollama":
            out.append(_item("AI", f"Ollama ({role})", "ok", f"lokal di {config.OLLAMA_URL}"))
            continue
        rows = st.get(prov, [])
        alive = [r for r in rows if not r["dead"]]
        cooling = [r for r in alive if r["cooling"]]
        if not rows:
            out.append(_item("AI", f"{llm.PROVIDER_LABEL[prov]} ({role})", "fail" if role == "utama" else "warn",
                             "API key kosong", f"Gratis di {signup[prov]} → isi {kp.ENV[prov]} di .env"))
            continue
        detail = f"{len(alive)}/{len(rows)} key aktif · model {model_of[prov]}"
        if cooling:
            detail += " · sedang pulih: " + ", ".join(f"key {r['n']}" for r in cooling)
        dead = [f"key {r['n']} ({r['tail']}) ditolak" for r in rows if r["dead"]]
        out.append(_item("AI", f"{llm.PROVIDER_LABEL[prov]} ({role})", "ok" if alive else "fail",
                         detail + (" · " + "; ".join(dead) if dead else ""),
                         "" if len(rows) > 1 else f"Tambah {kp.ENV[prov]}_2 dari akun lain → kuota berlipat & tidak macet"))
    n_cloud = sum(1 for p in llm.CLOUD if kp.has(p))
    out.append(_item("AI", "Cadangan AI", "ok" if n_cloud >= 2 else "warn",
                     f"{n_cloud} layanan AI cloud siap · rantai: " + " → ".join(llm.PROVIDER_LABEL[p] for p in rantai),
                     "" if n_cloud >= 2 else "Isi minimal 2 layanan (mis. Groq + Gemini/Cerebras) supaya tidak bergantung satu AI"))
    if ping_llm:
        t = time.time()
        try:
            llm.chat_json("Answer in JSON.", 'Return {"ok": true}', temperature=0)
            out.append(_item("AI", "Tes jawaban AI", "ok", f"menjawab dalam {time.time() - t:.1f} dtk"))
        except Exception as e:  # noqa: BLE001
            out.append(_item("AI", "Tes jawaban AI", "fail", str(e)[:300], "Cek API key / kuota di atas"))

    # --- aset & suara
    out.append(_item("Aset", "Pixabay", "ok" if config.PIXABAY_API_KEY else "fail",
                     "API key terisi" if config.PIXABAY_API_KEY else "API key kosong",
                     "" if config.PIXABAY_API_KEY else "Daftar gratis di pixabay.com/api/docs → isi PIXABAY_API_KEY"))
    out.append(_item("Aset", "Cerita AI (Pollinations)", "ok" if config.POLLINATIONS_KEY else "warn",
                     f"key terisi · model {config.STORY_IMAGE_MODEL}" if config.POLLINATIONS_KEY
                     else "tanpa key: karakter kurang konsisten & ±1 gambar/40 dtk",
                     "" if config.POLLINATIONS_KEY else "Daftar gratis di enter.pollinations.ai → isi POLLINATIONS_KEY di .env"))
    out.append(_item("Aset", "Pexels","ok" if config.PEXELS_API_KEY else "warn",
                     "API key terisi" if config.PEXELS_API_KEY else "kosong (opsional; key baru sedang ditutup Pexels)"))
    missing = [n.title() for n in fonts.CATALOG if not fonts.available(n)]
    out.append(_item("Aset", "Font subtitle", "ok" if not missing else "warn",
                     "semua font preset tersedia" if not missing else f"belum diunduh: {', '.join(missing)}",
                     "" if not missing else "Otomatis diunduh saat render pertama (butuh internet)"))

    # --- Google
    if not config.GOOGLE_CLIENT_SECRET.exists():
        out.append(_item("Google", "OAuth client", "warn", "credentials/client_secret.json belum ada",
                         "Opsional. Ikuti README bagian 'Setup Google' untuk Drive/Sheets/YouTube"))
    elif not config.GOOGLE_TOKEN.exists():
        out.append(_item("Google", "Login Google", "warn", "belum login", "python run.py --setup-google"))
    else:
        age = (time.time() - config.GOOGLE_TOKEN.stat().st_mtime) / 86400
        try:
            has_refresh = bool(json.loads(config.GOOGLE_TOKEN.read_text(encoding="utf-8")).get("refresh_token"))
        except (OSError, ValueError):
            has_refresh = False
        st = "ok" if has_refresh and age < 6 else "warn"
        out.append(_item("Google", "Login Google", st,
                         f"token diperbarui {age:.0f} hari lalu" + ("" if has_refresh else ", tanpa refresh token"),
                         "" if st == "ok" else "Mode Testing: login kedaluwarsa ±7 hari → python run.py --setup-google"))
    return out
