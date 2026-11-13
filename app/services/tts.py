"""
Tahap 4: Voice Over dengan Edge-TTS (gratis).

Alur:
  naskah (*penekanan*) -> pecah kalimat -> kamus fonetik (teks ucapan) -> TTS per kalimat
  -> WordBoundary timestamp -> disejajarkan kembali ke kata ASLI (untuk subtitle)
  -> digabung dengan jeda antar-kalimat -> audio/vo.wav + words[] (format Word kanonik)

Catatan jujur: Edge-TTS publik tidak lagi menerima SSML kustom, jadi "emosi" dan
"penekanan intonasi" diterapkan lewat rate/pitch/volume per kalimat, bukan per kata.
"""
import asyncio
import json
import re
import wave
from pathlib import Path

from .. import config, store
from ..store import EMOTION_VOICE
from . import ffmpeg_utils as ff

SR = 24000
SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
TOKEN = re.compile(r"\*([^*]+)\*|(\S+)")
PUNCT = re.compile(r"^([\"'“‘(\[]*)(.*?)([\"'”’)\].,!?;:…]*)$")

_voices_cache: list | None = None
PRON_FILE = config.DATA_DIR / "pronunciation.json"


# ---------------- kamus fonetik ----------------

def load_pronunciation() -> dict:
    """Kamus global: Google Sheets (tab Pronunciation) bila aktif, kalau tidak file lokal."""
    local = json.loads(PRON_FILE.read_text(encoding="utf-8")) if PRON_FILE.exists() else {}
    if config.google_enabled():
        try:
            from . import gsheets
            local.update(gsheets.get_pronunciation())
        except Exception as e:  # offline -> pakai lokal
            print("Sheets pronunciation:", e)
    return local


def save_pronunciation(d: dict) -> None:
    d = {k.strip().lower(): v.strip() for k, v in d.items() if k.strip() and v.strip()}
    PRON_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    if config.google_enabled():
        from . import gsheets
        gsheets.save_pronunciation(d)


def apply_pron(word: str, pron: dict) -> str:
    pre, core, post = PUNCT.match(word).groups()
    rep = pron.get(core.lower())
    return f"{pre}{rep}{post}" if rep else word


def apply_pron_text(text: str, pron: dict) -> str:
    # frasa multi-kata dulu (mis. "shiroe syn"), lalu per kata
    for k in sorted((k for k in pron if " " in k), key=len, reverse=True):
        text = re.sub(rf"(?i)\b{re.escape(k)}\b", pron[k], text)
    return " ".join(apply_pron(w, pron) for w in text.split())


# ---------------- tokenisasi ----------------

def tokenize(sentence: str) -> list[dict]:
    """'Ini *rahasia besar* kita.' -> [{text:'Ini',emphasis:False}, {text:'rahasia',emphasis:True}, ...]"""
    out = []
    for m in TOKEN.finditer(sentence):
        if m.group(1) is not None:
            out += [{"text": w, "emphasis": True} for w in m.group(1).split()]
        else:
            out.append({"text": m.group(2).replace("*", ""), "emphasis": False})
    return [t for t in out if t["text"]]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text.strip()) if s.strip()]


# ---------------- Edge-TTS ----------------

def _rate(speed: float, delta: int = 0) -> str:
    return f"{round((speed - 1) * 100 + delta):+d}%"


def _pitch(hz: int) -> str:
    return f"{int(hz):+d}Hz"


async def _synth(text: str, voice: str, rate: str, pitch: str, volume: str, mp3: Path) -> list[dict]:
    import edge_tts
    try:
        com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume, boundary="WordBoundary")
    except TypeError:  # edge-tts < 7 tidak punya param boundary (default WordBoundary)
        com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
    bounds = []
    with open(mp3, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                st = chunk["offset"] / 1e7
                bounds.append({"text": chunk["text"], "start": st, "end": st + chunk["duration"] / 1e7})
    return bounds


def synth(text: str, voice: str, rate: str, pitch: str, volume: str, mp3: Path, retries: int = 3) -> list[dict]:
    last = None
    for _ in range(retries):
        try:
            return asyncio.run(_synth(text, voice, rate, pitch, volume, mp3))
        except Exception as e:  # jaringan putus -> coba lagi
            last = e
    raise RuntimeError(f"Edge-TTS gagal: {last}")


def sentence_params(v: dict, emotion: str, has_emphasis: bool) -> tuple[str, str, str]:
    e = EMOTION_VOICE.get(emotion, EMOTION_VOICE["netral"])
    rd, pd, vol = e["rate_delta"], v.get("pitch_hz", 0) + e["pitch_delta"], 0
    if has_emphasis and v.get("emphasis_boost", True):
        rd -= 4      # sedikit melambat
        pd += 2      # sedikit naik
        vol = 8      # sedikit lebih keras
    return _rate(float(v.get("speed", 1.0)), rd), _pitch(pd), f"{vol:+d}%"


def align(tokens: list[dict], spoken_counts: list[int], bounds: list[dict], total: float) -> list[dict]:
    """Sejajarkan WordBoundary (teks ucapan) ke token asli (teks tampil)."""
    words = []
    if bounds and len(bounds) == sum(spoken_counts):
        i = 0
        for t, c in zip(tokens, spoken_counts):
            c = max(c, 1)
            words.append({**t, "start": bounds[i]["start"], "end": bounds[min(i + c, len(bounds)) - 1]["end"]})
            i += c
        return words
    # fallback: bagi proporsional berdasarkan panjang karakter
    st = bounds[0]["start"] if bounds else 0.05
    en = bounds[-1]["end"] if bounds else max(total - 0.05, st + 0.1)
    lens = [max(len(t["text"]), 1) for t in tokens]
    tot, acc = sum(lens), st
    for t, ln in zip(tokens, lens):
        d = (en - st) * ln / tot
        words.append({**t, "start": acc, "end": acc + d})
        acc += d
    return words


def _read_frames(wav_path: Path) -> bytes:
    with wave.open(str(wav_path), "rb") as w:
        return w.readframes(w.getnframes())


def render_vo(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    tmp = d / "audio" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    v = p["voice"]
    pron = {**load_pronunciation(), **{k.lower(): val for k, val in p["audio"].get("pronunciation", {}).items()}}
    pause = int(v.get("pause_ms", 350)) / 1000

    pcm = bytearray()
    words, sentences, timings = [], [], []
    scenes = p["script"]["scenes"]
    total_sent = sum(len(split_sentences(s["text"])) for s in scenes)
    n = 0
    for s in scenes:
        scene_start = len(pcm) / 2 / SR
        for k, sent in enumerate(split_sentences(s["text"])):
            n += 1
            progress(f"TTS kalimat {n}/{total_sent}")
            tokens = tokenize(sent)
            if not tokens:
                continue
            spoken_tokens = [apply_pron_text(t["text"], pron) for t in tokens]
            spoken = " ".join(spoken_tokens)
            rate, pitch, vol = sentence_params(v, s.get("emotion", "netral"), any(t["emphasis"] for t in tokens))
            mp3, wav = tmp / f"{n:04d}.mp3", tmp / f"{n:04d}.wav"
            bounds = synth(spoken, v["voice"], rate, pitch, vol, mp3)
            ff.to_wav(mp3, wav, SR)
            frames = _read_frames(wav)
            dur = len(frames) / 2 / SR
            offset = len(pcm) / 2 / SR
            aligned = align(tokens, [len(x.split()) for x in spoken_tokens], bounds, dur)
            for w in aligned:
                words.append({"text": w["text"], "start": round(offset + w["start"], 3),
                              "end": round(offset + w["end"], 3), "emphasis": w["emphasis"],
                              "scene_id": s["id"]})
            sentences.append({"scene_id": s["id"], "text": sent, "start": round(offset, 3),
                              "end": round(offset + dur, 3), "rate": rate, "pitch": pitch})
            pcm += frames
            last = k == len(split_sentences(s["text"])) - 1
            gap = pause * (1.6 if last else 1.0)
            pcm += b"\x00\x00" * int(gap * SR)
        timings.append({"scene_id": s["id"], "start": round(scene_start, 3)})

    total = len(pcm) / 2 / SR
    for i, t in enumerate(timings):
        t["end"] = timings[i + 1]["start"] if i + 1 < len(timings) else round(total, 3)

    out = d / "audio" / "vo.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(pcm))
    for f in tmp.glob("*"):
        f.unlink(missing_ok=True)

    def apply(p):
        p["audio"].update({"vo_path": "audio/vo.wav", "words": words, "sentences": sentences,
                           "scene_timings": timings, "duration": round(total, 3)})
        store.touch_stage(p, "audio")
        store.log(p, f"VO dirender: {total:.1f}s, {len(words)} kata")
    store.update(pid, apply)
    return {"duration": total, "words": len(words)}


def preview(text: str, voice: dict, emotion: str = "netral", pron: dict | None = None) -> bytes:
    """Pratinjau cepat satu kalimat (sebelum render penuh)."""
    pron = {**load_pronunciation(), **{k.lower(): v for k, v in (pron or {}).items()}}
    tokens = tokenize(text)
    spoken = " ".join(apply_pron_text(t["text"], pron) for t in tokens)
    rate, pitch, vol = sentence_params(voice, emotion, any(t["emphasis"] for t in tokens))
    mp3 = config.CACHE_DIR / "preview.mp3"
    synth(spoken, voice.get("voice", "id-ID-ArdiNeural"), rate, pitch, vol, mp3)
    return mp3.read_bytes()


def list_voices() -> list[dict]:
    global _voices_cache
    if _voices_cache is None:
        import edge_tts
        allv = asyncio.run(edge_tts.list_voices())
        keep = ("id-", "ms-", "jv-", "su-", "en-US", "en-GB", "ja-JP", "ko-KR")
        _voices_cache = [{"name": x["ShortName"], "gender": x.get("Gender"), "locale": x.get("Locale")}
                         for x in allv if x["ShortName"].startswith(keep)]
    return _voices_cache
