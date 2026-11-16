"""Transkrip presisi per klip (Clipper) — subtitle yang tidak bolong, juga untuk bahasa Jawa/campuran.

Masalah (diuji 16 Nov 2026 di podcast berbahasa Jawa-Indonesia dengan musik latar):
- Whisper (Groq) atas video 30 menit sekaligus hanya ±80-100 kata/menit (obrolan nyata 150-200): bagian yang
  tumpang-tindih musik diganti lirik lagu / dilewati, bahasa Jawa ditulis ngawur.
- Whisper potongan pendek sedikit lebih lengkap; kode bahasa Jawa di Groq = "jv" (bukan "jw"), hasilnya mirip.
- Gemini (audio langsung) menulis ±120-150 kata/menit, bahasa Jawa tetap Jawa, lirik lagu diabaikan, dan memberi
  label pembicara — tetapi waktunya hanya per baris & lambat (±30 dtk per menit audio).

Maka per klip (±60-90 dtk, sekali panggil): Gemini menulis TEKS + baris berwaktu + pembicara, Whisper potongan
pendek memberi WAKTU per kata. Kata Gemini diselaraskan ke kata Whisper (difflib); kata yang tidak cocok diberi waktu
dari sela patokan terdekat / perkiraan baris Gemini. Hasil menggantikan segmen transkrip di rentang klip, sehingga
potong, auto-reframe, nilai tambah, dan subtitle otomatis memakainya. Gemini gagal (kuota) → Whisper potongan pendek.
"""
import base64
import difflib
import json
import re
import tempfile
from pathlib import Path

from .. import config, store
from . import ffmpeg_utils as ff
from . import llm, stt

PAD = 0.4  # detik audio ekstra di kiri/kanan klip (kata di tepi tidak terpotong)
_TOK = re.compile(r"[^0-9a-zà-ÿ]+")

PROMPT = """Transkripsikan audio ini KATA PER KATA persis seperti yang diucapkan (verbatim) untuk subtitle.
- Bahasa bisa Indonesia, Jawa, Sunda, atau campuran. Tulis setiap kata dalam bahasa aslinya — JANGAN menerjemahkan,
  jangan merapikan tata bahasa, ejaan sehari-hari yang lazim (mis. Jawa: "ora", "wis", "kuwi", "piye", "ngono").
- Tulis juga kata seru & pengulangan yang benar-benar diucapkan ("iya iya", "lho", "wah"); jangan menambah kata.
- ABAIKAN lirik lagu, musik latar, dan suara dari video lain yang diputar — hanya ucapan orang yang sedang ngobrol.
- Pakai ejaan nama/istilah dari konteks bila memang itu yang diucapkan.
{context}
Keluaran JSON: {{"lines": [{{"start": detik, "end": detik, "speaker": "A", "text": "..."}}]}}
Satu baris = satu ucapan pendek (maks ±12 kata), waktu dalam detik dari awal audio (desimal), urut waktu,
pembicara diberi huruf A/B/C… konsisten untuk orang yang sama."""


def _norm(t: str) -> str:
    return _TOK.sub("", (t or "").lower())


def _sec(v) -> float | None:
    """Detik dari angka atau "m:ss.s"."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*", str(v or ""))
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def _context(p: dict) -> str:
    sc = p["script"]
    ctx = sc.get("context") or {}
    src = (p.get("research") or {}).get("source") or {}
    out = []
    if src.get("title"):
        out.append(f"Judul video: {src['title']}" + (f" (channel {src['channel']})" if src.get("channel") else ""))
    if ctx.get("speakers"):
        out.append("Pembicara: " + ", ".join(f"{s.get('name')} ({s.get('role')})" for s in ctx["speakers"][:6]
                                               if isinstance(s, dict) and s.get("name")))
    names = list(ctx.get("glossary") or []) + [k.get("to") for k in sc.get("transcript_dict") or [] if k.get("to")]
    if names:
        out.append("Ejaan nama/istilah: " + ", ".join(dict.fromkeys(str(n) for n in names if n))[:500])
    return ("Konteks:\n- " + "\n- ".join(out)) if out else ""


def _gemini_lines(audio: Path, p: dict, progress) -> tuple[list[dict], str]:
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "audio/mp3",
                                                     "data": base64.b64encode(audio.read_bytes()).decode()}},
                                    {"text": PROMPT.format(context=_context(p))}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    err = None
    for model in dict.fromkeys([config.GEMINI_MODEL] + list(config.GEMINI_FALLBACK_MODELS)):
        try:
            progress(f"Gemini mendengarkan klip untuk subtitle ({model})…")
            r = llm.gemini_post(model, body, "transkrip klip", timeout=240)
            txt = "".join(x.get("text", "") for x in r["candidates"][0]["content"]["parts"])
            raw = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
            lines = []
            for ln in raw.get("lines") or []:
                a, b, text = _sec(ln.get("start")), _sec(ln.get("end")), str(ln.get("text") or "").strip()
                if a is None or not text:
                    continue
                b = b if b is not None and b > a else a + max(0.6, 0.32 * len(text.split()))
                lines.append({"start": a, "end": b, "speaker": str(ln.get("speaker") or "")[:3], "text": text})
            if lines:
                return sorted(lines, key=lambda x: x["start"]), model
            err = RuntimeError("Gemini tidak mengembalikan baris transkrip")
        except Exception as e:  # noqa: BLE001 — model lain / jalur Whisper saja
            err = e
    raise RuntimeError(f"Gemini gagal: {str(err)[:160]}")


def _whisper_words(audio: Path, language: str | None, hint: str, progress) -> list[dict]:
    data = {"model": stt.GROQ_STT_MODEL, "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"], "temperature": "0"}
    if language:
        data["language"] = {"jw": "jv"}.get(language, language)
    if hint:
        data["prompt"] = hint[:400]
    progress("Whisper mengukur waktu tiap kata…")
    j = stt._groq_stt(audio, data, progress)
    return [{"text": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])}
            for w in j.get("words", []) if w.get("word", "").strip()]


def align(lines: list[dict], anchors: list[dict], dur: float) -> list[dict]:
    """Beri waktu ke tiap kata baris Gemini. Patokan = kata Whisper yang sama (urutan difflib, selisih waktu
    ≤ 2.5 dtk dari perkiraan baris); sisanya dibagi rata (per panjang huruf) di sela patokan, dibatasi
    perkiraan baris Gemini. → [{start, end, text, speaker, words}] (detik relatif awal audio)."""
    toks = []  # (baris, teks, perkiraan mulai, perkiraan selesai)
    for li, ln in enumerate(lines):
        ws = ln["text"].split()
        span, total = ln["end"] - ln["start"], sum(len(w) + 1 for w in ws) or 1
        acc = 0
        for w in ws:
            a = ln["start"] + span * acc / total
            acc += len(w) + 1
            toks.append([li, w, a, ln["start"] + span * acc / total])
    sm = difflib.SequenceMatcher(None, [_norm(t[1]) for t in toks], [_norm(w["text"]) for w in anchors], autojunk=False)
    fixed: dict[int, tuple[float, float]] = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            gi, wi = blk.a + k, blk.b + k
            if _norm(toks[gi][1]) and abs(anchors[wi]["start"] - toks[gi][2]) <= 2.5:
                fixed[gi] = (anchors[wi]["start"], anchors[wi]["end"])
    times = [None] * len(toks)
    for gi, ab in fixed.items():
        times[gi] = list(ab)
    i = 0
    while i < len(toks):  # isi kekosongan di antara patokan
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(toks) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else max(0.0, toks[i][2])
        hi = times[j][0] if j < len(toks) else min(dur, toks[j - 1][3])
        est_lo, est_hi = toks[i][2], toks[j - 1][3]
        # sela antar-patokan terlalu lebar (musik, jeda) → pakai perkiraan baris Gemini di dalam sela itu
        need = sum(len(t[1]) for t in toks[i:j]) * 0.075 + 0.15 * (j - i)
        if hi - lo > 2.5 * need + 1.0:
            lo, hi = max(lo, min(est_lo, hi - need)), min(hi, max(est_hi, lo + need))
        hi = max(hi, lo + 0.08 * (j - i))
        total = sum(len(t[1]) + 1 for t in toks[i:j])
        acc = 0
        for k in range(i, j):
            a = lo + (hi - lo) * acc / total
            acc += len(toks[k][1]) + 1
            times[k] = [a, lo + (hi - lo) * acc / total]
        i = j
    for k in range(1, len(times)):  # monoton & durasi minimum
        times[k][0] = max(times[k][0], times[k - 1][1] - 0.02)
        times[k][1] = max(times[k][1], times[k][0] + 0.08)
    segs = []
    for (li, w, _, _), (a, b) in zip(toks, times):
        if not segs or segs[-1]["_li"] != li:
            segs.append({"_li": li, "speaker": lines[li]["speaker"], "words": []})
        segs[-1]["words"].append({"text": w, "start": round(a, 3), "end": round(b, 3)})
    for s in segs:
        s.pop("_li")
        s["start"], s["end"] = s["words"][0]["start"], s["words"][-1]["end"]
        s["text"] = " ".join(w["text"] for w in s["words"])
    return segs


def refine(pid: str, params: dict, progress) -> dict:
    """Transkrip ulang rentang klip proyek ini (video hasil Clipper) → segmen transkrip di rentang itu diganti."""
    p = store.load(pid)
    sc = p["script"]
    clip = sc["clips"][sc["selected_clip"]]
    a0 = max(0.0, float(clip["start"]) - PAD)
    a1 = float(clip["end"]) + PAD
    src = store.source_file(p)
    so = (p.get("research") or {}).get("source") or {}
    hint = ". ".join(x for x in [so.get("title"), so.get("channel")] if x)
    with tempfile.TemporaryDirectory() as td:
        audio = Path(td) / "clip.mp3"
        ff.run(["-ss", f"{a0:.3f}", "-t", f"{a1 - a0:.3f}", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                "-b:a", "64k", str(audio)])
        anchors, note = [], []
        try:
            anchors = _whisper_words(audio, p.get("language"), hint, progress)
        except Exception as e:  # noqa: BLE001
            note.append(f"Whisper: {str(e)[:100]}")
        lines, engine = [], "whisper"
        if params.get("gemini", True):
            try:
                lines, model = _gemini_lines(audio, p, progress)
                engine = f"gemini:{model}"
            except Exception as e:  # noqa: BLE001
                note.append(str(e)[:160])
                progress(f"{note[-1]} — memakai Whisper potongan pendek")
    if lines:
        segs = align(lines, anchors, a1 - a0)
    elif anchors:  # Whisper saja: satu segmen per ±12 kata / jeda
        segs, cur = [], []
        for w in anchors:
            if cur and (len(cur) >= 12 or w["start"] - cur[-1]["end"] > 0.6):
                segs.append(cur)
                cur = []
            cur.append(w)
        if cur:
            segs.append(cur)
        segs = [{"start": s[0]["start"], "end": s[-1]["end"], "speaker": "", "text": " ".join(w["text"] for w in s),
                 "words": [{k: round(w[k], 3) if k != "text" else w[k] for k in ("text", "start", "end")} for w in s]}
                for s in segs]
    else:
        raise RuntimeError("Transkrip klip gagal: " + "; ".join(note))
    kamus = [(k["from"], k["to"]) for k in sc.get("transcript_dict") or [] if k.get("from") and k.get("to")]
    for s in segs:  # geser ke waktu sumber + kamus kreator
        for w in s["words"]:
            w["start"], w["end"] = round(w["start"] + a0, 3), round(w["end"] + a0, 3)
            for frm, to in kamus:
                if _norm(w["text"]) == _norm(frm):
                    w["text"] = re.sub(re.escape(frm), to, w["text"], flags=re.I) if frm.lower() in w["text"].lower() else to
        s["start"], s["end"] = s["words"][0]["start"], s["words"][-1]["end"]
        s["text"] = " ".join(w["text"] for w in s["words"])
    n_words = sum(len(s["words"]) for s in segs)
    lo, hi = float(clip["start"]), float(clip["end"])

    def apply(q):
        tr = q["script"]["transcript"]
        old = tr.get("segments") or []
        kept = [s for s in old if s["end"] <= lo - 0.05 or s["start"] >= hi + 0.05]
        prev = [s for s in old if not (s["end"] <= lo - 0.05 or s["start"] >= hi + 0.05)]
        hq = q["script"].get("transcript_hq") or {}
        tr["segments"] = sorted(kept + segs, key=lambda s: s["start"])
        q["script"]["transcript_hq"] = {"engine": engine, "words": n_words, "range": [lo, hi],
                                        "prev": hq.get("prev") or prev, "note": "; ".join(note)}
        store.log(q, f"Transkrip presisi klip ({engine}): {n_words} kata")
    store.update(pid, apply)
    per_min = n_words / max(1.0, (hi - lo) / 60)
    progress(f"Transkrip klip: {n_words} kata ({per_min:.0f}/mnt) via {engine.split(':')[0]}")
    return {"engine": engine, "words": n_words, "note": "; ".join(note)}
