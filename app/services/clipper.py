"""Jalur 2 (Clipper): transkrip Whisper -> pilih segmen viral -> potong tanpa memutus kalimat."""
import threading
import time

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
    src = store.source_file(p)
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
WINDOW_CHARS = 9000   # ukuran jendela minimum (karakter)
WINDOW_MAX = 17000    # maks ±5rb token/permintaan: muat di kuota 8rb token/menit per model Groq gratis
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


def _windows(units: list[dict], limit: int = WINDOW_CHARS) -> list[tuple[int, int]]:
    """Jendela [awal, akhir) berukuran ±limit karakter yang saling tumpang ±WINDOW_OVERLAP karakter."""
    sizes = [len(u["text"]) + 20 for u in units]
    out, start = [], 0
    while start < len(units):
        end, size = start, 0
        while end < len(units) and (size + sizes[end] <= limit or end == start):
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
    if params.get("lo") and params.get("hi"):  # durasi pilihan kreator (mis. 15-30 dtk)
        lo, hi = max(5, int(params["lo"])), max(int(params["lo"]) + 5, int(params["hi"]))
    title = p["research"]["source"].get("title", "")
    units = sentences(segs)
    words = _all_words(segs)
    dur = units[-1]["end"] - units[0]["start"]
    want = max(3, min(10, int(dur // 90) + 2))  # jumlah klip wajar sesuai panjang video
    # Ukuran jendela menyesuaikan: sebisa mungkin 1 jendela per model Groq → semua dinilai bersamaan tanpa antre,
    # dan instruksi prompt tidak diulang berkali-kali (hemat kuota token/menit).
    lanes = len(llm.groq_models()) if config.GROQ_API_KEY else 1
    total = sum(len(u["text"]) + 20 for u in units)
    wins = _windows(units, int(min(WINDOW_MAX, max(WINDOW_CHARS, total * 1.15 / lanes))))
    per_win = max(3, min(6, -(-want * 2 // len(wins))))
    direction = params.get("direction") if params.get("direction") is not None else p["script"].get("direction") or ""
    raw, failed = _score_windows(pid, p, units, wins, title, per_win, direction, params.get("provider"), progress,
                                 (lo, hi))
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


def _score_windows(pid, p, units, wins, title, per_win, direction, provider, progress, limits=None):
    """Nilai tiap jendela transkrip. Jendela dibagi bergiliran ke model-model Groq dan dikerjakan PARALEL
    (tiap model punya kuota token/menit sendiri, ±8rb di tier gratis). Hasil tiap jendela disimpan di
    script.clip_cache → bila sebagian gagal, klik ulang hanya mengerjakan jendela yang belum.
    Mengembalikan (kandidat mentah, [pesan error])."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    from .. import jobs

    def key(ws, we):
        txt = "|".join(u["text"] for u in units[ws:we])
        return hashlib.sha1(f"{p['format']}|{per_win}|{direction}|{limits}|{txt}".encode()).hexdigest()[:16]

    cache = dict(p["script"].get("clip_cache") or {})
    todo = [(k, ws, we) for k, (ws, we) in enumerate(wins) if key(ws, we) not in cache]
    raw = [c for ws, we in wins for c in cache.get(key(ws, we), [])]
    if not todo:
        return raw, []
    use_groq = (provider or config.LLM_PROVIDER).lower() == "groq" or (
        not provider and "groq" in config.LLM_FALLBACK_PROVIDERS and config.GROQ_API_KEY)
    models = llm.groq_models() if use_groq and config.GROQ_API_KEY else [None]
    # Antrean bersama: model yang bebas langsung mengambil bagian berikutnya; model yang kena batas
    # token/menit mengembalikan bagiannya ke antrean (diambil model lain) lalu istirahat sampai pulih.
    import queue
    import threading
    q: "queue.Queue" = queue.Queue()
    for t in todo:
        q.put(t)
    pending = [len(todo)]
    done, errors, lock = [len(wins) - len(todo)], [], threading.Lock()
    parent_progress, parent_jid = getattr(jobs._local, "progress", None), getattr(jobs._local, "jid", None)
    progress(f"AI menilai {len(todo)} bagian video" + (f" paralel di {len(models)} model Groq…" if len(models) > 1 else "…"))

    def lane(model):
        jobs._local.progress, jobs._local.jid = parent_progress, parent_jid  # bar status & tombol Batalkan
        while True:
            with lock:
                if pending[0] <= 0:
                    return
            try:
                k, ws, we = q.get(timeout=1)
            except queue.Empty:
                continue  # bagian sedang dikerjakan model lain (bisa dikembalikan bila kena batas)
            sys_, usr = prompts.viral_clips(units[ws:we], p["format"], p["language"], title, offset=ws,
                                            count=per_win, direction=direction, limits=limits)
            try:
                # tanpa schema strict: jawaban panjang gpt-oss sering gagal validasi & membakar kuota token
                got = llm.chat_json(sys_, usr, "groq" if model else provider, temperature=0.4,
                                    groq_model=model, effort="low", fallback=model is None,
                                    max_wait=3 if model and len(models) > 1 else None).get("clips", [])
            except llm.RateLimited as e:
                q.put((k, ws, we))  # oper ke model lain
                progress(f"AI menilai momen viral: {done[0]}/{len(wins)} bagian selesai — kuota gratis per menit "
                         f"{e.model.split('/')[-1]} habis, sisa bagian dilanjutkan ±{e.wait:.0f} dtk lagi…")
                for _ in range(int(e.wait) + 1):  # istirahat sampai kuota model ini pulih
                    time.sleep(1)
                    with lock:
                        if pending[0] <= 0:
                            return
                continue
            except Exception as e:  # noqa: BLE001 — satu bagian gagal tidak menggagalkan semua
                if isinstance(e, jobs.JobCancelled):
                    with lock:
                        pending[0] = 0
                    raise
                with lock:
                    errors.append(f"Bagian {k + 1}: {str(e)[:300]}")
                    pending[0] -= 1
                continue
            got = [c for c in got if isinstance(c, dict)]
            with lock:
                raw.extend(got)
                cache[key(ws, we)] = got
                done[0] += 1
                pending[0] -= 1
                n = done[0]

            def save(pp, kk=key(ws, we), g=got):
                pp["script"].setdefault("clip_cache", {})[kk] = g
            store.update(pid, save)
            progress(f"AI menilai momen viral: {n}/{len(wins)} bagian selesai…")

    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        futs = [ex.submit(lane, m) for m in models]
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
        src = store.source_file(p)
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
    ff.run(["-ss", f"{clip['start']:.3f}", "-to", f"{clip['end']:.3f}", "-i", str(store.source_file(p)),
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


# ---------------- satu video sumber → banyak video sekaligus ----------------

def fork_clip(src_pid: str, idx: int) -> dict:
    """Jadikan satu kandidat klip proyek anak (transkrip ikut, video sumber dipakai bersama). Mulai di tahap Momen."""
    src = store.load(src_pid)
    clip = src["script"]["clips"][idx]
    # Video hasil disimpan di dalam folder proyek sumber (clips/<id>) dan membaca video sumber milik
    # induknya lewat store.source_file — tidak ada salinan video sumber per klip.
    p = store.new_project("clipper", src["format"], "", language=src["language"], parent=src_pid)
    p.update({"title": clip.get("title") or src["title"], "current_stage": 1, "voice": src["voice"]})
    p["research"] = src["research"]
    p["script"] = {**src["script"], "selected_clip": idx, "clip_cache": {}}
    p["subtitles"]["style"] = dict(src["subtitles"].get("style") or p["subtitles"]["style"])
    p["assets"]["crop_mode"] = src["assets"].get("crop_mode") or p["assets"]["crop_mode"]
    p["stage_status"].update({"research": "approved", "script": "draft"})
    p["parent"] = src_pid
    store.log(p, f"Dari proyek {src_pid}, klip {clip['id']}")
    return store.save(p)


def produce_clips(pid: str, params: dict, progress) -> dict:
    """Produksi massal Clipper: tiap klip yang dicentang → proyek sendiri → potong + reframe → subtitle →
    (musik) → render → thumbnail. Hasil masuk tab Upload & Jadwal untuk dicek sebelum diunggah —
    gerbang manusia tetap ada di sana (dan tiap proyek tetap bisa dibuka & diedit per tahap).
    Berurutan (render berat). Satu klip gagal tidak menghentikan yang lain."""
    from . import audio_lib, render, subtitles, thumbs

    src = store.load(pid)
    clips = src["script"].get("clips") or []
    idx = [int(i) for i in params.get("indexes") or [] if 0 <= int(i) < len(clips)]
    if not idx:
        raise RuntimeError("Centang minimal satu klip.")
    crop = params.get("crop_mode") or src["assets"].get("crop_mode") or "face"
    style = params.get("sub_style") or src["subtitles"].get("style")
    music = bool(params.get("music"))
    from . import enhance
    eopts = enhance.options(params.get("enhance")) if params.get("enhance") is not False else None

    def save_src(q):
        q["assets"]["crop_mode"] = crop
        if style:
            q["subtitles"]["style"] = style
    store.update(pid, save_src)
    created, failed = [], []
    for n, i in enumerate(idx, 1):
        clip = clips[i]
        tag = f"Video {n}/{len(idx)}"
        progress(f"{tag}: menyiapkan proyek '{clip.get('title', '')[:40]}'…")
        p = fork_clip(pid, i)
        qid = p["id"]
        step = "persiapan"

        def sub(msg, tag=tag):
            progress(f"{tag}: {msg}")
        try:
            store.update(qid, lambda q: store.approve(q, "script"))
            step = "potong"
            cut_clip(qid, {"crop_mode": crop}, sub)
            if eopts and (eopts["commentary"] != "off" or any(eopts[k] for k in
                                                              ("context", "facts", "broll", "tighten", "punch", "emoji"))):
                step = "nilai tambah"
                try:
                    enhance.run(qid, {"opts": eopts, "seed": f"{qid}{n}"}, sub)
                except Exception as e:  # noqa: BLE001 — gagal nilai tambah: klip tetap jadi versi biasa
                    from .. import jobs
                    if isinstance(e, jobs.JobCancelled):
                        raise
                    sub(f"Nilai tambah dilewati: {str(e)[:120]}")
                    store.update(qid, lambda q, e=e: store.log(q, f"Nilai tambah gagal: {e}"))
            store.update(qid, lambda q: store.approve(q, "assets"))
            step = "musik"
            if music:
                store.update(qid, lambda q: q["audio"]["music"].update({"enabled": True}))
                audio_lib.auto_plan(qid, {"mood": params.get("mood") or "santai"}, sub)
            store.update(qid, lambda q: store.approve(q, "audio"))
            step = "subtitle"
            if style:
                st = enhance.vary_style(style, hash(qid)) if eopts and eopts.get("vary") else dict(style)
                store.update(qid, lambda q, st=st: q["subtitles"].update({"style": st}))
            subtitles.build(qid, {}, sub)
            store.update(qid, lambda q: store.approve(q, "subtitles"))
            step = "render"
            render.run(qid, {"drive_background": True}, sub)
            store.update(qid, lambda q: store.approve(q, "render"))
            step = "thumbnail"
            thumbs.auto(qid, {}, sub)
            store.update(qid, lambda q: store.approve(q, "thumbnail"))
            created.append({"id": qid, "title": p["title"], "score": clip.get("score")})
        except Exception as e:  # noqa: BLE001
            from .. import jobs
            if isinstance(e, jobs.JobCancelled):
                raise
            failed.append({"id": qid, "title": p["title"], "step": step, "error": str(e)[:200]})
            store.update(qid, lambda q, e=e, step=step: store.log(q, f"Produksi massal berhenti di tahap {step}: {e}"))
    store.update(pid, lambda q: store.log(q, f"Produksi massal: {len(created)} video jadi"
                                             + (f", {len(failed)} perlu dicek manual" if failed else "")))
    return {"created": created, "failed": failed}


def rebrand(pid: str, params: dict, progress) -> dict:
    """Render ulang semua video hasil proyek sumber ini supaya logo/overlay terbaru ikut terpasang.
    Status tahap (Thumbnail/Upload) dikembalikan seperti semula — ganti logo tidak membuat thumbnail
    atau jadwal usang. Video yang sudah terunggah ke YouTube dilewati (file di YouTube tidak berubah)."""
    from . import render
    kids = [q for q in store.list_projects() if q.get("parent") == pid and q.get("output")]
    todo = [q for q in kids if not (q.get("publish") or {}).get("video_id")]
    if not todo:
        raise RuntimeError("Belum ada video hasil yang bisa dirender ulang.")
    done, failed = [], []
    for n, q in enumerate(todo, 1):
        tag = f"Video {n}/{len(todo)}"
        before = store.load(q["id"])
        keep = {"stage_status": dict(before["stage_status"]), "current_stage": before["current_stage"]}
        try:
            render.run(q["id"], {"drive_background": True}, lambda m, tag=tag: progress(f"{tag}: {m}"))
            store.update(q["id"], lambda p: (p.update(keep), store.log(p, "Render ulang: logo/overlay diperbarui")))
            done.append(q["id"])
        except Exception as e:  # noqa: BLE001
            from .. import jobs
            if isinstance(e, jobs.JobCancelled):
                raise
            failed.append({"id": q["id"], "error": str(e)[:200]})
    return {"done": done, "failed": failed, "skipped_uploaded": len(kids) - len(todo)}


# ---------------- satu tombol ala 2short.ai ----------------

DURATIONS = {"auto": None, "pendek": (15, 30), "sedang": (30, 60), "panjang": (60, 90)}


def auto_clip(pid: str, params: dict, progress) -> dict:
    """Satu tombol: (unduh) → transkrip → cari momen → buat klip jadi (potong, subtitle, render, thumbnail).
    Pesan progres diawali [n/4] supaya UI bisa menampilkan daftar langkah. Langkah yang sudah selesai
    dilewati (bisa diulang / dilanjutkan bila sempat gagal)."""
    from . import research

    def step(n, label):
        return lambda m: progress(f"[{n}/4] {label}: {m}")

    p = store.load(pid)
    opts = {**(p.get("autoclip") or {}), **{k: v for k, v in params.items() if k != "url"}}
    store.update(pid, lambda q: q.update({"autoclip": {**opts, "status": "running"}}))
    try:
        if params.get("url") and not (p["research"].get("source") or {}).get("path"):
            research.download_source(pid, {"url": params["url"]}, step(1, "Mengunduh video"))
        else:
            progress("[1/4] Video siap")
        p = store.load(pid)
        if not (p["research"].get("source") or {}).get("path"):
            raise RuntimeError("Video belum ada — tempel link atau unggah file.")
        if p["stage_status"].get("research") != "approved":
            store.update(pid, lambda q: store.approve(q, "research"))
        if not (p["script"].get("transcript") or {}).get("segments"):
            transcribe(pid, {}, step(2, "Transkrip"))
        else:
            progress("[2/4] Transkrip sudah ada")
        lim = DURATIONS.get(opts.get("duration") or "auto")
        fp = {"direction": opts.get("direction") or ""}
        if lim:
            fp.update({"lo": lim[0], "hi": lim[1]})
        find_clips(pid, fp, step(3, "Mencari momen viral"))
        p = store.load(pid)
        clips = p["script"]["clips"]
        n = int(opts.get("count") or 0) or len(clips)
        idx = sorted(range(len(clips)), key=lambda i: -(clips[i].get("score") or 0))[:n]
        style = opts.get("sub_style")
        if not style and opts.get("template"):
            t = store.SUB_TEMPLATES.get(opts["template"]) or store.user_templates().get(opts["template"])
            style = {**t["style"], "template": opts["template"]} if t else None
        res = produce_clips(pid, {"indexes": sorted(idx), "crop_mode": opts.get("crop_mode") or "face",
                                  "sub_style": style, "music": bool(opts.get("music")),
                                  "mood": opts.get("mood") or "santai",
                                  "enhance": opts.get("enhance", {})}, step(4, "Membuat klip"))
    except Exception as e:
        store.update(pid, lambda q: q.setdefault("autoclip", {}).update({"status": "error", "error": str(e)[:300]}))
        raise
    store.update(pid, lambda q: q.setdefault("autoclip", {}).update({"status": "done", "error": ""}))
    return res
