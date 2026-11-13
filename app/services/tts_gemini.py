"""Voice over dengan Gemini TTS (gratis, jauh lebih ekspresif dari Edge-TTS, mendukung bahasa Indonesia).

Gemini tidak memberi timestamp per kata, jadi:
  1) seluruh naskah dibacakan dalam SATU permintaan (hemat kuota harian),
  2) audio ditranskrip Whisper dengan word timestamps (forced alignment sederhana),
  3) kata Whisper dicocokkan ke kata naskah (difflib) → format Word kanonik yang sama dengan Edge-TTS,
     sehingga subtitle, potongan per kalimat, dan render tidak perlu tahu engine mana yang dipakai."""
import base64
import difflib
import re
import wave

import httpx

from .. import config, store
from . import ffmpeg_utils as ff
from .tts import SR, apply_pron_text, load_pronunciation, split_sentences, tokenize

MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
VOICES = ["Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]
STYLE = {
    "naratif": "a calm, warm storyteller narrating a documentary",
    "antusias": "an energetic, enthusiastic YouTube Shorts narrator, upbeat and fast-paced",
    "edukatif": "a clear, friendly teacher explaining something fascinating",
    "sinematik": "a deep, dramatic cinematic trailer narrator with deliberate pauses",
}
EMO_EN = {"netral": "neutral", "ceria": "cheerful", "tegang": "tense", "dramatis": "dramatic", "sedih": "sad",
          "misteri": "mysterious, hushed", "heroik": "heroic", "lucu": "playful"}


def _norm(w: str) -> str:
    return re.sub(r"[^0-9a-zà-ÿ]", "", w.lower())


def _synthesize(text: str, voice: str, progress) -> bytes:
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY kosong.")
    last = ""
    for model in MODELS:
        progress(f"Gemini TTS ({model}, suara {voice}) membacakan naskah… ±10-40 dtk")
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       params={"key": config.GEMINI_API_KEY}, timeout=httpx.Timeout(240, connect=15),
                       json={"contents": [{"parts": [{"text": text}]}],
                             "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                                 "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}})
        if r.status_code == 200:
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            return base64.b64decode(part["data"])
        last = f"{model} → {r.status_code}: {r.text[:200]}"
        progress(f"Gemini TTS {model} gagal ({r.status_code}) — mencoba model berikutnya…")
    raise RuntimeError(f"Gemini TTS gagal: {last}")


def _match(tokens: list[dict], heard: list[dict], total: float) -> list[tuple[float, float]]:
    """Waktu (start, end) untuk tiap token naskah dari kata hasil Whisper. Token yang tidak cocok
    diinterpolasi di antara tetangganya yang cocok."""
    a = [_norm(t["text"]) for t in tokens]
    b = [_norm(w["text"]) for w in heard]
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            w = heard[blk.b + k]
            times[blk.a + k] = (w["start"], w["end"])
    # interpolasi celah
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        st = times[i - 1][1] if i > 0 else 0.0
        en = times[j][0] if j < len(times) else total
        lens = [max(len(tokens[k]["text"]), 1) for k in range(i, j)]
        acc = st
        for k, ln in zip(range(i, j), lens):
            dur = max(en - st, 0.05) * ln / sum(lens)
            times[k] = (acc, acc + dur)
            acc += dur
        i = j
    return times  # type: ignore[return-value]


def render_vo(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    (d / "audio").mkdir(exist_ok=True)
    v = p["voice"]
    pron = {**load_pronunciation(), **{k.lower(): val for k, val in p["audio"].get("pronunciation", {}).items()}}
    scenes = p["script"]["scenes"]

    # 1) teks ucapan + struktur token tampil per kalimat
    struct, paras = [], []
    for s in scenes:
        sents = []
        for sent in split_sentences(s["text"]):
            toks = tokenize(sent)
            if toks:
                sents.append(toks)
        struct.append((s, sents))
        spoken = " ".join(apply_pron_text(" ".join(t["text"].upper() if t["emphasis"] else t["text"] for t in toks), pron)
                          for toks in sents)
        emo = EMO_EN.get(s.get("emotion", "netral"), "neutral")
        paras.append(f"[{emo}] {spoken}" if emo != "neutral" else spoken)
    style = STYLE.get(v.get("preset", "naratif"), STYLE["naratif"])
    prompt = (f"Read the following Indonesian script aloud in natural Indonesian, as {style}. Words in CAPITALS are "
              f"emphasized (do not spell them). Tags in [brackets] describe the emotion of that paragraph — do NOT "
              f"read the tags aloud. Pause briefly between paragraphs.\n\n" + "\n\n".join(paras))

    # 2) sintesis
    pcm = _synthesize(prompt, v.get("gemini_voice") or "Kore", progress)
    raw = d / "audio" / "gemini_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    speed = float(v.get("speed", 1.0))
    out = d / "audio" / "vo.wav"
    flt = f"atempo={speed:.3f}," if abs(speed - 1) > 0.02 else ""
    ff.run(["-i", "audio/gemini_raw.wav", "-af", f"{flt}aresample={SR}", "-ac", "1", "-sample_fmt", "s16",
            "audio/vo.wav"], cwd=d)
    raw.unlink(missing_ok=True)
    total = ff.duration(out)

    # 3) timing kata via Whisper
    progress("Menyelaraskan kata dengan suara (Whisper)… pertama kali akan mengunduh model")
    from .clipper import transcribe_file
    tr = transcribe_file(out, p["language"], lambda m: progress(f"Whisper: {m}"), total)
    heard = [w for seg in tr["segments"] for w in seg.get("words", [])]
    flat = [(s, si, t) for s, sents in struct for si, toks in enumerate(sents) for t in toks]
    times = _match([t for _, _, t in flat], heard, total)

    words, sentences, timings = [], [], []
    sent_no, prev_key = 0, None
    for (s, si, t), (st, en) in zip(flat, times):
        if (s["id"], si) != prev_key:
            sent_no += 1
            prev_key = (s["id"], si)
            sentences.append({"scene_id": s["id"], "text": " ".join(x["text"] for x in struct_tokens(struct, s["id"], si)),
                              "start": round(st, 3), "end": round(en, 3)})
            if not timings or timings[-1]["scene_id"] != s["id"]:
                timings.append({"scene_id": s["id"], "start": round(st if timings else 0.0, 3)})
        sentences[-1]["end"] = round(en, 3)
        words.append({"text": t["text"], "start": round(st, 3), "end": round(en, 3), "emphasis": t["emphasis"],
                      "scene_id": s["id"], "sent": sent_no})
    for i, t in enumerate(timings):
        t["end"] = timings[i + 1]["start"] if i + 1 < len(timings) else round(total, 3)
    hits = {_norm(w["text"]) for w in heard}
    matched = sum(1 for _, _, t in flat if _norm(t["text"]) in hits)

    def apply(q):
        q["audio"].update({"vo_path": "audio/vo.wav", "words": words, "sentences": sentences,
                           "scene_timings": timings, "duration": round(total, 3), "engine": "gemini"})
        store.touch_stage(q, "audio")
        store.log(q, f"VO Gemini dirender: {total:.1f}s, {len(words)} kata, Whisper {len(heard)} kata")
    store.update(pid, apply)
    return {"duration": total, "words": len(words), "heard": len(heard), "matched": matched}


def struct_tokens(struct, sid, si):
    for s, sents in struct:
        if s["id"] == sid:
            return sents[si]
    return []
