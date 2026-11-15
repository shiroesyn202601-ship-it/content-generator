"""Speech-to-text dengan timestamp per kata (Clipper & penyelarasan Gemini TTS).

Urutan: faster-whisper lokal → Whisper di Groq (gratis, whisper-large-v3-turbo).
Catatan Windows: Smart App Control dapat memblokir DLL ctranslate2 (mesin faster-whisper) karena tidak
bertanda tangan → impor gagal "Application Control policy has blocked this file". Dalam kasus itu
Groq otomatis dipakai. STT_PROVIDER di .env: auto (default) | local | groq."""
import math
import time
import tempfile
from pathlib import Path

import httpx

from .. import config
from . import ffmpeg_utils as ff

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = "whisper-large-v3-turbo"
CHUNK_SEC = 30 * 60          # potongan audio untuk Groq (batas file gratis 25 MB)
_local_error: str | None = None
# Hasil "diblokir" disimpan permanen: setiap percobaan memuat DLL ctranslate2 yang diblokir Smart App Control
# memunculkan notifikasi Windows "Part of this app has been blocked". Server sering restart (auto-reload),
# jadi tanpa catatan ini notifikasi muncul berulang. Hapus file ini untuk mencoba Whisper lokal lagi.
BLOCK_FILE = config.DATA_DIR / "stt_local_blocked.json"


def local_blocked() -> str:
    """Pesan error yang tercatat bila Whisper lokal pernah gagal dimuat ("" = belum pernah / tidak diblokir)."""
    try:
        import json
        return json.loads(BLOCK_FILE.read_text(encoding="utf-8")).get("error", "diblokir")
    except (OSError, ValueError):
        return ""


def local_available(probe: bool = True) -> tuple[bool, str]:
    """probe=False: jangan pernah mencoba impor (mis. Cek Kesiapan) — hanya pakai status yang tercatat."""
    global _local_error
    if _local_error is None:
        if (config.STT_PROVIDER or "auto").lower() == "groq":
            return False, "STT_PROVIDER=groq (Whisper lokal tidak dipakai)"
        blocked = local_blocked()
        if blocked and (config.STT_PROVIDER or "auto").lower() != "local":
            _local_error = blocked
        elif not probe:
            return False, "belum dicek"
        else:
            try:
                import ctranslate2  # noqa: F401
                import faster_whisper  # noqa: F401
                _local_error = ""
            except Exception as e:  # noqa: BLE001 — termasuk DLL diblokir kebijakan Windows
                _local_error = str(e) or type(e).__name__
                try:
                    import json
                    import time
                    BLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
                    BLOCK_FILE.write_text(json.dumps({"error": _local_error[:500], "at": time.strftime("%Y-%m-%d %H:%M")}),
                                          encoding="utf-8")
                except OSError:
                    pass
    return (not _local_error, _local_error)


def transcribe(path: Path, language: str | None, progress=lambda m: None, total: float = 0, hint: str = "") -> dict:
    """{"language", "segments": [{start, end, text, words: [{text, start, end}]}]}"""
    import time as _t

    from . import usage
    t0 = _t.time()
    res = _transcribe(path, language, progress, total, hint)
    eng = (res or {}).get("engine") or ""
    usage.ai("Transkrip", {"groq": "Groq Whisper (cloud)", "local": "faster-whisper (lokal)"}.get(eng, eng or "Whisper"),
             (res or {}).get("model") or "", _t.time() - t0, "transkrip suara")
    return res


def _transcribe(path: Path, language: str | None, progress, total: float, hint: str) -> dict:
    pref = (config.STT_PROVIDER or "auto").lower()
    ok, why = local_available() if pref != "groq" else (False, "")
    if pref == "local" or (pref == "auto" and ok):
        if not ok:
            raise RuntimeError(f"Whisper lokal tidak bisa dimuat: {why}")
        from .clipper import transcribe_local
        return transcribe_local(path, language, progress, total)
    if not config.GROQ_API_KEY:
        raise RuntimeError("Whisper lokal tidak tersedia" + (f" ({why[:120]})" if why else "")
                           + " dan GROQ_API_KEY kosong — isi GROQ_API_KEY untuk transkrip via Groq (gratis).")
    if why and pref == "auto":
        progress("Whisper lokal diblokir/tidak tersedia di komputer ini — memakai Whisper Groq (cloud, gratis)…")
    return transcribe_groq(path, language, progress, hint)


def _groq_stt(part: Path, data: dict, progress) -> dict:
    """Satu potongan audio ke Whisper Groq, berputar di semua GROQ_API_KEY (batas audio/jam per akun)."""
    from . import keys
    from .llm import _retry_after
    audio, sleeps = part.read_bytes(), 0
    while True:
        key = keys.take("groq", GROQ_STT_MODEL)
        if key is None:
            w = keys.wait_time("groq", GROQ_STT_MODEL)
            if w == float("inf"):
                raise RuntimeError("Semua GROQ_API_KEY ditolak — cek key di console.groq.com/keys.")
            if w > 120 or sleeps >= 3:
                raise RuntimeError(f"Whisper Groq: batas audio semua key ({len(keys.pool('groq'))}) tercapai, pulih "
                                   f"±{w / 60:.0f} mnt. Tambah GROQ_API_KEY_2 dari akun lain agar tidak menunggu.")
            sleeps += 1
            progress(f"Whisper Groq: semua key kena batas — menunggu {w:.0f} dtk…")
            time.sleep(w + 1)
            continue
        r = httpx.post(GROQ_STT_URL, headers={"Authorization": f"Bearer {key}"},
                       files={"file": (part.name, audio, "audio/mpeg")}, data=data,
                       timeout=httpx.Timeout(600, connect=20))
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            keys.cool("groq", key, GROQ_STT_MODEL, max(_retry_after(r), 5.0))
            if len(keys.pool("groq")) > 1:
                progress(f"Whisper Groq ({keys.label('groq', key)}) kena batas → pindah ke key lain…")
            continue
        if r.status_code in (401, 403):
            keys.kill("groq", key, f"{r.status_code}")
            continue
        raise RuntimeError(f"Whisper Groq gagal ({r.status_code}): {r.text[:300]}")


def _hallucination(seg: dict) -> bool:
    """Whisper suka "mengarang" teks di musik/hening (mis. "Terima kasih telah menonton").
    Groq verbose_json membawa no_speech_prob / avg_logprob / compression_ratio per segmen."""
    nsp, lp, cr = seg.get("no_speech_prob", 0) or 0, seg.get("avg_logprob", 0) or 0, seg.get("compression_ratio", 0) or 0
    return (nsp > 0.6 and lp < -0.8) or cr > 2.4


def _assign_words(segs: list[dict], words: list[dict]) -> None:
    """Tiap kata masuk TEPAT satu segmen (dulu kata di perbatasan bisa masuk dua segmen → subtitle dobel)."""
    j = 0
    for i, s in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else float("inf")
        s["words"] = []
        while j < len(words) and words[j]["start"] < nxt - 0.02:
            if words[j]["start"] >= s["start"] - 0.5:
                s["words"].append(words[j])
            j += 1


def transcribe_groq(path: Path, language: str | None, progress=lambda m: None, hint: str = "") -> dict:
    """Whisper Groq per potongan 30 menit. Tiap potongan yang selesai disimpan (source/.stt/) → bila kuota
    habis di tengah video panjang, mengulang hanya melanjutkan potongan yang belum.
    hint: judul/nama channel → ejaan nama orang, merek & istilah lebih tepat."""
    import json
    dur = ff.duration(path)
    n = max(1, math.ceil(dur / CHUNK_SEC))
    segments: list[dict] = []
    lang_out = language
    cache_dir = path.parent / ".stt"
    sig = f"{path.stem}_{path.stat().st_size}_{GROQ_STT_MODEL}_{language or 'auto'}"
    dropped = 0
    with tempfile.TemporaryDirectory() as td:
        for k in range(n):
            start = k * CHUNK_SEC
            cached = cache_dir / f"{sig}_{k}.json"
            j = None
            if cached.is_file():
                try:
                    j = json.loads(cached.read_text(encoding="utf-8"))
                    progress(f"Bagian {k + 1}/{n} sudah ditranskrip sebelumnya — dilewati")
                except ValueError:
                    j = None
            if j is None:
                part = Path(td) / f"part{k}.mp3"
                progress(f"Menyiapkan audio untuk Whisper Groq ({k + 1}/{n})…")
                # mono 16 kHz 48 kbps: ±0.36 MB/menit → 30 menit ±11 MB (aman di bawah batas 25 MB)
                ff.run(["-ss", f"{start:.2f}", "-t", f"{CHUNK_SEC}", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                        "-b:a", "48k", str(part)])
                progress(f"Whisper Groq mentranskrip bagian {k + 1}/{n}…")
                data = {"model": GROQ_STT_MODEL, "response_format": "verbose_json",
                        "timestamp_granularities[]": ["word", "segment"], "temperature": "0"}
                if language:
                    data["language"] = language
                if hint.strip():
                    data["prompt"] = hint.strip()[:400]
                j = _groq_stt(part, data, progress)
                cache_dir.mkdir(exist_ok=True)
                cached.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
            lang_out = lang_out or j.get("language")
            words = [{"text": w["word"].strip(), "start": round(w["start"] + start, 3), "end": round(w["end"] + start, 3)}
                     for w in j.get("words", []) if w.get("word", "").strip()]
            segs = []
            for s in j.get("segments", []):
                if _hallucination(s):
                    dropped += 1
                    continue
                segs.append({"start": round(s["start"] + start, 3), "end": round(s["end"] + start, 3),
                             "text": s["text"].strip()})
            _assign_words(segs, words)
            segments += [s for s in segs if s["words"] or s["text"]]
            progress(f"Transkrip {min(100, (start + CHUNK_SEC) / max(dur, 1) * 100):.0f}% ({len(segments)} segmen)")
    if dropped:
        progress(f"{dropped} segmen 'halusinasi' Whisper (musik/hening) dibuang")
    return {"language": lang_out, "segments": segments, "engine": "groq", "model": GROQ_STT_MODEL}
