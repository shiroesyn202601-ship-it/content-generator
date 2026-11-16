"""
Nilai tambah Clipper — supaya klip bukan sekadar "potong + subtitle" (kebijakan YouTube reused/inauthentic
content & pembaruan jangkauan Shorts Okt 2026: yang dihitung adalah komentar, konteks, dan framing edukatif).

Dijalankan setelah klip dipotong (assets/clip.mp4) dan sebelum subtitle. Menghasilkan assets/clip_enh.mp4:

  [intro komentar kreator]  +  [klip dirapatkan + punch-in]  +  [outro komentar kreator]
        TTS di atas frame             jeda panjang & "eh/anu"          TTS opini + pertanyaan
        pertama yang di-blur          dibuang; punch-in ke wajah          untuk kolom komentar
                                      hanya di kalimat penekanan

  + B-roll layar penuh saat narasumber menyebut sesuatu (Wikimedia/Pixabay/AI), emoji (Noto) di kata
    penting, SFX whoosh/pop. Kartu konteks ("siapa dia") & kartu fakta (dicek ke Wikipedia) ditulis
    sebagai event ASS → ikut dibakar bersama subtitle saat render.

Semua timestamp kata dipetakan ulang ke timeline baru (p["enhance"]["words"]) sehingga subtitle tetap pas.
Rencana AI (p["enhance"]["plan"]) bisa diedit user lalu diterapkan ulang tanpa memanggil AI lagi.
"""
import random
import re
import threading
from pathlib import Path

from .. import config, store
from . import ffmpeg_utils as ff
from . import llm, prompts, usage

DEFAULTS = {"commentary": "both",   # both | intro | outro | off
            "persona": "",          # gaya bicara kreator, mis. "santai, kritis, sedikit humor"
            "context": True, "facts": True, "broll": True,
            "tighten": True, "punch": True, "emoji": True, "sfx": True, "vary": True,
            "creator": "Shiroe Syn"}  # label di layar saat kreator berkomentar
FILLERS = {"eh", "ehm", "em", "emm", "hmm", "hm", "ee", "eee", "eeh", "anu", "umm", "um", "uh", "ah", "eum"}
PAD_W = 0.08         # napas di sekitar kata yang dipertahankan
GAP_KEEP = 0.45      # jeda (setelah padding) di atas ini dibuang
MIN_SHOT = 1.6       # punch-in hanya bila potongan cukup panjang
PUNCH_EVERY = 9.0    # rata-rata paling banyak 1 punch-in per 9 dtk klip
PUNCH_GAP = 5.0      # jarak minimum antar-punch-in (dtk)
BROLL_DUR, EMOJI_DUR = 2.4, 1.3
BROLL_CONF = 0.7     # usulan B-roll AI di bawah keyakinan ini dibuang
_cache_lock = threading.Lock()  # produksi paralel: emoji/SFX yang sama jangan diunduh/dibuat bersamaan
_WORD = re.compile(r"[^\wÀ-ÿ'-]+", re.U)


def options(o: dict | None) -> dict:
    o = {**DEFAULTS, **(o or {})}
    if o["commentary"] not in ("both", "intro", "outro", "off"):
        o["commentary"] = "both"
    return o


def _core(t: str) -> str:
    return _WORD.sub("", (t or "").lower())


def valid(p: dict) -> bool:
    """Hasil enhance masih cocok dengan klip yang sekarang (klip dipotong ulang → enhance basi)."""
    e = p.get("enhance") or {}
    if not e.get("path"):
        return False
    d = store.project_dir(p["id"])
    src, out = d / (p["assets"].get("clip_path") or ""), d / e["path"]
    try:
        return out.is_file() and src.is_file() and abs(src.stat().st_mtime - e.get("src_mtime", 0)) < 1
    except OSError:
        return False


def video_path(p: dict) -> str | None:
    return p["enhance"]["path"] if p["mode"] == "clipper" and valid(p) else p["assets"].get("clip_path")


# ---------------------------------------------------------------- kata & kalimat klip
def _sentences(words: list[dict]) -> list[list[int]]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if re.search(r"[.!?…]$", w["text"]) or not nxt or nxt["start"] - w["end"] >= 0.8:
            out.append(cur)
            cur = []
    return out


def _clip_sentences(p: dict, words: list[dict]) -> list[list[int]]:
    """Kalimat klip memakai batas kalimat transkrip (clipper.sentences menyalin tanda baca dari teks segmen —
    kata Whisper sendiri sering tanpa tanda baca). Cadangan: putus di jeda ≥0.5 dtk / tiap 14 kata."""
    from . import clipper
    try:
        cs = (p["assets"].get("clip_range") or [0])[0]
        starts = sorted(s["start"] - cs for s in clipper.sentences(p["script"]["transcript"]["segments"]))
    except Exception:  # noqa: BLE001
        starts = []
    out, cur, j = [], [], 0
    for i, w in enumerate(words):
        nb = False
        while j < len(starts) and starts[j] <= w["start"] + 0.05:
            j += 1
            nb = True
        if cur and nb:
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    # Transkrip sering tanpa tanda baca → "kalimat" bisa 50+ kata. Pecah lagi di jeda kecil (≥0.25 dtk)
    # setelah 12 kata, paksa di 18 kata — satuan ini dipakai untuk punch-in & penempatan B-roll/emoji.
    fine = []
    for s in out:
        cur = []
        for i in s:
            if cur and ((len(cur) >= 12 and words[i]["start"] - words[cur[-1]]["end"] >= 0.25) or len(cur) >= 18):
                fine.append(cur)
                cur = []
            cur.append(i)
        if cur:
            fine.append(cur)
    return fine


def _has_word(words: list[dict], sent: list[int], word: str) -> bool:
    toks = [t for t in (_core(x) for x in (word or "").split()) if t]
    return bool(toks) and any(_core(words[i]["text"]) == toks[0] or (len(toks[0]) > 3 and toks[0] in _core(words[i]["text"]))
                              for i in sent)


def _find_word(words: list[dict], sent: list[int], word: str) -> int:
    target = _core(word)
    toks = [t for t in (_core(x) for x in (word or "").split()) if t]
    for i in sent:
        c = _core(words[i]["text"])
        if c and (c == target or (toks and c == toks[0]) or (len(c) > 3 and c in target)):
            return i
    return sent[0]


def _punch_plan(p: dict, clip: Path, shots: list[list[float]], rnd) -> dict:
    """Punch-in yang sesuai konteks (dulu: zoom bergantian tiap kalimat & selalu di tengah layar → terasa acak).
    Kini hanya di potongan dengan suara paling bertenaga (penekanan), saat SATU orang di layar, tidak melintasi
    potong kamera, berjarak ≥ PUNCH_GAP dtk dan tidak pernah di dua potongan berurutan. Zoom diarahkan ke wajah
    pembicara dan diperkecil bila bingkai auto-reframe sudah diperbesar. {indeks potongan: (zoom, fx, fy)}"""
    from . import reframe
    info = p["assets"].get("reframe") if p["assets"].get("crop_mode") == "face" else None
    framed = bool(info and info.get("segments"))
    try:
        env = list(reframe._audio_env(clip, 0.0, ff.duration(clip), 10.0))
    except Exception:  # noqa: BLE001
        env = []

    def energy(a: float, b: float) -> float:
        vals = env[int(a * 10):max(int(a * 10) + 1, int(b * 10))]
        return sum(vals) / len(vals) if vals else -99.0
    cands, start, t = [], [], 0.0
    for k, (a, b) in enumerate(shots):
        start.append(t)
        t += b - a
        if b - a < MIN_SHOT:
            continue
        face = reframe.face_in(info, a, b) if framed else (0.5, 0.42, 1.0)
        if face is not None:
            cands.append((energy(a, b), k) + tuple(face))
    if not cands:
        return {}
    budget = max(1, int(t / PUNCH_EVERY))
    floor = sorted(c[0] for c in cands)[len(cands) // 2]   # hanya yang di atas median energi
    chosen: dict = {}
    for e, k, fx, fy, room in sorted(cands, reverse=True):
        if len(chosen) >= budget or e < floor:
            break
        if k - 1 in chosen or k + 1 in chosen or any(abs(start[k] - start[j]) < PUNCH_GAP for j in chosen):
            continue
        z = round(rnd.uniform(1.10, 1.15), 3) if room > 0.85 else 1.06
        chosen[k] = (z, fx, fy)
    return chosen


# ---------------------------------------------------------------- rencana AI
PLAN_SYS = """Kamu editor & penulis untuk kreator YouTube Shorts Indonesia yang membuat klip dari podcast/video orang lain.
Tugasmu memberi NILAI TAMBAH yang diakui YouTube: komentar/opini kreator sendiri, konteks, dan framing edukatif.
Aturan keras:
- intro & outro adalah suara KREATOR (bukan narasumber). JANGAN mendeskripsikan apa yang terlihat di layar,
  JANGAN mengulang kalimat narasumber. Berikan sudut pandang, opini, alasan kenapa ini penting, atau pertanyaan.
- intro: maks 14 kata, memancing rasa penasaran tanpa spoiler. outro: maks 28 kata, opini/insight kreator +
  satu pertanyaan untuk penonton jawab di komentar. Bahasa Indonesia lisan yang natural.
- people: hanya orang yang namanya JELAS disebut di judul/transkrip. role_guess hanya bila jelas dari teks; jika ragu kosongkan.
- claims: maks 2 klaim FAKTUAL (bukan opini) dari narasumber yang bisa dicek ke Wikipedia. Jika tidak ada, [].
- broll: MAKS 2, dan [] lebih baik daripada B-roll yang tidak pas (B-roll menutupi wajah pembicara — hanya bila
  gambar itu benar-benar membantu penonton paham). Syarat SEMUA terpenuhi:
  (1) sesuatu yang SPESIFIK & dikenali: tempat/landmark bernama, tokoh publik terkenal (ada di Wikipedia), merek,
      karya, benda khusus yang sedang dijelaskan — bukan kata umum (uang, musik, acara, panggung, tangan, bulan,
      orang, rumah, kota, sekolah, pekerjaan, makanan biasa);
  (2) benar-benar DIBAHAS (jadi pokok obrolan, dijelaskan/diceritakan) — BUKAN disebut sambil lalu atau dalam
      daftar ("lighting-e urung, alat-alat sewa" = sambil lalu → JANGAN); bukan kiasan/slang/candaan;
  (3) ejaannya jelas benar — transkrip bisa salah dengar: nama/kata yang aneh atau tidak dikenal = JANGAN;
  (4) bukan orang yang sedang bicara di video, bukan cerita pengalaman pribadi (keluarga, pacar, masa kecil);
  (5) tidak sensitif (alat kelamin, kekerasan, dll.).
  word = kata persis dari kalimat itu. query_en = kata kunci foto bahasa Inggris 2-5 kata yang menyertakan konteks
  (mis. "concert stage spotlight", "Kediri East Java"). visual = deskripsi gambar yang SEHARUSNYA tampil, bahasa
  Inggris 1 kalimat, lengkap dengan suasana sesuai topik video (mis. kata "lampu sorot" di obrolan penyanyi dangdut →
  "bright stage spotlights shining over a dangdut concert stage in Indonesia at night", BUKAN lampu jalan).
  kind = "commons" untuk tokoh/tempat/merek nyata, "photo" untuk benda. why = alasan gambar ini membantu penonton.
  confidence = 0-1 seberapa yakin syarat (1)-(5) terpenuhi.
- emoji: maks 5, satu emoji yang cocok untuk kata penting (word = kata persis dari kalimat itu).
Balas HANYA JSON:
{"intro":"","outro":"","people":[{"name":"","role_guess":""}],"topic":"",
 "claims":[{"sent":0,"claim":"","query":""}],
 "broll":[{"sent":0,"word":"","query_en":"","visual":"","kind":"photo","why":"","confidence":0.0}],
 "emoji":[{"sent":0,"word":"","emoji":""}]}"""


def make_plan(p: dict, words: list[dict], sents: list[list[int]], opts: dict, progress) -> dict:
    src = p["research"].get("source") or {}
    clips, sel = p["script"].get("clips") or [], p["script"].get("selected_clip")
    clip = clips[sel] if sel is not None and sel < len(clips) else {}
    numbered = "\n".join(f"[{k}] " + " ".join(words[i]["text"] for i in s) for k, s in enumerate(sents))
    persona = opts.get("persona") or "santai, kritis, jujur, sedikit humor — seperti teman yang paham topiknya"
    user = (f"Judul video sumber: {src.get('title', '')}\nChannel sumber: {src.get('channel', '')}\n"
            f"Judul klip: {clip.get('title', '')}\nHook klip: {clip.get('hook', '')}\n"
            f"Gaya bicara kreator: {persona}\n"
            f"{prompts.video_context(p['script'].get('context'), clip.get('start'), clip.get('end'))}"
            f"\nTranskrip klip per kalimat:\n{numbered}")
    progress("AI menyusun komentar, konteks & B-roll…")
    raw = llm.chat_json(PLAN_SYS, user, temperature=0.8, effort="low")
    return _clean_plan(raw, len(sents))


# B-roll yang TIDAK boleh dicari otomatis (instruksi di prompt saja tidak cukup — AI kadang tetap mengusulkan).
# Foto stok untuk istilah ini berisiko melanggar kebijakan konten YouTube (seksual/kekerasan/medis grafis).
SENSITIVE = re.compile(
    r"\b(pubic|genital\w*|penis|vagina\w*|vulva|testic\w*|scrot\w*|anus|anal|buttock\w*|nipple\w*|breast\w*|"
    r"nude|naked|nudity|sex\w*|erotic|porn\w*|lingerie|underwear|bikini|condom\w*|intimate|crotch|groin|"
    r"blood\w*|gore|corpse|dead body|wound\w*|injur\w*|surgery|surgical|weapon\w*|gun\w*|knife|drug\w*|"
    r"syringe|vomit\w*|feces|poop|urine|kelamin\w*|kemaluan\w*|payudara\w*|telanjang|bugil|seks\w*|darah\w*|mayat\w*|manuk\w*|jembut\w*|selangkang\w*)\b",
    re.I)


def _sensitive(text: str) -> bool:
    return bool(SENSITIVE.search(text or ""))


def _conf(x: dict) -> float:
    """Keyakinan AI untuk B-roll; rencana lama / hasil edit user tanpa angka = dipercaya (tetap dicek vision)."""
    try:
        return float(x.get("confidence", 1.0))
    except (TypeError, ValueError):
        return 0.0


def _clean_plan(raw: dict, n_sent: int) -> dict:
    def s(v, n=300):
        return str(v or "").strip()[:n]

    def sent_ok(x):
        try:
            return 0 <= int(x.get("sent")) < n_sent
        except (TypeError, ValueError):
            return False
    def words_max(v, n):  # AI kadang melebihi batas kata → potong di batas kalimat/kata
        ws = s(v, 400).split()
        if len(ws) <= n:
            return " ".join(ws)
        cut = " ".join(ws[:n])
        m = re.match(r"(.*[.!?])\s", cut + " ")
        return m.group(1) if m and len(m.group(1).split()) >= n * 0.5 else cut.rstrip(",;:") + "…"
    out = {"intro": words_max(raw.get("intro"), 16), "outro": words_max(raw.get("outro"), 30), "topic": s(raw.get("topic"), 80),
           "people": [{"name": s(x.get("name"), 60), "role_guess": s(x.get("role_guess"), 80)}
                      for x in (raw.get("people") or []) if isinstance(x, dict) and s(x.get("name"))][:2],
           "claims": [{"sent": int(x["sent"]), "claim": s(x.get("claim"), 200), "query": s(x.get("query"), 80)}
                      for x in (raw.get("claims") or []) if isinstance(x, dict) and sent_ok(x) and s(x.get("claim"))][:2],
           "broll": [{"sent": int(x["sent"]), "word": s(x.get("word"), 40), "query_en": s(x.get("query_en"), 60),
                      "kind": x.get("kind") if x.get("kind") in ("photo", "commons", "ai") else "photo",
                      "why": s(x.get("why"), 120), "visual": s(x.get("visual"), 220)}
                     for x in (raw.get("broll") or []) if isinstance(x, dict) and sent_ok(x) and s(x.get("query_en"))
                     and _conf(x) >= BROLL_CONF and not _sensitive(f"{x.get('query_en')} {x.get('word')}")][:3],
           "emoji": [{"sent": int(x["sent"]), "word": s(x.get("word"), 40), "emoji": s(x.get("emoji"), 12)}
                     for x in (raw.get("emoji") or []) if isinstance(x, dict) and sent_ok(x)
                     and s(x.get("emoji")) and not s(x.get("emoji")).isascii()][:5]}
    return out


# ---------------------------------------------------------------- konteks & fakta (Wikipedia)
def _wiki_role(name: str) -> dict | None:
    from . import factsource
    try:
        titles = factsource.search("id", name, 2)
    except Exception:  # noqa: BLE001
        return None
    first = _core(name.split()[0]) if name.split() else ""
    for t in titles:
        if first and first not in _core(t):
            continue
        try:
            ex = factsource.extract("id", t)
        except Exception:  # noqa: BLE001
            ex = None
        if not ex:
            continue
        sent = re.split(r"(?<=[.!?])\s", ex["text"], 1)[0]
        m = re.search(r"\b(?:adalah|merupakan|ialah)\s+(.*)", sent)
        if not m:
            continue
        usage.ai("Sumber", "Wikipedia", ex["title"], None, "kartu konteks")
        role = re.split(r"[.;(]", m.group(1))[0].strip()
        role = re.sub(r"^(seorang|sebuah|salah satu)\s+", "", role)
        if len(role) > 70:
            role = role[:70].rsplit(" ", 1)[0] + "…"
        return {"name": ex["title"], "role": role, "url": ex["url"]}
    return None


FACT_SYS = """Kamu pemeriksa fakta. Untuk tiap klaim, bandingkan HANYA dengan teks sumber yang diberikan.
verdict: "didukung" (sumber jelas mendukung), "bertentangan" (sumber jelas menyatakan lain), "tidak_ada" (sumber tidak membahas).
card: kalimat singkat (maks 16 kata) untuk ditampilkan di layar — fakta tambahan dari sumber (bila didukung)
atau klarifikasi yang benar (bila bertentangan). Jangan menambah informasi yang tidak ada di sumber.
Balas JSON: {"items":[{"i":0,"verdict":"tidak_ada","card":"","source":""}]}"""


def _fact_cards(plan: dict, progress) -> list[dict]:
    from . import factsource
    claims = plan.get("claims") or []
    if not claims:
        return []
    progress("Mengecek klaim ke Wikipedia…")
    blocks = []
    for k, c in enumerate(claims):
        srcs = []
        try:
            titles = factsource.search("id", c["query"] or c["claim"], 2)
        except Exception:  # noqa: BLE001
            titles = []
        for t in titles:
            try:
                ex = factsource.extract("id", t)
            except Exception:  # noqa: BLE001
                ex = None
            if ex:
                usage.ai("Sumber", "Wikipedia", ex["title"], None, "cek fakta")
                srcs.append(f"[{ex['title']}] {ex['text'][:2200]}")
        blocks.append(f"KLAIM {k}: {c['claim']}\nSUMBER:\n" + ("\n\n".join(srcs) or "(tidak ditemukan)"))
    try:
        res = llm.chat_json(FACT_SYS, "\n\n---\n\n".join(blocks), temperature=0.2, effort="low")
    except Exception as e:  # noqa: BLE001 — cek fakta gagal → tanpa kartu fakta (jangan mengarang)
        progress(f"Cek fakta dilewati: {str(e)[:80]}")
        return []
    out = []
    for it in res.get("items") or []:
        try:
            i = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(claims) and it.get("verdict") in ("didukung", "bertentangan") and (it.get("card") or "").strip():
            out.append({"sent": claims[i]["sent"], "verdict": it["verdict"], "text": it["card"].strip()[:140],
                        "source": str(it.get("source") or "")[:80]})
    return out


# ---------------------------------------------------------------- aset: B-roll, emoji, SFX
BROLL_CHECK = """Kamu editor video. Lihat gambar ini baik-baik.
Narasumber di video (topik: "{topic}") sedang berkata: "{said}"
Gambar akan ditampilkan saat kata "{word}". Yang SEHARUSNYA terlihat: {visual}
1) "seen": sebutkan apa yang BENAR-BENAR terlihat di gambar (maks 20 kata, jujur, jangan menebak dari judul).
2) "object": true hanya bila benda/tempat yang dimaksud terlihat JELAS sebagai fokus gambar.
3) "setting": true hanya bila suasana/konteksnya sesuai yang seharusnya (mis. lampu sorot PANGGUNG konser, bukan
   lampu jalan; teleprompter, bukan stand partitur). Budaya/lokasi asing yang mencolok = false.
Balas JSON: {{"seen": "...", "object": true, "setting": true}}"""


def _broll_ok(path: Path, item: dict, topic: str, said: str) -> tuple[bool, str]:
    """AI vision menilai SATU gambar (maks 640 px — lebih detail dari lembar kontak): benda jelas + suasana sesuai."""
    import io

    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((640, 640))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    res = llm.vision_json(BROLL_CHECK.format(topic=topic[:120], said=said[:300], word=item["word"],
                                             visual=item.get("visual") or item["query_en"]), buf.getvalue())
    return bool(res.get("object")) and bool(res.get("setting")), str(res.get("seen") or "")[:120]


def _broll_asset(item: dict, d: Path, k: int, fmt: str, topic: str = "", said: str = "", progress=lambda m: None) -> dict | None:
    """Kandidat stok (Commons + Pixabay dengan tag yang nyambung) diperiksa SATU PER SATU oleh AI vision: harus jelas
    memperlihatkan bendanya DAN suasananya sesuai (lampu sorot panggung, bukan lampu jalan). Tidak ada yang lolos →
    gambar AI dibuat persis dari deskripsi visual (lalu diperiksa juga). Masih gagal / vision tak tersedia → tanpa
    B-roll. Lebih baik tanpa B-roll daripada B-roll yang membingungkan."""
    from PIL import Image

    from . import assets
    orient = "vertical" if fmt == "short" else "horizontal"
    q = item["query_en"]
    visual = item.get("visual") or q
    keys = set(re.findall(r"[a-z]{3,}", q.lower())) - {"the", "and", "with", "indonesia", "indonesian", "photo", "java", "east"}
    stock = []
    try:
        if item["kind"] == "commons":
            stock += assets.search_commons(q, 3)
        if config.PIXABAY_API_KEY:
            data = assets._cached_get("https://pixabay.com/api/", {
                "key": config.PIXABAY_API_KEY, "q": q[:100], "image_type": "photo",
                "orientation": orient, "per_page": 10, "safesearch": "true"})
            for hit in data.get("hits", []):
                tags = (hit.get("tags") or "").lower()
                if hit.get("largeImageURL") and keys and sum(kw in tags for kw in keys) >= max(1, (len(keys) + 1) // 2):
                    stock.append({"provider": "pixabay", "url": hit["largeImageURL"], "page_url": hit.get("pageURL"),
                                  "author": hit.get("user"), "license": "Pixabay Content License",
                                  "title": hit.get("tags") or q})
    except Exception:  # noqa: BLE001
        pass
    ai = [dict(c, title=visual) for c in assets.search_ai(visual, "portrait" if fmt == "short" else "landscape", 1)]
    dest, tmp = d / "assets" / f"broll_{k}.jpg", d / "assets" / f"broll_{k}_try.jpg"
    pick = None
    for c in stock[:3] + ai:
        is_ai = c["provider"] == "ai"
        progress(f"B-roll '{item['word']}': {'membuat gambar AI dari deskripsi' if is_ai else 'memeriksa foto ' + c['provider']}…")
        try:
            assets._download(c["url"], tmp)
            if is_ai and not c["url"].startswith("cfimg:"):
                assets._crop_watermark(tmp)
            Image.open(tmp).convert("RGB").save(tmp, "JPEG", quality=90)
        except Exception:  # noqa: BLE001
            continue
        try:
            ok, seen = _broll_ok(tmp, item, topic, said)
        except Exception:  # noqa: BLE001 — tanpa vision tidak ada pemeriksaan → jangan pasang apa pun
            tmp.unlink(missing_ok=True)
            return None
        if ok:
            tmp.replace(dest)
            pick = dict(c, check=seen)
            break
        progress(f"B-roll '{item['word']}': ditolak — terlihat {seen}")
    tmp.unlink(missing_ok=True)
    if pick is None:
        progress(f"B-roll '{item['word']}' dilewati — tidak ada gambar yang benar-benar cocok")
        return None
    usage.ai("Gambar", {"commons": "Wikimedia Commons", "pixabay": "Pixabay", "ai": "Gambar AI"}.get(
        pick.get("provider"), pick.get("provider") or "?"), q, None, "B-roll")
    return {"file": f"assets/broll_{k}.jpg", "provider": pick.get("provider"), "page_url": pick.get("page_url"),
            "author": pick.get("author"), "license": pick.get("license"), "title": pick.get("title"),
            "requires_attribution": pick.get("requires_attribution", False), "check": pick["check"]}


def _emoji_png(e: str) -> Path | None:
    """Emoji Noto (Apache 2.0) 512 px, di-cache di data/cache/emoji."""
    with _cache_lock:
        return _emoji_png_locked(e)


def _emoji_png_locked(e: str) -> Path | None:
    cps = "_".join(f"{ord(ch):x}" for ch in e if ord(ch) != 0xFE0F)
    if not re.fullmatch(r"[0-9a-f_]{2,40}", cps):
        return None
    cache = config.DATA_DIR / "cache" / "emoji"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{cps}.png"
    if f.is_file() and f.stat().st_size > 0:
        return f
    from . import assets
    for url in (f"https://fonts.gstatic.com/s/e/notoemoji/latest/{cps}/512.png",        # Noto, Apache 2.0
                f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{cps.replace('_', '-')}.png"):  # CC-BY
        try:
            assets._download(url, f)
            if f.stat().st_size > 200:
                usage.ai("Gambar", "Noto Emoji (Google)" if "gstatic" in url else "Twemoji", e, None, "emoji")
                return f
        except Exception:  # noqa: BLE001
            pass
        f.unlink(missing_ok=True)
    return None


def _sfx(name: str) -> Path | None:
    """SFX dari library user (data/library/sfx atau Drive) bila ada; kalau tidak, dibuat sintetis oleh FFmpeg."""
    from . import audio_lib
    try:
        it = audio_lib.pick_sfx(name)
        if it:
            return audio_lib.local_path(it)
    except Exception:  # noqa: BLE001
        pass
    d = config.DATA_DIR / "cache" / "sfx_builtin"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.wav"
    with _cache_lock:
        return _sfx_make(name, f)


def _sfx_make(name: str, f: Path) -> Path | None:
    if not f.is_file():
        src = {"whoosh": "anoisesrc=d=0.45:c=pink:a=0.6,highpass=f=500,lowpass=f=5000,"
                         "afade=t=in:d=0.22,afade=t=out:st=0.22:d=0.23,volume=0.55",
               "pop": "sine=f=880:d=0.09,afade=t=out:st=0.015:d=0.075,volume=0.7"}.get(name)
        if not src:
            return None
        ff.run(["-f", "lavfi", "-i", src, "-ar", "48000", "-ac", "2", str(f)])
    return f


# ---------------------------------------------------------------- TTS komentar
def _speak(p: dict, text: str, out_wav: Path, offset: float, tag: str) -> tuple[float, list[dict]]:
    from . import tts
    v = p["voice"]
    tokens = tts.tokenize(text)
    if not tokens:
        return 0.0, []
    pron = tts.load_pronunciation()
    spoken_tokens = [tts.apply_pron_text(t["text"], pron) for t in tokens]
    rate, pitch, vol = tts.sentence_params(v, "netral", False)
    mp3 = out_wav.with_suffix(".mp3")
    voice = v.get("voice") or "id-ID-ArdiNeural"
    with usage.timed("Suara", "Edge-TTS (Microsoft)", voice, f"komentar {tag}"):
        bounds = tts.synth(" ".join(spoken_tokens), voice, rate, pitch, vol, mp3)
    ff.to_wav(mp3, out_wav, 48000)
    dur = ff.duration(out_wav)
    aligned = tts.align(tokens, [len(x.split()) for x in spoken_tokens], bounds, dur)
    words = [{"text": w["text"], "start": round(offset + 0.15 + w["start"], 3), "end": round(offset + 0.15 + w["end"], 3),
              "emphasis": w["emphasis"], "scene_id": tag} for w in aligned]
    return dur + 0.45, words


def _still(clip: Path, t: float, out: Path, W: int, H: int, dur: float, wav: Path, cwd: Path) -> None:
    """Segmen intro/outro: frame klip di-blur + gelap, durasi = suara komentar."""
    png = out.with_suffix(".png")
    ff.run(["-ss", f"{max(0.0, t):.3f}", "-i", str(clip), "-frames:v", "1", str(png)], cwd=cwd)
    ff.run(["-loop", "1", "-framerate", str(config.FPS), "-t", f"{dur:.3f}", "-i", str(png), "-i", str(wav),
            "-filter_complex",
            f"[0:v]scale={W}:{H},boxblur=22:2,eq=brightness=-0.13:saturation=0.8,format=yuv420p,setsar=1[v];"
            f"[1:a]adelay=150|150,apad,atrim=0:{dur:.3f},aformat=sample_rates=48000:channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", str(out)], cwd=cwd)
    png.unlink(missing_ok=True)


# ---------------------------------------------------------------- variasi anti-template
PALETTE = ["#FFE600", "#00E5FF", "#7CFF4F", "#FF4FD8", "#FF9F1C", "#B794FF"]


def vary_style(style: dict, seed: int) -> dict:
    """Variasi kecil per klip supaya channel tidak terlihat "template massal" (inauthentic content)."""
    r = random.Random(seed)
    s = dict(style or {})
    s["highlight"] = r.choice(PALETTE)
    s["pos_y"] = round(float(s.get("pos_y") or 60) + r.uniform(-4, 4), 1)
    s["size"] = int(round(float(s.get("size") or 80) * r.uniform(0.94, 1.06)))
    if s.get("anim_in", "pop") in ("pop", "bounce", "slide_up", "fade"):
        s["anim_in"] = r.choice(["pop", "bounce", "slide_up", "fade"])
    if s.get("active_anim", "pop") in ("pop", "bounce"):
        s["active_anim"] = r.choice(["pop", "bounce"])
    return s


# ---------------------------------------------------------------- utama
@usage.stepped("Nilai tambah")
def run(pid: str, params: dict, progress) -> dict:
    """params: {opts: {...}, plan: {...} (opsional, hasil edit user → AI tidak dipanggil), seed}"""
    from . import subtitles
    p = store.load(pid)
    if p["mode"] != "clipper" or not p["assets"].get("clip_path"):
        raise RuntimeError("Potong klip dulu.")
    d = store.project_dir(pid)
    W, H = config.RESOLUTIONS[p["format"]]
    opts = options(params.get("opts") or (p.get("enhance") or {}).get("opts"))
    clip_rel = p["assets"]["clip_path"]
    clip = d / clip_rel
    clip_dur = ff.duration(clip)
    words = subtitles._clip_words(p)
    if not words:
        raise RuntimeError("Klip tidak punya kata bertimestamp (transkrip).")
    sents = _clip_sentences(p, words)
    for k, s in enumerate(sents):
        for i in s:
            words[i]["sent"] = k

    plan = params.get("plan")
    if plan is None and params.get("reuse_plan", True) and (p.get("enhance") or {}).get("plan") \
            and (p.get("enhance") or {}).get("clip_range") == p["assets"].get("clip_range"):
        plan = p["enhance"]["plan"]
    if plan is None:
        plan = make_plan(p, words, sents, opts, progress)
    else:  # rencana lama / hasil edit user: kartu fakta & konteks dipertahankan (tidak dicek ulang)
        given = plan
        plan = _clean_plan(given, len(sents))
        if isinstance(given.get("facts"), list):
            plan["facts"] = [{"sent": int(f["sent"]), "verdict": f.get("verdict") if f.get("verdict") in ("didukung", "bertentangan") else "didukung",
                              "text": str(f["text"]).strip()[:140], "source": str(f.get("source") or "")[:80]}
                             for f in given["facts"] if isinstance(f, dict) and str(f.get("text") or "").strip()
                             and isinstance(f.get("sent"), int) and 0 <= f["sent"] < len(sents)]
        if "context" in given:
            c = given["context"]
            plan["context"] = ({"name": str(c["name"])[:60], "role": str(c.get("role") or "")[:90], "url": str(c.get("url") or "")}
                               if isinstance(c, dict) and str(c.get("name") or "").strip() else None)
    if "facts" not in plan:
        plan["facts"] = _fact_cards(plan, progress) if opts["facts"] else []
    if "context" not in plan:
        plan["context"] = None
        if opts["context"] and plan.get("people"):
            progress("Mencari konteks narasumber di Wikipedia…")
            for person in plan["people"]:
                info = _wiki_role(person["name"])
                if info:
                    plan["context"] = {"name": info["name"], "role": info["role"], "url": info["url"]}
                    break
            if not plan["context"] and plan["people"][0].get("role_guess"):
                plan["context"] = {"name": plan["people"][0]["name"], "role": plan["people"][0]["role_guess"], "url": ""}

    # --- 1) potongan: buang jeda panjang & kata pengisi, lalu pecah per kalimat untuk punch-in
    keep = [w for w in words if not (opts["tighten"] and _core(w["text"]) in FILLERS)]
    spans: list[list[float]] = []
    if opts["tighten"] and keep:
        for w in keep:
            a, b = max(0.0, w["start"] - PAD_W), min(clip_dur, w["end"] + PAD_W)
            if spans and a - spans[-1][1] <= GAP_KEEP:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        if spans[0][0] < 0.6:
            spans[0][0] = 0.0
        if clip_dur - spans[-1][1] < 0.6:
            spans[-1][1] = clip_dur
    else:
        spans = [[0.0, clip_dur]]
    cuts = [words[s[0]]["start"] for s in sents[1:]]
    if p["assets"].get("crop_mode") == "face":  # batas potong kamera auto-reframe ikut jadi batas potongan
        cuts += [sg["t0"] for sg in ((p["assets"].get("reframe") or {}).get("segments") or [])[1:]]
    cuts = sorted(set(cuts))
    shots = []
    for a, b in spans:
        cur = a
        for t in cuts:
            if cur + MIN_SHOT <= t <= b - MIN_SHOT:
                shots.append([cur, t])
                cur = t
        shots.append([cur, b])
    r = random.Random(params.get("seed") or pid)
    punch = _punch_plan(p, clip, shots, r) if opts["punch"] else {}
    progress(f"Merapatkan klip: {clip_dur:.1f} → {sum(b - a for a, b in shots):.1f} dtk, {len(shots)} potongan")

    # --- 2) komentar kreator (TTS)
    tmp = d / "assets" / "enh"
    tmp.mkdir(parents=True, exist_ok=True)
    intro_dur = outro_dur = 0.0
    intro_words, outro_words = [], []
    if opts["commentary"] in ("both", "intro") and plan.get("intro"):
        progress("Suara komentar pembuka…")
        intro_dur, intro_words = _speak(p, plan["intro"], tmp / "intro.wav", 0.0, "intro")
        _still(clip, shots[0][0], tmp / "intro.mp4", W, H, intro_dur, tmp / "intro.wav", d)
    body_dur = 0.0
    timeline = []  # [a, b, out_start, punch-in (zoom, fx, fy) | None]
    for k, (a, b) in enumerate(shots):
        timeline.append([a, b, intro_dur + body_dur, punch.get(k)])
        body_dur += b - a

    def m(t: float) -> float | None:
        for a, b, o, _ in timeline:
            if a - 1e-3 <= t <= b + 1e-3:
                return round(o + t - a, 3)
        return None
    clip_words = []
    for w in keep:
        s0, e0 = m(w["start"]), m(w["end"])
        if s0 is None and e0 is None:
            continue
        s0 = s0 if s0 is not None else max(0.0, (e0 or 0) - (w["end"] - w["start"]))
        e0 = e0 if e0 is not None else s0 + (w["end"] - w["start"])
        clip_words.append({**w, "start": s0, "end": e0, "scene_id": "clip"})
    if opts["commentary"] in ("both", "outro") and plan.get("outro"):
        progress("Suara komentar penutup…")
        outro_dur, outro_words = _speak(p, plan["outro"], tmp / "outro.wav", intro_dur + body_dur, "outro")
        _still(clip, shots[-1][1] - 0.1, tmp / "outro.mp4", W, H, outro_dur, tmp / "outro.wav", d)
    total = intro_dur + body_dur + outro_dur

    def word_time(sent: int, word: str) -> float | None:
        if not (0 <= sent < len(sents)):
            return None
        i = _find_word(words, sents[sent], word)
        return m(words[i]["start"]) if m(words[i]["start"]) is not None else m(words[sents[sent][0]]["start"])

    # --- 3) kartu konteks & fakta (event ASS saat render)
    cards = []
    body0 = intro_dur
    who = (opts.get("creator") or "").strip()[:30]
    if who and intro_dur:
        cards.append({"kind": "voice", "start": 0.0, "end": round(intro_dur, 2), "title": who, "text": ""})
    if who and outro_dur:
        cards.append({"kind": "voice", "start": round(intro_dur + body_dur, 2), "end": round(total, 2), "title": who, "text": ""})
    if plan.get("context") and opts["context"]:
        c0 = max(body0 + 0.3, 2.7)
        cards.append({"kind": "context", "start": round(c0, 2), "end": round(c0 + 3.6, 2),
                      "title": plan["context"]["name"], "text": plan["context"]["role"]})
    for f in plan.get("facts") or []:
        t = word_time(f["sent"], "")
        if t is None:
            continue
        t = max(t, body0 + 0.2)
        while any(c["start"] - 0.3 < t + 4.5 and t < c["end"] + 0.3 for c in cards) and t < total - 5:
            t += 1.0
        if t + 2 < intro_dur + body_dur:
            cards.append({"kind": "fact_ok" if f["verdict"] == "didukung" else "fact_warn", "start": round(t, 2),
                          "end": round(min(t + 4.5, intro_dur + body_dur), 2),
                          "title": "FAKTA" if f["verdict"] == "didukung" else "KONTEKS", "text": f["text"],
                          "source": f.get("source", "")})

    # --- 4) B-roll & emoji & SFX
    broll, emojis, sfx = [], [], []
    if opts["broll"]:
        for k, it in enumerate(plan.get("broll") or []):
            if not (0 <= it["sent"] < len(sents)) or not _has_word(words, sents[it["sent"]], it["word"]):
                continue  # kata tidak ada di kalimat itu (AI mengarang / transkrip berubah)
            t = word_time(it["sent"], it["word"])
            if t is None or t + BROLL_DUR > intro_dur + body_dur or any(abs(t - b["start"]) < BROLL_DUR + 1.5 for b in broll):
                continue
            progress(f"B-roll {k + 1}: mencari gambar untuk '{it['word']}'…")
            said = " ".join(words[i]["text"] for i in sents[it["sent"]])
            a = _broll_asset(it, d, k, p["format"], plan.get("topic") or p.get("title") or "", said, progress)
            if a:
                broll.append({**a, "start": round(t, 2), "end": round(t + BROLL_DUR, 2), "query": it["query_en"]})
                sfx.append(("whoosh", max(0.0, t - 0.12)))
    if opts["emoji"]:
        st = subtitles.normalize_style(p["subtitles"].get("style"), p["format"])
        for it in plan.get("emoji") or []:
            t = word_time(it["sent"], it["word"])
            if t is None or any(abs(t - e["start"]) < 2.0 for e in emojis) \
                    or any(b["start"] - 0.2 <= t <= b["end"] for b in broll):
                continue
            f = _emoji_png(it["emoji"])
            if f:
                emojis.append({"file": str(f), "emoji": it["emoji"], "start": round(t, 2), "end": round(t + EMOJI_DUR, 2),
                               "y": int(H * st["pos_y"] / 100 - H * 0.10 - 170)})
                sfx.append(("pop", t))
    if intro_dur:
        sfx.append(("whoosh", max(0.0, intro_dur - 0.2)))
    if not opts["sfx"]:
        sfx = []

    # --- 5) rakit video
    progress("Merakit video nilai tambah…")
    inputs = ["-i", clip_rel]
    n_in = 1
    has_a = ff.has_audio(clip)
    g = [f"[0:v]split={len(timeline)}" + "".join(f"[vs{k}]" for k in range(len(timeline)))]
    if has_a:
        g.append(f"[0:a]asplit={len(timeline)}" + "".join(f"[as{k}]" for k in range(len(timeline))))
    seq = []
    if intro_dur:
        inputs += ["-i", str((tmp / "intro.mp4").relative_to(d)).replace("\\", "/")]
        g.append(f"[{n_in}:v]fps={config.FPS},format=yuv420p,setsar=1[iv];"
                 f"[{n_in}:a]aformat=sample_rates=48000:channel_layouts=stereo[ia]")
        seq.append("[iv][ia]")
        n_in += 1
    for k, (a, b, _, pz) in enumerate(timeline):
        vz = ""
        if pz:  # perbesar ke arah wajah: titik wajah tetap di posisi layar yang sama (seperti zoom lensa)
            z, fx, fy = pz
            zw, zh = round(W * z / 2) * 2, round(H * z / 2) * 2
            vz = f",scale={zw}:{zh},crop={W}:{H}:{int(fx * (zw - W))}:{int(fy * (zh - H))}"
        g.append(f"[vs{k}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS{vz},fps={config.FPS},format=yuv420p,setsar=1[v{k}]")
        if has_a:
            fo = max(0.0, b - a - 0.012)
            g.append(f"[as{k}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                     f"aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:d=0.012,afade=t=out:st={fo:.3f}:d=0.012[a{k}]")
        else:
            g.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{b - a:.3f}[a{k}]")
        seq.append(f"[v{k}][a{k}]")
    if outro_dur:
        inputs += ["-i", str((tmp / "outro.mp4").relative_to(d)).replace("\\", "/")]
        g.append(f"[{n_in}:v]fps={config.FPS},format=yuv420p,setsar=1[ov];"
                 f"[{n_in}:a]aformat=sample_rates=48000:channel_layouts=stereo[oa]")
        seq.append("[ov][oa]")
        n_in += 1
    g.append("".join(seq) + f"concat=n={len(seq)}:v=1:a=1[cv][ca]")
    cur = "[cv]"
    for k, b in enumerate(broll):
        dur = b["end"] - b["start"]
        inputs += ["-loop", "1", "-framerate", str(config.FPS), "-t", f"{dur:.3f}", "-i", b["file"]]
        g.append(f"[{n_in}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"zoompan=z='1+0.06*on/{int(dur * config.FPS)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={config.FPS},"
                 f"format=rgba,fade=t=in:st=0:d=0.15:alpha=1,fade=t=out:st={dur - 0.15:.3f}:d=0.15:alpha=1,"
                 f"setpts=PTS-STARTPTS+{b['start']:.3f}/TB[bi{k}]")
        g.append(f"{cur}[bi{k}]overlay=0:0:eof_action=pass[bo{k}]")
        cur, n_in = f"[bo{k}]", n_in + 1
    for k, e in enumerate(emojis):
        dur = e["end"] - e["start"]
        inputs += ["-loop", "1", "-framerate", str(config.FPS), "-t", f"{dur:.3f}", "-i", e["file"]]
        g.append(f"[{n_in}:v]scale=170:170,format=rgba,fade=t=in:st=0:d=0.12:alpha=1,"
                 f"fade=t=out:st={dur - 0.2:.3f}:d=0.2:alpha=1,setpts=PTS-STARTPTS+{e['start']:.3f}/TB[ei{k}]")
        g.append(f"{cur}[ei{k}]overlay=(main_w-overlay_w)/2:{max(0, e['y'])}:eof_action=pass[eo{k}]")
        cur, n_in = f"[eo{k}]", n_in + 1
    acur, mix = "[ca]", []
    for k, (name, at) in enumerate(sfx):
        f = _sfx(name)
        if not f:
            continue
        inputs += ["-i", str(f)]
        ms = int(at * 1000)
        g.append(f"[{n_in}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms},volume=0.45[sx{k}]")
        mix.append(f"[sx{k}]")
        n_in += 1
    if mix:
        g.append(f"{acur}{''.join(mix)}amix=inputs={len(mix) + 1}:duration=first:normalize=0[aout]")
        acur = "[aout]"
    out_rel = "assets/clip_enh.mp4"
    ff.run_encode([*inputs, "-filter_complex", ";".join(g), "-map", cur, "-map", acur],
                  ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", out_rel],
                  cwd=d)

    enh = {"opts": opts, "plan": plan, "path": out_rel, "src_mtime": clip.stat().st_mtime,
           "clip_range": p["assets"].get("clip_range"), "duration": round(total, 2),
           "intro_dur": round(intro_dur, 2), "outro_dur": round(outro_dur, 2),
           "removed": round(clip_dur - body_dur, 2), "shots": len(timeline), "punch": len(punch),
           "words": intro_words + clip_words + outro_words, "cards": cards,
           "broll": broll, "emoji": [{k: v for k, v in e.items() if k != "file"} for e in emojis]}

    def apply(q):
        q["enhance"] = enh
        store.touch_stage(q, "assets")
        if any(b.get("provider") == "ai" for b in broll):
            q["publish"]["synthetic_media"] = True
        store.log(q, f"Nilai tambah: {len(intro_words) + len(outro_words)} kata komentar, {len(cards)} kartu, "
                     f"{len(broll)} B-roll, {len(emojis)} emoji, jeda dibuang {enh['removed']} dtk")
    store.update(pid, apply)
    return {k: enh[k] for k in ("duration", "removed", "shots", "intro_dur", "outro_dur")} | \
        {"cards": len(cards), "broll": len(broll), "emoji": len(emojis)}


# ---------------------------------------------------------------- kartu → ASS
def _wrap(text: str, n: int) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > n:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def ass_parts(p: dict, s: dict) -> tuple[str, str]:
    """(baris Style tambahan, event Dialogue) untuk kartu konteks & fakta."""
    if p["mode"] != "clipper" or not valid(p):
        return "", ""
    cards = (p.get("enhance") or {}).get("cards") or []
    if not cards:
        return "", ""
    from .subtitles import _esc, _t
    W, H = config.RESOLUTIONS[p["format"]]
    fs = max(30, int(H * 0.024)) if p["format"] == "short" else max(26, int(H * 0.032))
    font = s.get("font") or "Montserrat ExtraBold"
    styles = (f"Style: Card,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H30141414,&H30141414,0,0,0,0,100,100,0,0,3,18,0,7,0,0,0,1\n"
              f"Style: FactOk,{font},{fs},&H00141414,&H00141414,&H0000E6FF,&H0000E6FF,0,0,0,0,100,100,0,0,3,18,0,8,0,0,0,1\n"
              f"Style: Voice,{font},{int(fs * 0.85)},&H00FFFFFF,&H00FFFFFF,&H00F72F7B,&H00F72F7B,0,0,0,0,100,100,2,0,3,14,0,8,0,0,0,1\n"
              f"Style: FactWarn,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H003B3BE8,&H003B3BE8,0,0,0,0,100,100,0,0,3,18,0,8,0,0,0,1")
    ev = []
    y = int(H * (0.105 if p["format"] == "short" else 0.07))
    for c in cards:
        if c["kind"] == "voice":  # penanda: yang bicara sekarang kreator, bukan narasumber
            txt = f"{{\\an8\\pos({W // 2},{int(H * 0.31)})\\fad(150,150)}}KOMENTAR · {_esc(c['title'].upper())}"
            ev.append(f"Dialogue: 4,{_t(c['start'])},{_t(c['end'])},Voice,,0,0,0,,{txt}")
        elif c["kind"] == "context":
            body = "\\N".join(_esc(x) for x in _wrap(c["text"], 30))
            txt = (f"{{\\an7\\pos({int(W * 0.06)},{y})\\fad(220,260)}}{{\\fs{int(fs * 1.15)}}}{_esc(c['title'].upper())}"
                   f"{{\\fs{fs}}}\\N{body}")
            ev.append(f"Dialogue: 4,{_t(c['start'])},{_t(c['end'])},Card,,0,0,0,,{txt}")
        else:
            body = "\\N".join(_esc(x) for x in _wrap(c["text"], 28))
            style = "FactOk" if c["kind"] == "fact_ok" else "FactWarn"
            txt = f"{{\\an8\\pos({W // 2},{y})\\fad(200,260)}}{{\\b1}}{_esc(c['title'])}:{{\\b0}} {body}"
            ev.append(f"Dialogue: 4,{_t(c['start'])},{_t(c['end'])},{style},,0,0,0,,{txt}")
    return styles, "\n".join(ev) + "\n"


def credits(p: dict) -> list[str]:
    out = []
    for b in (p.get("enhance") or {}).get("broll") or []:
        if b.get("requires_attribution") or b.get("provider") == "commons":
            line = f"• \"{b.get('title') or 'Gambar'}\" oleh {b.get('author') or 'tidak diketahui'}, {b.get('license')}"
            if b.get("page_url"):
                line += f" — {b['page_url']}"
            out.append(line)
    ctx = ((p.get("enhance") or {}).get("plan") or {}).get("context") or {}
    if ctx.get("url"):
        out.append(f"• Konteks narasumber: {ctx['url']}")
    return out
