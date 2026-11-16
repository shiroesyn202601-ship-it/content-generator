"""Jalur 2 (Clipper): transkrip Whisper -> pilih segmen viral -> potong tanpa memutus kalimat."""
import hashlib
import re
import threading
import time

from .. import config, store
from . import ffmpeg_utils as ff, keys
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


def transcribe_file(path, language: str | None, progress, total: float = 0, hint: str = "") -> dict:
    """Transkrip dengan timestamp kata: Whisper lokal bila bisa, kalau tidak Whisper Groq (lihat stt.py)."""
    from . import stt
    return stt.transcribe(path, language, progress, total, hint)


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
    so = p["research"].get("source") or {}
    # judul + channel + awal deskripsi sebagai "prompt" Whisper → nama orang/merek/istilah ditulis benar
    hint = ". ".join(x for x in [so.get("title"), so.get("channel"), (so.get("description") or "")[:200]] if x)
    tr = transcribe_file(src, lang, progress, so.get("duration") or 0, hint)

    def apply(p):
        p["script"]["transcript"] = tr
        # transkrip baru → hasil sunting lama tidak berlaku (kamus kreator tetap dipakai)
        for k in ("transcript_raw", "transcript_fixes", "proof_cache", "context", "proof_reject", "transcript_rejected"):
            p["script"].pop(k, None)
        store.touch_stage(p, "script")
        store.log(p, f"Whisper: {len(tr['segments'])} segmen")
    store.update(pid, apply)
    out = {"segments": len(tr["segments"])}
    if params.get("proofread", True):  # langsung disunting AI dengan konteks percakapan sebelum dicari momennya
        out.update(proofread_safe(pid, progress))
    return out


def proofread_safe(pid: str, progress) -> dict:
    """Sunting transkrip; gagal (kuota AI habis dll.) tidak menghentikan Clipper — transkrip mentah tetap dipakai."""
    from . import proofread
    try:
        return proofread.run(pid, {}, progress)
    except llm.LLMError as e:
        progress(f"Sunting transkrip dilewati ({str(e)[:100]}) — memakai transkrip Whisper apa adanya")
        return {"fixes": 0, "failed": 1}


def dedupe_segments(segments: list[dict]) -> list[dict]:
    """Transkrip lama (sebelum perbaikan stt._assign_words) bisa memuat kata yang sama di dua segmen
    bertetangga → subtitle & kalimat dobel. Kata dengan waktu mulai yang sudah terpakai dibuang."""
    seen, out = set(), []
    for s in segments:
        ws = []
        for w in s.get("words", []):
            k = (round(w["start"], 2), w["text"].strip().lower())
            if k not in seen:
                seen.add(k)
                ws.append(w)
        out.append({**s, "words": ws})
    return out


def _all_words(segments: list[dict]) -> list[dict]:
    return [w for s in dedupe_segments(segments) for w in s.get("words", [])]


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
    words = [w for s in dedupe_segments(segments) for w in _punct_words(s)]
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


CONNECTIVE = re.compile(r"^(jadi|nah|terus|trus|makanya|karena itu|soalnya|tapi|dan|lalu|kemudian|habis itu|"
                        r"so|and|but|because|then)\b", re.I)


def _fit_range(units: list[dict], a: int, b: int, lo: float, hi: float) -> tuple[int, int]:
    """Perlebar/persempit rentang kalimat supaya durasi klip masuk batas format."""
    while units[b]["end"] - units[a]["start"] < lo and b + 1 < len(units) \
            and units[b + 1]["end"] - units[a]["start"] <= hi:
        b += 1
    while units[b]["end"] - units[a]["start"] > hi and b > a:
        # terlalu panjang: kalimat pembuka yang cuma kata sambung ("jadi…", "nah…") dibuang lebih dulu,
        # supaya punchline di akhir tidak ikut terpotong
        if a < b and CONNECTIVE.match(units[a]["text"].strip()) \
                and units[b]["end"] - units[a + 1]["start"] >= lo * 0.75:
            a += 1
        else:
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


def _heat_at(heat: list[dict], st: float, en: float) -> float:
    """Rata-rata nilai heatmap YouTube (0..1) di rentang [st, en]; 0 bila tidak ada data."""
    vals = [h["v"] for h in heat if h["e"] > st and h["s"] < en]
    return sum(vals) / len(vals) if vals else 0.0


def _loudness(p: dict, progress) -> list[float]:
    """RMS suara per 0.5 dtk (dB) dari seluruh video — satu kali decode audio, disimpan di source/.stt/."""
    import json
    import subprocess
    src = store.source_file(p)
    cache = src.parent / ".stt" / f"loud_{src.stem}_{src.stat().st_size}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    progress("Mengukur energi suara (tawa, teriakan, momen heboh)…")
    try:
        import numpy as np
        raw = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "8000",
                              "-f", "s16le", "-"], capture_output=True, timeout=900).stdout
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        n = len(a) // 4000
        if not n:
            return []
        rms = np.sqrt((a[:n * 4000].reshape(n, 4000) ** 2).mean(axis=1) + 1e-9)
        out = [round(float(x), 1) for x in 20 * np.log10(rms)]
    except Exception:  # noqa: BLE001 — sinyal tambahan; gagal = tidak dipakai
        return []
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def _mark_signals(p: dict, units: list[dict], progress) -> list[dict]:
    """Tandai kalimat dengan sinyal nyata untuk AI: 🔥 = 20% teratas heatmap "paling sering diputar ulang"
    YouTube, 🔊 = suara jauh lebih keras dari biasanya. Mengembalikan heatmap untuk bonus skor."""
    heat = (p["research"].get("source") or {}).get("heatmap") or []
    if heat:
        hv = [_heat_at(heat, u["start"], u["end"]) for u in units]
        cut = sorted(hv)[int(len(hv) * 0.8)] if hv else 1
        for u, v in zip(units, hv):
            if v >= cut and v > 0.3:
                u["sig"] = u.get("sig", "") + " 🔥"
    loud = _loudness(p, progress)
    if len(loud) > 20:
        import statistics
        speech = [x for x in loud if x > -50]  # abaikan hening
        if len(speech) > 20:
            mu, sd = statistics.mean(speech), statistics.pstdev(speech) or 1.0
            for u in units:  # rata-rata jauh di atas biasa, atau satu lonjakan tajam (tawa/teriak)
                seg = loud[int(u["start"] * 2):int(u["end"] * 2) + 1]
                if seg and ((statistics.mean(seg) - mu) / sd >= 0.9
                            or ((max(seg) - mu) / sd >= 1.5 and statistics.mean(seg) > mu)):
                    u["sig"] = u.get("sig", "") + " 🔊"
    return heat


def _rerank(clips: list[dict], units: list[dict], title: str, lang: str, direction: str, heat: list[dict],
            progress, context: dict | None = None) -> list[dict]:
    """Babak final: satu model membandingkan semua kandidat berdampingan dengan teks klip UTUH.
    Skor akhir = 60% rerank + 40% skor penyusunan − penalti kualitas (+ bonus maks 10 dari heatmap YouTube)."""
    if len(clips) >= 3:
        progress(f"Babak final: AI membandingkan {len(clips)} kandidat berdampingan…")
        sys_, usr = prompts.rerank_clips(clips, units, title, lang, direction, context)
        try:
            rk = llm.chat_json(sys_, usr, temperature=0.2, effort="medium").get("ranking") or []
        except llm.LLMError as e:
            progress(f"Babak final dilewati ({str(e)[:80]}) — memakai skor penyusunan")
            rk = []
        got = {int(_num(r.get("n"), -1)): r for r in rk if isinstance(r, dict)}
        for i, c in enumerate(clips):
            r = got.get(i)
            if r is not None:
                c["window_score"] = c["score"]
                c["score"] = int(round(0.6 * max(0, min(100, _num(r.get("score")))) + 0.4 * c["score"]))
                if r.get("why"):
                    c["why"] = str(r["why"])[:200]
    for c in clips:
        c["score"] = max(0, c["score"] - c.get("penalty", 0))
        if heat:
            c["heat"] = round(_heat_at(heat, c["start"], c["end"]), 2)
            c["score"] = min(100, c["score"] + int(round(10 * c["heat"])))
    return sorted(clips, key=lambda c: -c["score"])


# ---------------- gerbang kualitas (kode, tanpa AI) ----------------

# Kata terakhir yang menandakan kalimat/cerita belum selesai
DANGLING = re.compile(r"\b(tapi|terus|trus|dan|kalo|kalau|yang|karena|soalnya|jadi|cuman|cuma|atau|sama|nah|kayak|"
                      r"misalnya|abis itu|habis itu|but|and|so|because|or)\W*$", re.I)


# Promo/sponsor/ajakan & pembuka-penutup acara: bukan momen, walau AI kadang menganggapnya "jelas & spesifik"
PROMO = re.compile(r"\b(hashtag|subscribe|follow|giveaway|kode promo|link di bio|disponsori|sponsor(ed)?|juara 1|"
                   r"tag at|klik link|like dan share|nyalakan lonceng|season \d|episode (kali )?ini)\b", re.I)


def _tokens(text: str) -> list[str]:
    return [t for t in (_norm(x) for x in text.split()) if t]


def _quality(units: list[dict], a: int, b: int) -> tuple[int, list[str]]:
    """Penalti (0..100) + catatan untuk klip units[a..b]: transkrip berulang/halusinasi, bicara jarang
    (musik/hening), akhir menggantung."""
    toks = [t for u in units[a:b + 1] for t in _tokens(u["text"])]
    dur = max(1.0, units[b]["end"] - units[a]["start"])
    pen, notes = 0, []
    if toks:
        uniq = len(set(toks)) / len(toks)
        run = best = 1
        for x, y in zip(toks, toks[1:]):
            run = run + 1 if x == y else 1
            best = max(best, run)
        if uniq < 0.38 or best >= 4:
            pen += 25
            notes.append("transkrip berulang")
    wpm = len(toks) / dur * 60
    if wpm < 70:
        pen += 30 if wpm < 45 else 12
        notes.append("sedikit ucapan")
    last = units[b]["text"].strip()
    if DANGLING.search(last) or last.endswith((",", "-")):
        pen += 12
        notes.append("akhir menggantung")
    elif last.endswith("?"):
        pen += 10
        notes.append("berakhir di pertanyaan")
    promo = sum(1 for u in units[a:b + 1] if PROMO.search(u["text"]))
    if promo >= 2:
        pen += 35
        notes.append("promo/ajakan")
    return pen, notes


def _tidy_end(units: list[dict], a: int, b: int, hi: float) -> int:
    """Akhir klip menggantung ("…tapi", "…terus") atau pertanyaan yang belum dijawab → sambung 1-2 kalimat
    bila masih muat durasi."""
    for _ in range(2):
        last = units[b]["text"].strip()
        if not (DANGLING.search(last) or last.endswith("?")) or b + 1 >= len(units):
            break
        if units[b + 1]["end"] - units[a]["start"] > hi:
            break
        b += 1
    return b


# ---------------- tahap 1: pindai puncak · tahap 2: susun klip di sekitar puncak ----------------

PEAK_GAP = 25.0     # dua puncak berjarak < ini (dtk) = momen yang sama → ambil yang terkuat
BUILD_BEFORE, BUILD_AFTER = 75.0, 60.0  # konteks transkrip yang dilihat penyusun klip di sekitar puncak


def find_clips(pid: str, params: dict, progress) -> dict:
    """Cari momen terbaik. Dulu satu prompt besar per ±15 menit transkrip meminta AI langsung memilih rentang klip
    + menilai sendiri → hasilnya cenderung "topik" (daftar tempat makan, obrolan pembuka) dengan skor seragam 70-85,
    dan akhir klip menggantung. Kini 3 tahap:
      1. Pindai puncak (paralel per bagian): AI hanya menandai KALIMAT PUNCAK (punchline, fakta/angka mengejutkan,
         opini tajam, pengakuan, klimaks cerita, jawaban/tips) + inti momennya dalam satu kalimat.
      2. Susun klip (paralel per puncak): AI melihat ±75 dtk sebelum & 60 dtk sesudah puncak, memilih awal (setup yang
         dibutuhkan penonton) & akhir (tepat setelah payoff + reaksinya), atau menolak bila tanpa inti yang jelas.
      3. Babak final: semua klip dibandingkan berdampingan dengan teks utuh. Kode menambah penalti untuk transkrip
         berulang, bicara jarang, dan akhir menggantung."""
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
    heat = _mark_signals(p, units, progress)
    dur = units[-1]["end"] - units[0]["start"]
    want = max(3, min(10, int(dur // 90) + 2))  # jumlah klip wajar sesuai panjang video
    ctx = p["script"].get("context") or {}
    direction = params.get("direction") if params.get("direction") is not None else p["script"].get("direction") or ""
    provider = params.get("provider")
    lanes = len(_lanes()) if keys.has("groq") else 1
    total = sum(len(u["text"]) + 20 for u in units)
    wins = _windows(units, int(min(WINDOW_MAX, max(WINDOW_CHARS, total * 1.15 / lanes))))
    per_win = max(4, min(10, -(-want * 3 // len(wins))))

    def ask(sys_, usr, model, prov, effort, temperature):
        """model = jalur Groq; prov = layanan cadangan (Groq dilewati)."""
        return llm.chat_json(sys_, usr, prov, temperature=temperature, groq_model=model, effort=effort,
                             fallback=False if (model or prov) else True, only_model=bool(model),
                             max_wait=3 if model and lanes > 1 else None, skip=("groq",) if prov else ())

    # ---- tahap 1: puncak
    def peak_job(ws, we):
        def run(model, prov):
            sys_, usr = prompts.clip_peaks(units[ws:we], p["language"], title, offset=ws, count=per_win,
                                           direction=direction, context=ctx)
            got = ask(sys_, usr, model, prov, "low", 0.3)
            return [x for x in got.get("peaks") or [] if isinstance(x, dict)]
        txt = "|".join(u["text"] + u.get("sig", "") for u in units[ws:we])
        key = hashlib.sha1(f"peak2|{per_win}|{direction}|{bool(ctx.get('summary'))}|{txt}".encode()).hexdigest()[:16]
        return key, run

    raw, failed = _fan_out(pid, "clip_cache", [peak_job(ws, we) for ws, we in wins], provider, progress,
                           "Tahap 1/3 · memindai momen puncak")
    if failed and not raw:
        raise RuntimeError(f"Semua {len(wins)} bagian gagal dinilai AI — kuota gratis sedang habis. "
                           f"Tunggu 1-2 menit lalu klik lagi (bagian yang sudah berhasil tidak diulang).\n\n{failed[0]}")
    peaks = []
    for x in raw:
        n = int(_num(x.get("n"), -1))
        if 0 <= n < len(units):
            st = max(1.0, min(10.0, _num(x.get("strength"), 5)))
            if "🔥" in units[n].get("sig", "") or "🔊" in units[n].get("sig", ""):
                st += 0.5
            peaks.append({"n": n, "strength": st, "type": str(x.get("type") or "")[:20],
                          "point": str(x.get("point") or "")[:200]})
    peaks.sort(key=lambda x: -x["strength"])
    chosen = []
    for pk in peaks:  # puncak yang berdekatan = satu momen
        if all(abs(units[pk["n"]]["start"] - units[o["n"]]["start"]) >= PEAK_GAP for o in chosen):
            chosen.append(pk)
    chosen = chosen[:want * 2 + 2]
    if not chosen:
        raise RuntimeError("AI tidak menemukan momen puncak di video ini. Coba beri Arahan AI (mis. topik yang dicari).")

    # ---- tahap 2: susun klip di sekitar tiap puncak
    def build_job(pk):
        t = units[pk["n"]]["start"]
        a = next((i for i, u in enumerate(units) if u["end"] >= t - BUILD_BEFORE), 0)
        b = max(i for i, u in enumerate(units) if u["start"] <= t + BUILD_AFTER)

        def run(model, prov):
            sys_, usr = prompts.clip_build(units[a:b + 1], a, pk, p["language"], title, (lo, hi), direction, ctx)
            got = ask(sys_, usr, model, prov, "medium", 0.2)
            return [{**got, "peak": pk["n"], "peak_type": pk["type"], "peak_point": pk["point"]}] \
                if isinstance(got, dict) else []
        txt = "|".join(u["text"] for u in units[a:b + 1])
        return hashlib.sha1(f"build2|{pk['n']}|{lo}|{hi}|{direction}|{txt}".encode()).hexdigest()[:16], run

    built, failed2 = _fan_out(pid, "build_cache", [build_job(pk) for pk in chosen], provider, progress,
                              f"Tahap 2/3 · menyusun {len(chosen)} momen jadi klip")
    progress("Merapikan kandidat klip (batas kalimat, durasi, kualitas transkrip)…")
    cands, rejected = [], 0
    for c in built:
        if c.get("ok") is False:
            rejected += 1
            continue
        pk = int(c["peak"])
        a = int(max(0, min(_num(c.get("start_unit"), pk), pk)))
        b = int(max(pk, min(_num(c.get("end_unit"), pk), len(units) - 1)))
        a, b = _fit_range(units, a, b, lo, hi)
        b = _tidy_end(units, a, b, hi)
        st, en = _pad(words, units[a]["start"], units[b]["end"])
        if not (lo * 0.75 <= en - st <= hi + 1):
            continue
        sc = c.get("scores") if isinstance(c.get("scores"), dict) else {}
        subs = {k: int(max(0, min(100, _num(sc.get(k), 50)))) for k in WEIGHTS}
        pen, notes = _quality(units, a, b)
        point = str(c.get("point") or c.get("peak_point") or "")
        cands.append({"start": st, "end": en, "title": str(c.get("title") or "")[:80],
                      "hook": str(c.get("hook") or ""), "point": point, "type": c.get("peak_type") or "",
                      "score": int(round(sum(subs[k] * w for k, w in WEIGHTS.items()))),
                      "scores": subs, "penalty": pen, "flags": notes,
                      "keywords": [str(x) for x in c.get("keywords") or [] if x]})
    cands.sort(key=lambda c: -(c["score"] - c["penalty"]))
    clips = []
    for c in cands:  # klip yang tumpang-tindih ≥25% dengan klip berskor lebih tinggi dibuang
        if all(_overlap(c, o) < 0.25 for o in clips):
            clips.append(c)
    progress(f"Tahap 3/3 · membandingkan {min(len(clips), max(want + 4, 12))} klip terbaik…")
    clips = _rerank(clips[:max(want + 4, 12)], units, title, p["language"], direction, heat, progress, ctx)
    for c in clips:  # alasan yang tampil di UI: inti momen + penilaian babak final
        c["reason"] = " — ".join(x for x in (c.get("point"), c.get("why")) if x)[:300]
    # ID stabil (dari rentang waktu), supaya "Cari Momen" ulang tidak menukar klip yang sudah jadi video
    # Klip lama dengan rentang (hampir) sama memakai ID lamanya → video hasil yang sudah ada tetap tertaut.
    old = p["script"].get("clips") or []

    def cid(c):
        same = next((o for o in old if abs(o["start"] - c["start"]) < 0.6 and abs(o["end"] - c["end"]) < 0.6), None)
        return same["id"] if same else "c" + hashlib.sha1(f"{c['start']:.1f}-{c['end']:.1f}".encode()).hexdigest()[:8]
    clips = [{**c, "id": cid(c)} for c in clips[:want]]
    if not clips:
        raise RuntimeError(f"AI tidak menemukan klip berdurasi {lo}-{hi} dtk yang punya inti jelas "
                           f"({rejected} momen ditolak). Coba durasi lain atau beri Arahan AI.")
    fails = len(failed) + len(failed2)
    note = (f"{fails} bagian/momen belum dinilai (kuota AI gratis habis). Klik \"Cari Momen Viral\" lagi 1-2 menit "
            "kemudian untuk melengkapi — yang sudah dinilai tidak diulang." if fails else "")

    def apply(p):
        p["script"]["clips"] = clips
        p["script"]["selected_clip"] = None
        p["script"]["clip_note"] = note
        if params.get("direction") is not None:
            p["script"]["direction"] = params["direction"]
        store.touch_stage(p, "script")
        store.log(p, f"AI: {len(chosen)} momen puncak → {len(clips)} klip ({rejected} ditolak) dari {len(units)} kalimat"
                     + (f", {fails} gagal" if fails else ""))
    store.update(pid, apply)
    return {"count": len(clips), "peaks": len(chosen), "rejected": rejected, "failed_parts": fails,
            "parts": len(wins)}


def _lanes() -> list[str]:
    """Jalur paralel AI: tiap model Groq × jumlah key (tiap akun = kuota token/menit sendiri),
    maks 9 jalur supaya tidak membanjiri server."""
    n = max(1, len(keys.pool("groq")))
    return [m for _ in range(n) for m in llm.groq_models()][:9]


def _fan_out(pid: str, cache_name: str, tasks: list[tuple], provider, progress, label: str):
    """Kerjakan banyak permintaan AI kecil PARALEL di jalur-jalur Groq (tiap model × key punya kuota token/menit
    sendiri). tasks = [(cache_key, run(model, provider) -> list)]. Hasil per tugas disimpan di
    p.script[cache_name] → bila sebagian gagal (kuota), klik ulang hanya mengerjakan yang belum.
    Jalur yang kena batas mengembalikan tugasnya ke antrean (diambil jalur lain) lalu istirahat sampai pulih.
    Tugas yang gagal di semua jalur Groq dicoba sekali lewat layanan lain (Gemini, Cerebras, …).
    Mengembalikan (gabungan hasil, [pesan error])."""
    import queue
    from concurrent.futures import ThreadPoolExecutor

    from .. import jobs

    cache = dict(store.load(pid)["script"].get(cache_name) or {})
    out = [x for k, _ in tasks for x in cache.get(k, [])]
    todo = [(i, k, fn) for i, (k, fn) in enumerate(tasks) if k not in cache]
    keep = {k for k, _ in tasks}

    def save(k, got):
        cache[k] = got
        store.update(pid, lambda pp: pp["script"].setdefault(cache_name, {}).__setitem__(k, got))

    errors: list[str] = []
    if todo:
        use_groq = (provider or config.LLM_PROVIDER).lower() == "groq" or (
            not provider and "groq" in llm.chain() and keys.has("groq"))
        models = _lanes() if use_groq and keys.has("groq") else [None]
        q: "queue.Queue" = queue.Queue()
        for t in todo:
            q.put(t)
        pending, done, lock = [len(todo)], [len(tasks) - len(todo)], threading.Lock()
        tries: dict[int, int] = {}
        parent_progress, parent_jid = getattr(jobs._local, "progress", None), getattr(jobs._local, "jid", None)

        def lane(model):
            jobs._local.progress, jobs._local.jid = parent_progress, parent_jid  # bar status & tombol Batalkan
            while True:
                with lock:
                    if pending[0] <= 0:
                        return
                try:
                    i, k, fn = q.get(timeout=1)
                except queue.Empty:
                    continue  # tugas sedang dikerjakan jalur lain (bisa dikembalikan bila kena batas)
                try:
                    got = fn(model, None if model else provider)
                except llm.RateLimited as e:
                    with lock:
                        tries[i] = tries.get(i, 0) + 1
                        give_up = tries[i] > 2 * len(models)
                        if give_up:  # kuota harian habis / semua jalur terus penuh → serahkan ke layanan lain
                            errors.append(f"{label} #{i + 1}: kuota Groq habis")
                            pending[0] -= 1
                    if give_up:
                        continue
                    q.put((i, k, fn))  # oper ke jalur lain
                    progress(f"{label}: {done[0]}/{len(tasks)} selesai — kuota per menit "
                             f"{e.model.split('/')[-1]} habis, dilanjutkan ±{e.wait:.0f} dtk lagi…")
                    for _ in range(int(e.wait) + 1):  # istirahat sampai kuota model ini pulih
                        time.sleep(1)
                        with lock:
                            if pending[0] <= 0:
                                return
                    continue
                except Exception as e:  # noqa: BLE001 — satu tugas gagal tidak menggagalkan semua
                    if isinstance(e, jobs.JobCancelled):
                        with lock:
                            pending[0] = 0
                        raise
                    with lock:
                        errors.append(f"{label} #{i + 1}: {str(e)[:300]}")
                        pending[0] -= 1
                    continue
                with lock:
                    out.extend(got)
                    done[0] += 1
                    pending[0] -= 1
                    n = done[0]
                save(k, got)
                progress(f"{label}: {n}/{len(tasks)} selesai…")

        progress(f"{label}: {len(todo)} permintaan AI"
                 + (f" paralel di {len(models)} jalur Groq…" if len(models) > 1 else "…"))
        with ThreadPoolExecutor(max_workers=len(models)) as ex:
            for f in [ex.submit(lane, m) for m in models]:
                f.result()
        # gagal di semua jalur Groq → coba lewat layanan lain supaya Clipper tidak bergantung pada satu AI
        others = [x for x in llm.chain() if x != "groq"]
        retry = [(i, k, fn) for i, k, fn in todo if k not in cache]
        if errors and models != [None] and others and retry:
            progress(f"{len(retry)} permintaan gagal di Groq → dicoba dengan {llm.PROVIDER_LABEL[others[0]]}…")
            errors.clear()
            for i, k, fn in retry:
                try:
                    got = fn(None, others[0])
                except llm.LLMError as e:
                    errors.append(f"{label} #{i + 1}: {str(e)[:300]}")
                    continue
                out.extend(got)
                save(k, got)
    # buang cache yang sudah tidak dipakai (transkrip / arahan berubah)
    store.update(pid, lambda pp: pp["script"].__setitem__(
        cache_name, {k: v for k, v in (pp["script"].get(cache_name) or {}).items() if k in keep}))
    return out, errors


def cut_clip(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    clip = p["script"]["clips"][p["script"]["selected_clip"]]
    mode = params.get("crop_mode") or p["assets"].get("crop_mode") or "center"
    w, h = config.RESOLUTIONS[p["format"]]
    (d / "assets").mkdir(exist_ok=True)
    graph = f"[0:v]{ff.fit_filter(w, h, mode if mode != 'face' else 'blur')},fps={config.FPS},format=yuv420p[v]"
    src = store.source_file(p)
    vin = ["-ss", f"{clip['start']:.3f}", "-to", f"{clip['end']:.3f}", "-i", str(src)]
    amap = "0:a?"
    reframe_info = None
    if mode == "face":
        from . import reframe
        words = [x for s in dedupe_segments((p["script"].get("transcript") or {}).get("segments") or [])
                 for x in s.get("words") or []]
        progress("Auto-reframe: mencari wajah & siapa yang sedang bicara…")
        pl, reframe_info = reframe.track(src, clip["start"], clip["end"], w, h, words, progress)
        if pl:
            graph = reframe.filter_graph(pl, w, h, config.FPS)
            seg_in = reframe.segment_inputs(pl, src, clip["start"], config.FPS)
            amap = f"{len(pl['segments'])}:a?"  # audio dari input terakhir (rentang klip utuh)
            vin = seg_in + vin
            st = reframe_info["stats"]
            progress(f"Auto-reframe: {reframe_info['shots']} shot, {st.get('switches', 0)} kali pindah pembicara"
                     + (f", layar dibagi {st['split']:.0f} dtk" if st.get("split") else "")
                     + (f", video utuh {st['fit']:.0f} dtk" if st.get("fit") else ""))
        else:
            progress("Auto-reframe: tidak ada wajah terdeteksi — memakai mode latar blur")
    progress(f"Memotong {clip['start']:.1f}s - {clip['end']:.1f}s ({mode})...")
    # file antara → encoder GPU bila ada (dulu libx264 di CPU, berebut CPU dengan klip lain yang berjalan bersamaan)
    ff.run_encode([*vin, "-filter_complex", graph, "-map", "[v]", "-map", amap],
                  ["-c:a", "aac", "-b:a", "192k", "-shortest", "assets/clip.mp4"], cwd=d, intermediate=True)

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
    p["script"] = {k: v for k, v in src["script"].items() if k not in ("transcript_raw", "proof_cache", "transcript_fixes",
                                                                  "transcript_rejected")}
    p["script"].update({"selected_clip": idx, "clip_cache": {}, "build_cache": {}})
    tr = src["script"].get("transcript") or {}
    if tr.get("segments"):  # transkrip penuh video 1 jam ±1 MB → anak cukup ±90 dtk di sekitar klipnya
        lo_t, hi_t = clip["start"] - 90, clip["end"] + 90
        p["script"]["transcript"] = {**tr, "segments": dedupe_segments(
            [g for g in tr["segments"] if g["end"] >= lo_t and g["start"] <= hi_t])}
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
    Beberapa klip dikerjakan BERSAMAAN (config.CLIP_WORKERS, default 3): sementara satu klip menunggu AI/
    jaringan, klip lain memakai CPU/GPU. Satu klip gagal tidak menghentikan yang lain."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from .. import jobs
    from . import audio_lib, render, subtitles, thumbs, usage

    src = store.load(pid)
    clips = src["script"].get("clips") or []
    idx = [int(i) for i in params.get("indexes") or [] if 0 <= int(i) < len(clips)]
    if not idx:
        raise RuntimeError("Centang minimal satu klip.")
    crop = params.get("crop_mode") or src["assets"].get("crop_mode") or "face"
    style = params.get("sub_style") or src["subtitles"].get("style")
    music = bool(params.get("music"))
    hq = params.get("hq_subs", True) is not False
    from . import clipstt, enhance
    eopts = enhance.options(params.get("enhance")) if params.get("enhance") is not False else None

    def save_src(q):
        q["assets"]["crop_mode"] = crop
        if style:
            q["subtitles"]["style"] = style
    store.update(pid, save_src)
    results: dict[int, tuple] = {}
    lock = threading.Lock()
    workers = max(1, min(int(params.get("workers") or config.CLIP_WORKERS), len(idx)))
    done = [0]
    if workers > 1:
        progress(f"Membuat {len(idx)} video, {workers} sekaligus…")

    def one(n, i):
        clip = clips[i]
        tag = f"Video {n}/{len(idx)}"
        progress(f"{tag}: menyiapkan proyek '{clip.get('title', '')[:40]}'…")
        with lock:  # fork membaca & menulis proyek sumber — satu per satu
            p = fork_clip(pid, i)
        qid = p["id"]
        usage.link(n, qid)
        step = "persiapan"

        def sub(msg, tag=tag):
            progress(f"{tag}: {msg}")
        try:
            store.update(qid, lambda q: store.approve(q, "script"))
            if hq:
                step = "transkrip presisi"
                try:
                    with usage.step("Transkrip presisi (subtitle)", qid):
                        clipstt.refine(qid, {}, sub)
                except Exception as e:  # noqa: BLE001 — gagal: tetap pakai transkrip Whisper video penuh
                    from .. import jobs
                    if isinstance(e, jobs.JobCancelled):
                        raise
                    sub(f"Transkrip presisi dilewati: {str(e)[:120]}")
            step = "potong"
            with usage.step("Potong & auto-reframe", qid):
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
            res = ("ok", {"id": qid, "title": p["title"], "score": clip.get("score")})
        except Exception as e:  # noqa: BLE001
            if isinstance(e, jobs.JobCancelled):
                raise
            res = ("fail", {"id": qid, "title": p["title"], "step": step, "error": str(e)[:200]})
            store.update(qid, lambda q, e=e, step=step: store.log(q, f"Produksi massal berhenti di tahap {step}: {e}"))
        with lock:
            done[0] += 1
            progress(f"{done[0]}/{len(idx)} video selesai")
        return res

    task = jobs.bind(usage.bind(usage.current(), one))
    cancelled = None
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="klip") as ex:
        futs = {ex.submit(task, n, i): n for n, i in enumerate(idx, 1)}
        for f, n in futs.items():
            try:
                results[n] = f.result()
            except jobs.JobCancelled as e:
                cancelled = e
            except Exception as e:  # noqa: BLE001 — galat tak terduga di luar try per klip
                results[n] = ("fail", {"id": "", "title": clips[idx[n - 1]].get("title", ""), "step": "?", "error": str(e)[:200]})
    if cancelled:
        raise cancelled
    created = [r[1] for n, r in sorted(results.items()) if r[0] == "ok"]
    failed = [r[1] for n, r in sorted(results.items()) if r[0] == "fail"]
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


def resubtitle(pid: str, params: dict, progress) -> dict:
    """Perbaiki subtitle video hasil yang sudah jadi: transkrip presisi per klip (Gemini + Whisper, lihat clipstt)
    → nilai tambah dirakit ulang (rencana lama dipakai, tanpa AI) → subtitle → render ulang. Dipanggil pada proyek
    sumber (semua video hasil yang belum diunggah) atau pada satu video hasil. Gaya subtitle, thumbnail & jadwal
    tidak berubah; video yang sudah terunggah ke YouTube dilewati."""
    from . import clipstt, enhance, render, subtitles
    me = store.load(pid)
    if me.get("parent"):
        todo = [{"id": pid}]
        skipped = 0
    else:
        kids = [q for q in store.list_projects() if q.get("parent") == pid and q.get("output")]
        todo = [q for q in kids if not (q.get("publish") or {}).get("video_id")]
        skipped = len(kids) - len(todo)
    if not todo:
        raise RuntimeError("Belum ada video hasil yang bisa diperbaiki.")
    done, failed = [], []
    for n, q in enumerate(todo, 1):
        tag = f"Video {n}/{len(todo)}"

        def sub(m, tag=tag):
            progress(f"{tag}: {m}")
        before = store.load(q["id"])
        keep = {"stage_status": dict(before["stage_status"]), "current_stage": before["current_stage"]}
        style = dict(before["subtitles"].get("style") or {})
        try:
            res = clipstt.refine(q["id"], {}, sub)
            if (before.get("enhance") or {}).get("path"):
                # transkrip berubah → nomor kalimat rencana lama tidak berlaku; rencana (B-roll dll.) dibuat ulang
                enhance.run(q["id"], {"opts": before["enhance"].get("opts"), "seed": q["id"], "reuse_plan": False}, sub)
            store.update(q["id"], lambda p, st=style: p["subtitles"].update({"style": st}))
            subtitles.build(q["id"], {}, sub)
            render.run(q["id"], {"drive_background": True}, sub)
            store.update(q["id"], lambda p: (p.update(keep), store.log(p, "Subtitle diperbaiki (transkrip presisi)")))
            done.append({"id": q["id"], "words": res["words"], "engine": res["engine"]})
        except Exception as e:  # noqa: BLE001
            from .. import jobs
            if isinstance(e, jobs.JobCancelled):
                raise
            failed.append({"id": q["id"], "error": str(e)[:200]})
            store.update(q["id"], lambda p, e=e: store.log(p, f"Perbaiki subtitle gagal: {e}"))
    return {"done": done, "failed": failed, "skipped_uploaded": skipped}


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
    opts = {**(p.get("autoclip") or {}), **{k: v for k, v in params.items() if k not in ("url", "_retry")}}
    # "Buat Klip" dari UI membawa opsi (durasi/jumlah/template…) = rencana baru. "Jalankan ulang" job yang terputus
    # (params kosong / hanya url & _retry) = LANJUTAN: momen terpilih sebelumnya diselesaikan, tidak ditambah.
    if not params.get("_retry") and set(params) - {"url", "_retry"}:
        opts.pop("picked", None)
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
        tr = p["script"].get("transcript") or {}
        if not tr.get("segments"):
            transcribe(pid, {}, step(2, "Transkrip"))
        elif not tr.get("proofread") or tr["proofread"].get("failed"):
            proofread_safe(pid, step(2, "Menyunting transkrip"))
        else:
            progress("[2/4] Transkrip sudah ada & sudah disunting")
        # Klip yang sudah jadi video = anak yang SUDAH punya hasil render. Anak tanpa render (terputus di tengah,
        # mis. server restart) dihapus lalu dibuat ulang — kalau tidak, momen itu dianggap selesai selamanya.
        kids = [q for q in store.list_projects() if q.get("parent") == pid]
        done_ids = {q.get("clip_id") for q in kids if q.get("output")}
        broken = [q for q in kids if not q.get("output")]
        p = store.load(pid)
        plan = opts.get("picked") or []  # opts, bukan p lama: "Buat Klip" baru sudah membuang rencana sebelumnya
        if plan and p["script"].get("clips"):
            # percobaan ulang: lanjutkan rencana awal — tidak mencari momen lagi & tidak menambah klip baru
            progress("[3/4] Momen sudah dipilih sebelumnya — melanjutkan klip yang belum jadi")
        else:
            lim = DURATIONS.get(opts.get("duration") or "auto")
            fp = {"direction": opts.get("direction") or ""}
            if lim:
                fp.update({"lo": lim[0], "hi": lim[1]})
            find_clips(pid, fp, step(3, "Mencari momen viral"))
        p = store.load(pid)
        clips = p["script"]["clips"]
        if not plan:
            n = int(opts.get("count") or 0) or len(clips)
            plan = [clips[i]["id"] for i in sorted(range(len(clips)), key=lambda i: -(clips[i].get("score") or 0))
                    if clips[i].get("id") not in done_ids][:n]
            store.update(pid, lambda q: q.setdefault("autoclip", {}).update({"picked": plan}))
        idx = [i for i, c in enumerate(clips) if c.get("id") in plan and c.get("id") not in done_ids]
        for q in broken:
            if q.get("clip_id") in {clips[i]["id"] for i in idx}:
                store.remove_tree(store.project_dir(q["id"]))
        if not idx:
            progress("[4/4] Semua klip terbaik sudah jadi video")
            store.update(pid, lambda q: q.setdefault("autoclip", {}).update({"status": "done", "error": ""}))
            return {"created": [], "failed": []}
        style = opts.get("sub_style")
        if not style and opts.get("template"):
            t = store.SUB_TEMPLATES.get(opts["template"]) or store.user_templates().get(opts["template"])
            style = {**t["style"], "template": opts["template"]} if t else None
        res = produce_clips(pid, {"indexes": sorted(idx), "crop_mode": opts.get("crop_mode") or "face",
                                  "sub_style": style, "music": bool(opts.get("music")),
                                  "mood": opts.get("mood") or "santai",
                                  "enhance": opts.get("enhance", {}),
                                  "hq_subs": opts.get("hq_subs", True)}, step(4, "Membuat klip"))
    except Exception as e:
        store.update(pid, lambda q: q.setdefault("autoclip", {}).update({"status": "error", "error": str(e)[:300]}))
        raise
    store.update(pid, lambda q: q.setdefault("autoclip", {}).update({"status": "done", "error": ""}))
    return res
