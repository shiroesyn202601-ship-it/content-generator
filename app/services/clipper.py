"""Jalur 2 (Clipper): transkrip Whisper -> pilih segmen viral -> potong tanpa memutus kalimat."""
import threading

from .. import config, store
from . import ffmpeg_utils as ff
from . import llm, prompts

_model = None
_model_lock = threading.Lock()


def _whisper():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE,
                                  compute_type=config.WHISPER_COMPUTE)
    return _model


def transcribe_file(path, language: str | None, progress, total: float = 0) -> dict:
    progress(f"Memuat model Whisper '{config.WHISPER_MODEL}' (pertama kali akan mengunduh model)...")
    model = _whisper()
    segments, info = model.transcribe(str(path), language=language or None,
                                      word_timestamps=True, vad_filter=True)
    out = []
    for seg in segments:
        words = [{"text": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
                 for w in (seg.words or []) if w.word.strip()]
        out.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                    "text": seg.text.strip(), "words": words})
        if total:
            progress(f"Transkrip {seg.end / total * 100:.0f}% ({len(out)} segmen)")
    return {"language": info.language, "segments": out}


def transcribe(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    src = store.project_dir(pid) / p["research"]["source"]["path"]
    lang = params.get("language") or p["language"]
    tr = transcribe_file(src, lang, progress, p["research"]["source"].get("duration") or 0)

    def apply(p):
        p["script"]["transcript"] = tr
        store.touch_stage(p, "script")
        store.log(p, f"Whisper: {len(tr['segments'])} segmen")
    store.update(pid, apply)
    return {"segments": len(tr["segments"])}


def _all_words(segments: list[dict]) -> list[dict]:
    return [w for s in segments for w in s.get("words", [])]


def snap(segments: list[dict], start: float, end: float) -> tuple[float, float]:
    """Geser start/end ke batas kalimat (segmen Whisper) terdekat, lalu beri padding kecil
    yang tidak pernah masuk ke kata sebelum/sesudahnya — kata tidak akan terpotong."""
    if not segments:
        return start, end
    s_seg = min(segments, key=lambda s: abs(s["start"] - start))
    e_cands = [s for s in segments if s["end"] > s_seg["start"]] or segments
    e_seg = min(e_cands, key=lambda s: abs(s["end"] - end))
    st, en = s_seg["start"], e_seg["end"]
    words = _all_words(segments)
    prev_end = max([w["end"] for w in words if w["end"] <= st + 0.01] or [0.0])
    next_start = min([w["start"] for w in words if w["start"] >= en - 0.01] or [en + 1.0])
    st = max(0.0, st - min(0.25, (st - prev_end) / 2))
    en = en + min(0.35, (next_start - en) / 2)
    return round(st, 3), round(en, 3)


def find_clips(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    segs = p["script"]["transcript"]["segments"]
    progress("AI mencari momen viral...")
    sys_, usr = prompts.viral_clips(segs, p["format"], p["language"], p["research"]["source"].get("title", ""))
    res = llm.chat_json(sys_, usr, params.get("provider"), temperature=0.5)
    clips = []
    for i, c in enumerate(res.get("clips", [])):
        a = max(0, min(int(c.get("start_seg", 0)), len(segs) - 1))
        b = max(a, min(int(c.get("end_seg", a)), len(segs) - 1))
        st, en = snap(segs, segs[a]["start"], segs[b]["end"])
        clips.append({"id": f"c{i + 1}", "start": st, "end": en, "title": c.get("title", ""),
                      "hook": c.get("hook", ""), "reason": c.get("reason", ""),
                      "score": c.get("score", 0), "keywords": c.get("keywords", [])})
    clips.sort(key=lambda c: -(c.get("score") or 0))

    def apply(p):
        p["script"]["clips"] = clips
        p["script"]["selected_clip"] = None
        store.touch_stage(p, "script")
        store.log(p, f"AI: {len(clips)} kandidat klip")
    store.update(pid, apply)
    return {"count": len(clips)}


def cut_clip(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    clip = p["script"]["clips"][p["script"]["selected_clip"]]
    mode = params.get("crop_mode") or p["assets"].get("crop_mode") or "center"
    w, h = config.RESOLUTIONS[p["format"]]
    (d / "assets").mkdir(exist_ok=True)
    progress(f"Memotong {clip['start']:.1f}s - {clip['end']:.1f}s ({mode})...")
    vf = f"{ff.fit_filter(w, h, mode)},fps={config.FPS},format=yuv420p"
    ff.run(["-ss", f"{clip['start']:.3f}", "-to", f"{clip['end']:.3f}", "-i", p["research"]["source"]["path"],
            "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
            "assets/clip.mp4"], cwd=d)

    def apply(p):
        p["assets"]["clip_path"] = "assets/clip.mp4"
        p["assets"]["crop_mode"] = mode
        p["assets"]["clip_range"] = [clip["start"], clip["end"]]
        store.touch_stage(p, "assets")
        store.log(p, f"Klip dipotong ({mode})")
    store.update(pid, apply)
    return {"path": "assets/clip.mp4"}
