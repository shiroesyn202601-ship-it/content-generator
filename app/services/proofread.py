"""Clipper: sunting transkrip Whisper sebelum dicari momen viralnya.

1. KONTEKS: satu panggilan AI membaca judul, channel, deskripsi, chapter + cuplikan seluruh transkrip →
   topik, ringkasan alur, pembicara, ejaan nama/istilah yang benar (`script.context`).
2. KOREKSI: transkrip dibagi per ±6rb karakter dan dikoreksi PARALEL dengan konteks tadi. AI hanya mengembalikan
   daftar perbaikan {kalimat, dari, jadi} — bukan menulis ulang — jadi timestamp kata tetap utuh & ucapan asli
   (gaya lisan, bahasa gaul) tidak dirapikan. Tiap bagian juga diringkas → `context.outline` (peta video per menit),
   dipakai penilai momen supaya tiap bagian tahu posisinya di cerita besar.
3. KAMUS: penggantian manual kreator ("joko we" → "Jokowi") diterapkan di seluruh transkrip, juga setelah AI.

Transkrip asli disimpan di `script.transcript_raw`; penyuntingan selalu dimulai lagi dari situ (tidak menumpuk).
Hasil per bagian di-cache (`script.proof_cache`) → bagian yang gagal karena kuota bisa diulang tanpa mengulang yang lain."""
import copy
import difflib
import hashlib
import re
import time

from .. import store
from . import keys, llm
from .clipper import _lanes, _norm, dedupe_segments, sentences

CHUNK_CHARS = 6000       # per permintaan koreksi (±2rb token) — kecil supaya AI teliti & muat kuota per menit
SAMPLE_CHARS = 12000     # cuplikan transkrip untuk membaca konteks keseluruhan


def _h(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _chunks(units: list[dict], limit: int = CHUNK_CHARS) -> list[tuple[int, int]]:
    out, start, size = [], 0, 0
    for i, u in enumerate(units):
        n = len(u["text"]) + 8
        if size + n > limit and i > start:
            out.append((start, i))
            start, size = i, 0
        size += n
    if start < len(units):
        out.append((start, len(units)))
    return out


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _sample(units: list[dict]) -> str:
    """Seluruh transkrip bila muat, kalau tidak potongan merata dari awal sampai akhir (bertanda menit)."""
    full = "\n".join(f"{_mmss(u['start'])} {u['text']}" for u in units)
    if len(full) <= SAMPLE_CHARS:
        return full
    parts, n = [], 8
    per = SAMPLE_CHARS // n
    step = max(1, len(units) // n)
    for k in range(n):
        buf, size = [], 0
        for u in units[k * step:]:
            line = f"{_mmss(u['start'])} {u['text']}"
            if size + len(line) > per and buf:
                break
            buf.append(line)
            size += len(line)
        parts.append("\n".join(buf))
    return "\n…\n".join(parts)


# ---------------------------------------------------------------- prompt

CTX_SYS = "Kamu analis konten video yang teliti. Kamu hanya menulis hal yang jelas dari data, tidak mengarang."
FIX_SYS = ("Kamu korektor transkrip Whisper yang teliti dan KONSERVATIF. Kamu memahami alur percakapan, lalu hanya "
           "memperbaiki kata yang jelas salah dengar. Ucapan lisan dibiarkan apa adanya.")


def _ctx_prompt(src: dict, units: list[dict]) -> str:
    chap = "\n".join(f"{_mmss(c.get('start_time') or 0)} {c.get('title', '')}" for c in (src.get("chapters") or [])[:30])
    chap = ("Chapter:\n" + chap) if chap else ""
    return f"""Judul video: {src.get('title', '')}
Channel: {src.get('channel', '')}
Deskripsi video:
{(src.get('description') or '')[:1500]}
{chap}

Transkrip otomatis (Whisper, bisa ada salah dengar) — menit:detik + ucapan:
{_sample(units)}

Pahami videonya, lalu tulis:
- "topic": topik utama (1 kalimat).
- "format": jenis video (podcast / wawancara / monolog / debat / ceramah / vlog / tutorial / lainnya).
- "summary": 3-5 kalimat — apa yang dibahas dan bagaimana alur percakapannya dari awal sampai akhir.
- "speakers": orang yang berbicara (nama hanya bila JELAS dari judul/deskripsi/transkrip; kalau tidak, sebut perannya
  mis. "host", "tamu"), dengan "role" singkat.
- "glossary": nama orang, tempat, merek, istilah teknis/asing yang muncul, dengan EJAAN YANG BENAR (maks 30).
Balas JSON: {{"topic":"","format":"","summary":"","speakers":[{{"name":"","role":""}}],"glossary":[""]}}"""


def _ctx_block(ctx: dict) -> str:
    if not ctx:
        return ""
    sp = "; ".join(f"{s.get('name')} ({s.get('role')})" if s.get("role") else str(s.get("name"))
                   for s in ctx.get("speakers") or [] if s.get("name"))
    out = []
    if ctx.get("topic"):
        out.append(f"Topik: {ctx['topic']}")
    if ctx.get("summary"):
        out.append(f"Alur video: {ctx['summary']}")
    if sp:
        out.append(f"Pembicara: {sp}")
    if ctx.get("glossary"):
        out.append("Ejaan nama/istilah yang benar: " + ", ".join(ctx["glossary"][:40]))
    return "\n".join(out)


def _fix_prompt(src: dict, ctx: dict, units: list[dict], a: int, b: int, before: str) -> str:
    lines = "\n".join(f"[{i}] {units[i]['text']}" for i in range(a, b))
    before = f"Ucapan sebelum bagian ini (hanya konteks, jangan dikoreksi): {before}" if before else ""
    return f"""Video: "{src.get('title', '')}" — channel {src.get('channel', '')}
{_ctx_block(ctx)}
{before}

Transkrip hasil Whisper per kalimat bernomor:
{lines}

Baca dan pahami percakapannya dulu. Lalu cari kata yang SALAH DENGAR oleh Whisper:
- kata yang bunyinya mirip tapi tidak cocok dengan konteks kalimat / percakapan;
- nama orang, tempat, merek, atau istilah yang ejaannya salah. Bila bunyi sebuah kata/frasa MIRIP salah satu nama
  di daftar ejaan (mis. "tambah ikun" ≈ "Tanboy Kun"), ganti dengan nama itu persis seperti di daftar;
- kata ngawur yang tidak bermakna di kalimat itu.
Aturan:
- Koreksi HANYA bila kamu yakin dari konteks. Ragu = biarkan (jangan dimasukkan ke daftar).
- JANGAN memperbaiki tata bahasa, gaya bicara, bahasa gaul/daerah/slang (kureng, gue, lu, nggak), kata pengisi
  (eh, anu, gitu), seruan, kata kasar, atau susunan kalimat. Ini ucapan lisan — biarkan apa adanya. JANGAN menerjemahkan.
- JANGAN menghapus ucapan. Seruan, celetukan, pertanyaan yang diulang, dan kata kasar adalah ucapan asli.
  Satu-satunya yang boleh dihapus ("to": "") adalah halusinasi Whisper yang jelas: frasa penutup video
  ("terima kasih telah menonton", "jangan lupa subscribe") di tengah obrolan, atau teks bahasa lain yang ngawur.
- "from" = potongan PERSIS dari kalimat [n] (1-5 kata), "to" = penggantinya dengan jumlah kata yang mirip
  (harus pas dengan yang diucapkan).
Juga tulis "summary": 1-2 kalimat isi bagian ini (siapa membahas apa), dan "terms": nama/istilah penting di bagian
ini dengan ejaan benar.
Balas JSON: {{"fixes":[{{"n":{a},"from":"","to":"","why":"alasan singkat"}}],"summary":"","terms":[""]}}"""


# ---------------------------------------------------------------- AI paralel

def _ask_parallel(tasks: list[tuple[str, str, str]], effort: str, progress, label: str) -> dict:
    """tasks: [(key, system, user)] → {key: dict | Exception}. Dibagi bergiliran ke model/key Groq (kuota
    token/menit masing-masing) dan dikerjakan bersamaan; tiap panggilan tetap boleh pindah ke layanan cadangan."""
    from concurrent.futures import ThreadPoolExecutor

    from .. import jobs
    if not tasks:
        return {}
    models = _lanes() if keys.has("groq") and "groq" in llm.chain() else [None]
    pp, pj = getattr(jobs._local, "progress", None), getattr(jobs._local, "jid", None)
    done, out = [0], {}

    def one(i, key, sys_, usr):
        jobs._local.progress, jobs._local.jid = pp, pj  # bar status & tombol Batalkan
        model = models[i % len(models)]
        res = None
        if effort != "low" and model:
            # berpikir lebih dalam = koreksi lebih teliti, tapi gpt-oss di Groq kadang gagal menyusun JSON (400
            # json_validate_failed) → langsung ulang dengan "low" di bawah, tanpa menunggu antrean model lain
            try:
                res = llm.chat_json(sys_, usr, "groq", temperature=0.1, groq_model=model, effort=effort,
                                    fallback=False, only_model=True, max_wait=20)
            except jobs.JobCancelled:
                raise
            except Exception:  # noqa: BLE001
                res = None
        if res is None:
            try:
                res = llm.chat_json(sys_, usr, "groq" if model else None, temperature=0.1,
                                    groq_model=model, effort="low")
            except jobs.JobCancelled:
                raise
            except Exception as e:  # noqa: BLE001 — satu bagian gagal tidak menggagalkan semua
                res = e
        done[0] += 1
        progress(f"{label}: {done[0]}/{len(tasks)} bagian")
        return key, res

    with ThreadPoolExecutor(max_workers=min(len(models), len(tasks))) as ex:
        for key, res in [f.result() for f in [ex.submit(one, i, *t) for i, t in enumerate(tasks)]]:
            out[key] = res
    return out


# ---------------------------------------------------------------- menerapkan perbaikan ke kata ber-timestamp

def _find(norms: list[str], src: list[str], fuzzy: bool = True) -> list[int]:
    n = len(src)
    hits = [i for i in range(len(norms) - n + 1) if norms[i:i + n] == src]
    if hits or not fuzzy:
        return hits
    want = "".join(src)
    best, pos = 0.0, -1
    for i in range(len(norms) - n + 1):
        r = difflib.SequenceMatcher(None, "".join(norms[i:i + n]), want).ratio()
        if r > best:
            best, pos = r, i
    return [pos] if best >= 0.82 else []


def _seg_text(seg: dict, frm: str, to: str, multi: bool) -> None:
    cores = [c for c in (re.sub(r"[^\w]", "", t) for t in frm.split()) if c]
    ok = False
    if cores and not multi:
        pat = r"(?<!\w)" + r"[^\w]+".join(map(re.escape, cores)) + r"(?!\w)"
        new, k = re.subn(pat, to, seg.get("text", ""), count=1, flags=re.IGNORECASE)
        if k:
            new = re.sub(r"\s+([,.!?…])", r"", re.sub(r"\s{2,}", " ", new))
            seg["text"], ok = re.sub(r"^[\s,.!?…]+", "", new).strip(), True
    if not ok:  # tidak ketemu di teks / melintasi segmen → susun ulang dari kata (tanda baca segmen ini hilang)
        seg["text"] = " ".join(w["text"] for w in seg.get("words", []))


def _replace_at(uw: list, pos: int, n: int, to: str) -> None:
    """uw: [(segmen, kata)] milik satu kalimat. Ganti n kata mulai pos dengan kata-kata `to`; waktu dibagi
    sebanding panjang huruf di rentang kata yang diganti."""
    span = uw[pos:pos + n]
    t0, t1 = span[0][1]["start"], span[-1][1]["end"]
    toks = to.split()
    tot = sum(max(1, len(t)) for t in toks) or 1
    new, t = [], t0
    for tk in toks:
        d = (t1 - t0) * max(1, len(tk)) / tot
        new.append({"text": tk, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    seg0 = span[0][0]
    idx0 = next(k for k, w in enumerate(seg0["words"]) if w is span[0][1])
    segs_touched = []
    for seg, w in span:
        seg["words"] = [x for x in seg["words"] if x is not w]
        if not any(s is seg for s in segs_touched):
            segs_touched.append(seg)
    seg0["words"][idx0:idx0] = new
    uw[pos:pos + n] = [(seg0, w) for w in new]
    return segs_touched


def _apply(uw: list, frm: str, to: str, every: bool = False) -> int:
    src = [t for t in (_norm(x) for x in frm.split()) if t]
    if not src:
        return 0
    hits = _find([_norm(w["text"]) for _, w in uw], src, fuzzy=not every)
    if not hits:
        return 0
    if not every:
        hits = hits[:1]
    for pos in reversed(hits):  # dari belakang supaya posisi sebelumnya tidak bergeser
        touched = _replace_at(uw, pos, len(src), to)
        for seg in touched:
            _seg_text(seg, frm, to, multi=len(touched) > 1)
    return len(hits)


RAGU = re.compile(r"\b(biarkan|tidak yakin|kurang yakin|ragu|tidak jelas|bisa jadi)\b", re.IGNORECASE)


# Frasa halusinasi khas Whisper (muncul di bagian hening/musik) — satu-satunya yang boleh dihapus AI
HALU = re.compile(r"terima ?kasih (telah|sudah|udah)? ?(menonton|nonton)|subscribe|like,? (dan|and) share|"
                  r"sampai jumpa (lagi )?di video|subtitle|amara\.org|thanks? for watching|tonton juga", re.IGNORECASE)
NON_LATIN = re.compile(r"[Ѐ-ӿ֐-ۿ฀-๿぀-ヿ㐀-鿿가-힯]")


MIN_SIMILAR = 0.55  # "Gunaderma"→"Gunadarma" 0.94, "tambah ikun"→"Tanboy Kun" 0.63; "Gunaderma"→"Gadjah Mada" 0.52 ditolak


def _ok_fix(f: dict) -> bool:
    frm, to = str(f.get("from") or "").strip(), str(f.get("to") or "").strip()
    if not frm or _norm(frm) == _norm(to):
        return False
    nf, nt = len(frm.split()), len(to.split())
    # AI kadang tetap mengusulkan padahal alasannya sendiri ragu ("mungkin…, biarkan") → buang
    if RAGU.search(str(f.get("why") or "")):
        return False
    if not to:  # menghapus ucapan asli (seruan/celetukan bisa justru momen terbaik) hanya untuk halusinasi jelas
        return bool(HALU.search(frm) or NON_LATIN.search(frm)) and nf <= 12
    a, b = [_norm(x) for x in frm.split() if _norm(x)], [_norm(x) for x in to.split() if _norm(x)]
    if set(a) <= set(b) or set(b) <= set(a):
        return False  # hanya menambah / membuang kata (mis. "makan tahu" → "makan tahu busuk"), bukan salah dengar
    # Whisper salah dengar kata yang BUNYINYA MIRIP → pengganti yang jauh berbeda adalah tebakan AI
    # (hanya bagian yang berubah — kata yang sama di kedua sisi, mis. "Universitas", tidak ikut menaikkan skor)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal" and difflib.SequenceMatcher(
                None, "".join(a[i1:i2]), "".join(b[j1:j2])).ratio() < MIN_SIMILAR:
            return False
    return nf <= 8 and nt <= nf * 2 + 2


# ---------------------------------------------------------------- aksi

def run(pid: str, params: dict, progress) -> dict:
    """params: ai (default True) — panggil AI untuk bagian yang belum ada di cache; force — abaikan cache;
    dict — kamus penggantian lengkap [{from, to}] (menimpa); replace — tambahkan ke kamus;
    reject / unreject — {t, from}: batalkan / pakai lagi satu koreksi AI (diingat walau disunting ulang)."""
    p = store.load(pid)
    sc = p["script"]
    raw = sc.get("transcript_raw") or sc.get("transcript") or {}
    if not raw.get("segments"):
        raise RuntimeError("Transkrip kosong — jalankan Transkrip dulu.")
    src = p["research"].get("source") or {}
    use_ai = params.get("ai", True)
    cache = {} if params.get("force") else dict(sc.get("proof_cache") or {})
    kamus = list(params["dict"]) if isinstance(params.get("dict"), list) else list(sc.get("transcript_dict") or [])
    rkey = lambda t, frm: f"{float(t):.1f}|{_norm(frm)}"  # noqa: E731
    reject = set(sc.get("proof_reject") or [])
    if params.get("reject"):
        reject.add(rkey(params["reject"]["t"], params["reject"]["from"]))
    if params.get("unreject"):
        reject.discard(rkey(params["unreject"]["t"], params["unreject"]["from"]))
    for r in params.get("replace") or []:
        kamus = [k for k in kamus if _norm(k.get("from", "")) != _norm(r.get("from", ""))] + [r]
    kamus = [{"from": str(k.get("from", "")).strip(), "to": str(k.get("to", "")).strip()}
             for k in kamus if str(k.get("from", "")).strip()]

    segs = copy.deepcopy(dedupe_segments(raw["segments"]))
    units = sentences(segs)
    full_key = _h("\n".join(u["text"] for u in units))
    ctx = sc.get("context") if (sc.get("context") or {}).get("key") == full_key and not params.get("force") else None
    chunks = _chunks(units)
    ckey = {c: _h("|".join(units[i]["text"] for i in range(*c))) for c in chunks}

    failed: list[str] = []
    if use_ai:
        if not ctx:
            progress("Membaca konteks seluruh video (topik, pembicara, nama & istilah)…")
            try:
                got = llm.chat_json(CTX_SYS, _ctx_prompt(src, units), temperature=0.2, effort="low")
                ctx = {"topic": str(got.get("topic") or ""), "format": str(got.get("format") or ""),
                       "summary": str(got.get("summary") or ""),
                       "speakers": [{"name": str(s.get("name") or ""), "role": str(s.get("role") or "")}
                                    for s in got.get("speakers") or [] if isinstance(s, dict) and s.get("name")][:8],
                       "glossary": [str(g) for g in got.get("glossary") or [] if g][:30], "key": full_key}
            except llm.LLMError as e:
                failed.append(f"Konteks: {str(e)[:160]}")
                ctx = {}
        todo = [c for c in chunks if ckey[c] not in cache]
        if todo:
            progress(f"Menyunting transkrip: {len(todo)} bagian dicek AI dengan konteks percakapan…")
            tasks = []
            for a, b in todo:
                before = " ".join(u["text"] for u in units[max(0, a - 2):a])[-300:]
                tasks.append((ckey[(a, b)], FIX_SYS, _fix_prompt(src, ctx or {}, units, a, b, before)))
            res = _ask_parallel(tasks, "medium", progress, "Menyunting transkrip")
            for (a, b) in todo:
                r = res.get(ckey[(a, b)])
                if isinstance(r, Exception) or not isinstance(r, dict):
                    failed.append(f"Bagian {_mmss(units[a]['start'])}: {str(r)[:160]}")
                    continue
                fx = [{"n": int(f["n"]), "from": str(f["from"]).strip(), "to": str(f.get("to") or "").strip(),
                       "why": str(f.get("why") or "")[:120]}
                      for f in r.get("fixes") or []
                      if isinstance(f, dict) and str(f.get("n", "")).lstrip("-").isdigit()
                      and a <= int(f["n"]) < b and _ok_fix(f)]
                cache[ckey[(a, b)]] = {"fixes": fx, "summary": str(r.get("summary") or "")[:300],
                                       "terms": [str(t) for t in r.get("terms") or [] if t][:15]}

    # --- terapkan (selalu dari transkrip asli): perbaikan AI per kalimat, lalu kamus kreator di semua kalimat
    words = [(s, w) for s in segs for w in s.get("words", [])]
    uws, k = [], 0
    for u in units:
        cur = []
        while k < len(words) and words[k][1]["start"] < u["end"] - 1e-3:
            if words[k][1]["end"] >= u["start"] - 1e-3:
                cur.append(words[k])
            k += 1
        uws.append(cur)
    applied, rejected = [], []
    for c in chunks:
        for f in (cache.get(ckey[c]) or {}).get("fixes", []):
            if not (0 <= f["n"] < len(uws)) or not _ok_fix(f):
                continue
            if rkey(units[f["n"]]["start"], f["from"]) in reject:
                rejected.append({"t": round(units[f["n"]]["start"], 2), "from": f["from"], "to": f["to"], "why": f["why"]})
                continue
            if _apply(uws[f["n"]], f["from"], f["to"]):
                applied.append({"t": round(units[f["n"]]["start"], 2), "from": f["from"], "to": f["to"],
                                "why": f["why"], "by": "ai"})
    for kk in kamus:
        n = 0
        for i, uw in enumerate(uws):
            got = _apply(uw, kk["from"], kk["to"], every=True)
            if got:
                n += got
                applied.append({"t": round(units[i]["start"], 2), "from": kk["from"], "to": kk["to"],
                                "why": "kamus", "by": "kamus"})
    segs = [s for s in segs if s.get("words") or s.get("text", "").strip()]

    if ctx is not None:
        terms = list(dict.fromkeys([*(ctx.get("glossary") or []),
                                    *[t for c in chunks for t in (cache.get(ckey[c]) or {}).get("terms", [])]]))
        ctx = {**ctx, "glossary": terms[:40],
               "outline": [{"start": round(units[a]["start"], 1), "end": round(units[b - 1]["end"], 1),
                            "summary": (cache.get(ckey[(a, b)]) or {}).get("summary", "")}
                           for a, b in chunks if (cache.get(ckey[(a, b)]) or {}).get("summary")]}
    keep = set(ckey.values())
    n_ai = sum(1 for x in applied if x["by"] == "ai")

    def save(q):
        s = q["script"]
        if not s.get("transcript_raw"):
            s["transcript_raw"] = raw
        s["transcript"] = {**raw, "segments": segs,
                           "proofread": {"at": time.strftime("%Y-%m-%d %H:%M"), "fixes": len(applied),
                                         "failed": len(failed), "ai": bool(use_ai) or bool(cache)}}
        s["transcript_fixes"] = applied[:500]
        s["transcript_dict"] = kamus
        s["proof_reject"] = sorted(reject)
        s["transcript_rejected"] = rejected[:200]
        s["proof_cache"] = {kk: v for kk, v in cache.items() if kk in keep}
        if ctx is not None:
            s["context"] = ctx
        store.touch_stage(q, "script")
        store.log(q, f"Sunting transkrip: {n_ai} koreksi AI, {len(applied) - n_ai} dari kamus"
                     + (f", {len(failed)} bagian gagal" if failed else ""))
    store.update(pid, save)
    if failed:
        progress(f"Transkrip disunting sebagian — {len(failed)} bagian belum dicek AI (kuota). {failed[0]}")
    return {"fixes": len(applied), "ai_fixes": n_ai, "failed": len(failed), "parts": len(chunks)}
