"""Speech-to-text dengan timestamp per kata (Clipper & penyelarasan Gemini TTS).

Urutan: faster-whisper lokal → Whisper di Groq (gratis, whisper-large-v3-turbo).
Catatan Windows: Smart App Control dapat memblokir DLL ctranslate2 (mesin faster-whisper) karena tidak
bertanda tangan → impor gagal "Application Control policy has blocked this file". Dalam kasus itu
Groq otomatis dipakai. STT_PROVIDER di .env: auto (default) | local | groq."""
import math
import tempfile
from pathlib import Path

import httpx

from .. import config
from . import ffmpeg_utils as ff

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = "whisper-large-v3-turbo"
CHUNK_SEC = 30 * 60          # potongan audio untuk Groq (batas file gratis 25 MB)
_local_error: str | None = None


def local_available() -> tuple[bool, str]:
    global _local_error
    if _local_error is None:
        try:
            import ctranslate2  # noqa: F401
            import faster_whisper  # noqa: F401
            _local_error = ""
        except Exception as e:  # noqa: BLE001 — termasuk DLL diblokir kebijakan Windows
            _local_error = str(e)
    return (not _local_error, _local_error)


def transcribe(path: Path, language: str | None, progress=lambda m: None, total: float = 0) -> dict:
    """{"language", "segments": [{start, end, text, words: [{text, start, end}]}]}"""
    pref = (config.STT_PROVIDER or "auto").lower()
    ok, why = local_available()
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
    return transcribe_groq(path, language, progress)


def transcribe_groq(path: Path, language: str | None, progress=lambda m: None) -> dict:
    dur = ff.duration(path)
    n = max(1, math.ceil(dur / CHUNK_SEC))
    segments: list[dict] = []
    lang_out = language
    with tempfile.TemporaryDirectory() as td:
        for k in range(n):
            start = k * CHUNK_SEC
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
            r = httpx.post(GROQ_STT_URL, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                           files={"file": (part.name, part.read_bytes(), "audio/mpeg")}, data=data,
                           timeout=httpx.Timeout(600, connect=20))
            if r.status_code != 200:
                raise RuntimeError(f"Whisper Groq gagal ({r.status_code}): {r.text[:300]}")
            j = r.json()
            lang_out = lang_out or j.get("language")
            words = [{"text": w["word"].strip(), "start": round(w["start"] + start, 3), "end": round(w["end"] + start, 3)}
                     for w in j.get("words", []) if w.get("word", "").strip()]
            for s in j.get("segments", []):
                st, en = s["start"] + start, s["end"] + start
                seg_words = [w for w in words if st - 0.05 <= w["start"] < en + 0.05]
                segments.append({"start": round(st, 3), "end": round(en, 3), "text": s["text"].strip(),
                                 "words": seg_words})
            progress(f"Transkrip {min(100, (start + CHUNK_SEC) / max(dur, 1) * 100):.0f}% ({len(segments)} segmen)")
    return {"language": lang_out, "segments": segments, "engine": "groq"}
