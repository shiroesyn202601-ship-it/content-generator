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


_END = (".", "?", "!", "…")
GAP_SENT = 0.8      # jeda bicara (dtk) yang dianggap akhir kalimat walau tanpa tanda baca
MAX_SENT = 20.0     # kalimat lebih panjang dari ini dipecah di jeda/koma terdekat
PAD_IN, PAD_OUT = 0.35, 0.45  # napas sebelum kalimat pertama & sesudah kalimat terakhir (hanya di area hening)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _punct_words(seg: dict) -> list[dict]:
    """Kata Whisper Groq tidak membawa tanda baca → salin dari teks segmen dengan penyejajaran difflib."""
    import difflib
    words = [dict(w) for w in seg.get("words", [])]
    toks = seg.get("text", "").split()
    a, b = [_norm(w["text"]) for w in words], [_norm(t) for t in toks]
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            tok = toks[blk.b + k].rstrip("\"'”’)]")
            words[blk.a + k]["_end"] = tok.endswith(_END)
            words[blk.a + k]["_comma"] = tok.endswith((",", ";", ":"))
    if words:
        words[-1]["_seg_end"] = True
    return words


def sentences(segments: list[dict]) -> list[dict]:
    """Satuan kalimat dari kata ber-timestamp: putus di . ? ! atau jeda ≥0.8 dtk; kalimat sangat panjang dipecah
    di koma/jeda terdekat. Inilah satuan yang dipilih AI, sehingga klip selalu mulai & berakhir di batas kalimat.
    [{start, end, text}]; transkrip tanpa timestamp kata memakai segmen apa adanya."""
    words = [w for s in segments for w in _punct_words(s)]
    if not words:
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segments]
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99.0
        long_ = cur[-1]["end"] - cur[0]["start"] > MAX_SENT
        if w.get("_end") or gap >= GAP_SENT or (long_ and (w.get("_comma") or w.get("_seg_end") or gap >= 0.3)):
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["text"] for x in cur)})
            cur = []
    if cur:
        out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(x["text"] for x in cur)})
    return out


def _pad(words: list[dict], st: float, en: float) -> tuple[float, float]:
    """Tambah napas di awal/akhir tanpa pernah masuk ke kata sebelum/sesudahnya."""
    prev_end = max([w["end"] for w in words if w["end"] <= st + 0.01] or [max(0.0, st - PAD_IN * 2)])
    next_start = min([w["start"] for w in words if w["start"] >= en - 0.01] or [en + PAD_OUT * 2])
    st = max(0.0, st - min(PAD_IN, max(0.0, st - prev_end) * 0.6))
    en = en + min(PAD_OUT, max(0.0, next_start - en) * 0.6)
    return round(st, 3), round(en, 3)


def snap(segments: list[dict], start: float, end: float) -> tuple[float, float]:
    """Geser start/end ke awal/akhir kalimat terdekat, lalu beri jeda napas — kata tidak akan terpotong."""
    if not segments:
        return start, end
    sents = sentences(segments)
    s0 = min(sents, key=lambda s: abs(s["start"] - start))
    e_cands = [s for s in sents if s["end"] > s0["start"]] or sents
    e0 = min(e_cands, key=lambda s: abs(s["end"] - end))
    return _pad(_all_words(segments), s0["start"], e0["end"])


CLIP_LIMITS = {"short": (20, 59), "long": (90, 600)}
WINDOW_CHARS = 9000   # transkrip panjang dipecah per jendela agar tidak melewati batas token LLM gratis
WINDOW_OVERLAP = 1500  # jendela bertumpuk supaya momen di perbatasan jendela tidak hilang
# Bobot rubrik (diadaptasi dari Anil-matcha & openshorts): hook paling menentukan retensi Shorts.
WEIGHTS = {"hook": 0.35, "alur": 0.25, "nilai": 0.25, "bagikan": 0.15}


def _num(v, default: float = 0.0) -> float:
    """LLM kadang mengirim angka sebagai string ("8", "8/10", "1-10") atau null."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().split("/")[0].split("-")[0])
    except (ValueError, TypeError):
        return default


def _fit_range(units: list[dict], a: int, b: int, lo: float, hi: float) -> tuple[int, int]:
    """Perlebar/persempit rentang kalimat supaya durasi klip masuk batas format."""
    while units[b]["end"] - units[a]["start"] < lo and b + 1 < len(units) \
            and units[b + 1]["end"] - units[a]["start"] <= hi:
        b += 1
    while units[b]["end"] - units[a]["start"] > hi and b > a:
        b -= 1
    return a, b


def _windows(units: list[dict]) -> list[tuple[int, int]]:
    """Jendela [awal, akhir) berukuran ±WINDOW_CHARS yang saling tumpang ±WINDOW_OVERLAP karakter."""
    sizes = [len(u["text"]) + 20 for u in units]
    out, start = [], 0
    while start < len(units):
        end, size = start, 0
        while end < len(units) and (size + sizes[end] <= WINDOW_CHARS or end == start):
            size += sizes[end]
            end += 1
        out.append((start, end))
        if end >= len(units):
            break
        back, nxt = 0, end
        while nxt > start + 1 and back + sizes[nxt - 1] <= WINDOW_OVERLAP:
            nxt -= 1
            back += sizes[nxt]
        start = nxt
    return out


def _overlap(a: dict, b: dict) -> float:
    """Porsi tumpang-tindih terhadap klip yang lebih pendek (0..1)."""
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    return max(0.0, inter) / max(0.1, min(a["end"] - a["start"], b["end"] - b["start"]))


def _schema() -> dict:
    sub = llm.strict({k: llm.INT for k in WEIGHTS})
    clip = llm.strict({"start_unit": llm.INT, "end_unit": llm.INT, "title": llm.STR, "hook": llm.STR,
                       "reason": llm.STR, "scores": sub, "keywords": llm.arr(llm.STR)})
    return llm.strict({"clips": llm.arr(clip)})


def find_clips(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    segs = p["script"]["transcript"]["segments"]
    if not segs:
        raise RuntimeError("Transkrip kosong — jalankan Transkrip dulu.")
    lo, hi = CLIP_LIMITS[p["format"]]
    title = p["research"]["source"].get("title", "")
    units = sentences(segs)
    words = _all_words(segs)
    dur = units[-1]["end"] - units[0]["start"]
    want = max(3, min(10, int(dur // 90) + 2))  # jumlah klip wajar sesuai panjang video
    wins = _windows(units)
    per_win = max(3, min(6, -(-want * 2 // len(wins))))
    direction = params.get("direction") if params.get("direction") is not None else p["script"].get("direction") or ""
    raw, failed = _score_windows(pid, p, units, wins, title, per_win, direction, params.get("provider"), progress)
    if failed and not raw:
        raise RuntimeError(f"Semua {len(wins)} bagian gagal dinilai AI — kuota gratis sedang habis. "
                           f"Tunggu 1-2 menit lalu klik lagi (bagian yang sudah berhasil tidak diulang).\n\n{failed[0]}")

    progress("Merapikan kandidat klip (batas kalimat, durasi & tumpang-tindih)…")
    cands = []
    for c in raw:
        a = int(max(0, min(_num(c.get("start_unit", c.get("start_seg"))), len(units) - 1)))
        b = int(max(a, min(_num(c.get("end_unit", c.get("end_seg")), a), len(units) - 1)))
        a, b = _fit_range(units, a, b, lo, hi)
        st, en = _pad(words, units[a]["start"], units[b]["end"])
        if not (lo * 0.75 <= en - st <= hi + 1):
            continue  # tetap terlalu pendek/panjang (mis. sisa video tidak cukup)
        sc = c.get("scores") if isinstance(c.get("scores"), dict) else {}
        subs = {k: int(max(0, min(100, _num(sc.get(k), _num(c.get("score")) * 10)))) for k in WEIGHTS}
        total = int(round(sum(subs[k] * w for k, w in WEIGHTS.items())))
        cands.append({"start": st, "end": en, "title": str(c.get("title") or ""),
                      "hook": str(c.get("hook") or ""), "reason": str(c.get("reason") or ""),
                      "score": total, "scores": subs,
                      "keywords": [str(x) for x in c.get("keywords") or [] if x]})
    cands.sort(key=lambda c: -c["score"])
    clips = []
    for c in cands:  # klip yang tumpang-tindih ≥25% dengan klip berskor lebih tinggi dibuang
        if all(_overlap(c, o) < 0.25 for o in clips):
            clips.append(c)
    clips = [{"id": f"c{i + 1}", **c} for i, c in enumerate(clips[:want])]
    if not clips:
        raise RuntimeError(f"AI tidak menemukan klip berdurasi {lo}-{hi} dtk. Coba lagi, atau pilih manual.")

    note = (f"{len(failed)} dari {len(wins)} bagian video belum dinilai (kuota AI gratis habis). "
            "Klik \"Cari Momen Viral\" lagi 1-2 menit kemudian untuk melengkapi — bagian yang sudah dinilai tidak diulang."
            if failed else "")

    def apply(p):
        p["script"]["clips"] = clips
        p["script"]["selected_clip"] = None
        p["script"]["clip_note"] = note
        if params.get("direction") is not None:
            p["script"]["direction"] = params["direction"]
        store.touch_stage(p, "script")
        store.log(p, f"AI: {len(clips)} kandidat klip dari {len(units)} kalimat"
                     + (f" ({len(failed)}/{len(wins)} bagian gagal)" if failed else ""))
    store.update(pid, apply)
    return {"count": len(clips), "failed_parts": len(failed), "parts": len(wins)}


def _score_windows(pid, p, units, wins, title, per_win, direction, provider, progress):
    """Nilai tiap jendela transkrip. Jendela dibagi bergiliran ke model-model Groq dan dikerjakan PARALEL
    (tiap model punya kuota token/menit sendiri, ±8rb di tier gratis). Hasil tiap jendela disimpan di
    script.clip_cache → bila sebagian gagal, klik ulang hanya mengerjakan jendela yang belum.
    Mengembalikan (kandidat mentah, [pesan error])."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    from .. import jobs

    def key(ws, we):
        txt = "|".join(u["text"] for u in units[ws:we])
        return hashlib.sha1(f"{p['format']}|{per_win}|{direction}|{txt}".encode()).hexdigest()[:16]

    cache = dict(p["script"].get("clip_cache") or {})
    todo = [(k, ws, we) for k, (ws, we) in enumerate(wins) if key(ws, we) not in cache]
    raw = [c for ws, we in wins for c in cache.get(key(ws, we), [])]
    if not todo:
        return raw, []
    use_groq = (provider or config.LLM_PROVIDER).lower() == "groq" or (
        not provider and "groq" in config.LLM_FALLBACK_PROVIDERS and config.GROQ_API_KEY)
    models = llm.groq_models() if use_groq and config.GROQ_API_KEY else [None]
    lanes = {m: [t for i, t in enumerate(todo) if models[i % len(models)] == m] for m in models}
    done, errors, lock = [len(wins) - len(todo)], [], __import__("threading").Lock()
    parent_progress, parent_jid = getattr(jobs._local, "progress", None), getattr(jobs._local, "jid", None)
    progress(f"AI menilai {len(todo)} bagian video" + (f" paralel di {len(models)} model Groq…" if len(models) > 1 else "…"))

    def lane(model, items):
        jobs._local.progress, jobs._local.jid = parent_progress, parent_jid  # bar status & tombol Batalkan
        for k, ws, we in items:
            sys_, usr = prompts.viral_clips(units[ws:we], p["format"], p["language"], title, offset=ws,
                                            count=per_win, direction=direction)
            try:
                # tanpa schema strict: jawaban panjang gpt-oss sering gagal validasi & membakar kuota token
                got = llm.chat_json(sys_, usr, "groq" if model else provider, temperature=0.4,
                                    groq_model=model, effort="low", fallback=model is None).get("clips", [])
            except (llm.LLMError, Exception) as e:  # noqa: BLE001 — satu bagian gagal tidak menggagalkan semua
                if isinstance(e, jobs.JobCancelled):
                    raise
                with lock:
                    errors.append(f"Bagian {k + 1}: {str(e)[:300]}")
                continue
            got = [c for c in got if isinstance(c, dict)]
            with lock:
                raw.extend(got)
                cache[key(ws, we)] = got
                done[0] += 1
                n = done[0]

            def save(pp, kk=key(ws, we), g=got):
                pp["script"].setdefault("clip_cache", {})[kk] = g
            store.update(pid, save)
            progress(f"AI menilai momen viral: {n}/{len(wins)} bagian selesai…")

    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        futs = [ex.submit(lane, m, items) for m, items in lanes.items() if items]
        for f in futs:
            f.result()
    # buang cache jendela yang sudah tidak dipakai (transkrip / arahan berubah)
    keep = {key(ws, we) for ws, we in wins}

    def prune(pp):
        pp["script"]["clip_cache"] = {k: v for k, v in (pp["script"].get("clip_cache") or {}).items() if k in keep}
    store.update(pid, prune)
    return raw, errors


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
        progress("Auto-reframe: mendeteksi wajah & potongan scene…")
        pl, reframe_info = reframe.track(src, clip["start"], clip["end"], w, h, progress)
        if pl:
            reframe.write_cmds(pl, d / "assets" / "reframe.cmd")
            fit = reframe.crop_filter(pl, "assets/reframe.cmd", reframe_info["src_h"], w, h)
            progress(f"Auto-reframe ({reframe_info['engine']}): {reframe_info['shots']} shot, wajah di "
                     f"{reframe_info['face_ratio'] * 100:.0f}% sampel — kamera mengikuti wajah")
        else:
            progress("Auto-reframe: tidak ada wajah terdeteksi — memakai mode latar blur")
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
