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
    """Transkrip dengan timestamp kata: Whisper lokal bila bisa, kalau tidak Whisper Groq (lihat stt.py)."""
    from . import stt
    return stt.transcribe(path, language, progress, total)


def transcribe_local(path, language: str | None, progress, total: float = 0) -> dict:
    progress(f"Memuat model Whisper '{config.WHISPER_MODEL}' (pertama kali akan mengunduh model)...")
    model = _whisper()
    # VAD default memakai jeda 2 dtk → segmen kepanjangan; 500 ms memberi batas kalimat lebih rapat.
    # condition_on_previous_text=False mencegah Whisper mengulang/berhalusinasi di audio panjang.
    segments, info = model.transcribe(str(path), language=language or None,
                                      word_timestamps=True, vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 500},
                                      condition_on_previous_text=False)
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


CLIP_LIMITS = {"short": (20, 59), "long": (90, 600)}
WINDOW_CHARS = 9000  # transkrip panjang dipecah per jendela agar tidak melewati batas token LLM gratis


def _num(v, default: float = 0.0) -> float:
    """LLM kadang mengirim angka sebagai string ("8", "8/10", "1-10") atau null."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().split("/")[0].split("-")[0])
    except (ValueError, TypeError):
        return default


def _fit_range(segs: list[dict], a: int, b: int, lo: float, hi: float) -> tuple[int, int]:
    """Perlebar/persempit rentang segmen supaya durasi klip masuk batas format."""
    while segs[b]["end"] - segs[a]["start"] < lo and b + 1 < len(segs) \
            and segs[b + 1]["end"] - segs[a]["start"] <= hi:
        b += 1
    while segs[b]["end"] - segs[a]["start"] > hi and b > a:
        b -= 1
    return a, b


def _windows(segs: list[dict]) -> list[tuple[int, int]]:
    out, start, size = [], 0, 0
    for i, s in enumerate(segs):
        size += len(s["text"]) + 20
        if size > WINDOW_CHARS and i > start:
            out.append((start, i))
            start, size = i, len(s["text"]) + 20
    out.append((start, len(segs)))
    return out


def find_clips(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    segs = p["script"]["transcript"]["segments"]
    if not segs:
        raise RuntimeError("Transkrip kosong — jalankan Transkrip dulu.")
    lo, hi = CLIP_LIMITS[p["format"]]
    title = p["research"]["source"].get("title", "")
    wins = _windows(segs)
    raw = []
    for k, (ws, we) in enumerate(wins):
        progress("AI mencari momen viral…" if len(wins) == 1 else f"AI mencari momen viral (bagian {k + 1}/{len(wins)})…")
        sys_, usr = prompts.viral_clips(segs[ws:we], p["format"], p["language"], title, offset=ws)
        raw += llm.chat_json(sys_, usr, params.get("provider"), temperature=0.5).get("clips", [])

    progress("Merapikan kandidat klip (durasi & tumpang-tindih)…")
    cands = []
    for c in raw:
        a = int(max(0, min(_num(c.get("start_seg")), len(segs) - 1)))
        b = int(max(a, min(_num(c.get("end_seg"), a), len(segs) - 1)))
        a, b = _fit_range(segs, a, b, lo, hi)
        st, en = snap(segs, segs[a]["start"], segs[b]["end"])
        if not (lo * 0.75 <= en - st <= hi + 1):
            continue  # tetap terlalu pendek/panjang (mis. sisa video tidak cukup)
        cands.append({"start": st, "end": en, "title": str(c.get("title") or ""),
                      "hook": str(c.get("hook") or ""), "reason": str(c.get("reason") or ""),
                      "score": int(round(max(0.0, min(10.0, _num(c.get("score")))))),
                      "keywords": [str(x) for x in c.get("keywords") or [] if x]})
    cands.sort(key=lambda c: -c["score"])
    clips = []
    for c in cands:  # buang klip yang >50% tumpang-tindih dengan klip berskor lebih tinggi
        if all(min(c["end"], o["end"]) - max(c["start"], o["start"]) <= 0.5 * (c["end"] - c["start"]) for o in clips):
            clips.append(c)
    clips = [{"id": f"c{i + 1}", **c} for i, c in enumerate(clips[:8])]
    if not clips:
        raise RuntimeError(f"AI tidak menemukan klip berdurasi {lo}-{hi} dtk. Coba lagi, atau pilih manual.")

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
    fit = ff.fit_filter(w, h, mode if mode != "face" else "blur")
    reframe_info = None
    if mode == "face":
        from . import reframe
        src = d / p["research"]["source"]["path"]
        progress("Auto-reframe: mendeteksi wajah & pergantian shot…")
        shots = reframe.track(src, clip["start"], clip["end"], progress)
        vs = next(s for s in ff.probe(src)["streams"] if s.get("codec_type") == "video")
        face_vf = reframe.crop_filter(shots, int(vs["width"]), int(vs["height"]), w, h)
        n_face = sum(s["cx"] is not None for s in shots)
        if face_vf:
            fit = face_vf
            progress(f"Auto-reframe: {len(shots)} shot, {n_face} dengan wajah — bingkai mengikuti wajah")
        else:
            progress("Auto-reframe: tidak ada wajah terdeteksi — memakai mode latar blur")
        reframe_info = {"shots": shots, "faces": n_face}
    progress(f"Memotong {clip['start']:.1f}s - {clip['end']:.1f}s ({mode})...")
    vf = f"{fit},fps={config.FPS},format=yuv420p"
    ff.run(["-ss", f"{clip['start']:.3f}", "-to", f"{clip['end']:.3f}", "-i", p["research"]["source"]["path"],
            "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
            "assets/clip.mp4"], cwd=d)

    def apply(p):
        p["assets"]["clip_path"] = "assets/clip.mp4"
        p["assets"]["crop_mode"] = mode
        p["assets"]["clip_range"] = [clip["start"], clip["end"]]
        p["assets"]["reframe"] = reframe_info
        store.touch_stage(p, "assets")
        store.log(p, f"Klip dipotong ({mode})")
    store.update(pid, apply)
    return {"path": "assets/clip.mp4"}
