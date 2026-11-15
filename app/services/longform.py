"""Video panjang (Dari Nol, 16:9): naskah per BAB supaya hasilnya rapi & tidak membosankan.

Kenapa tidak satu prompt seperti Shorts: narasi 8 menit (±1.000 kata) dari satu panggilan LLM cenderung
memendek, mengulang, dan alurnya datar. Di sini:
  1) outline: cold open (teaser), 4-7 bab (judul pendek = chapter YouTube, poin dari sumber), penutup;
  2) tiap bagian ditulis TERPISAH dengan anggaran kata sendiri + 2 kalimat terakhir bagian sebelumnya
     (sambungan mulus) + akhir bab berupa jembatan ke bab berikutnya (open loop → penonton bertahan);
  3) narasi disimpan dengan baris "## Judul bab" → scriptgen.split memecah per bab (scene tahu babnya).
Hasil bab dipakai di tempat lain:
  - kartu judul bab di video (ass_chapters → subtitles.export),
  - chapter YouTube "00:00 Judul" di deskripsi (chapter_marks → youtube.gen_metadata).
"""
import re

from .. import config, store
from . import factsource, llm, prompts, scriptgen

INTRO_TITLE, OUTRO_TITLE = "Pembuka", "Penutup"

OUTLINE_SCHEMA = llm.strict({
    "title": llm.STR,
    "cold_open": llm.STR,
    "chapters": llm.arr(llm.strict({"title": llm.STR, "summary": llm.STR, "points": llm.arr(llm.STR),
                                    "share": llm.INT})),
    "outro": llm.STR,
})

TONE = {
    "fakta": "dokumenter edukatif yang memikat: rasa penasaran, fakta mengejutkan, penjelasan jernih",
    "tragedi": "dokumenter jurnalistik: tenang, objektif, ketegangan dibangun lewat kronologi & data",
    "kisah-hidup": "storytelling biografi yang emosional: dilema, titik balik, pelajaran hidup",
    "custom": "dokumenter YouTube yang memikat dan mudah diikuti",
}


def _parts_plan(outline: dict, wmax: int) -> list[dict]:
    """Anggaran kata tiap bagian: pembuka ±9%, penutup ±7%, sisanya dibagi bab sesuai 'share'."""
    chs = [c for c in outline.get("chapters") or [] if (c.get("title") or "").strip()][:8]
    if len(chs) < 2:
        raise RuntimeError("AI tidak membuat outline bab yang valid. Coba lagi.")
    w_intro, w_outro = max(45, int(wmax * 0.09)), max(40, int(wmax * 0.07))
    rest = max(150, wmax - w_intro - w_outro)
    shares = [max(5, int(c.get("share") or 0)) for c in chs]
    tot = sum(shares)
    parts = [{"kind": "intro", "title": INTRO_TITLE, "words": w_intro, "brief": outline.get("cold_open", "")}]
    for c, s in zip(chs, shares):
        parts.append({"kind": "chapter", "title": c["title"].strip().strip("#").strip()[:60], "words": int(rest * s / tot),
                      "brief": c.get("summary", ""), "points": [str(x) for x in c.get("points") or []][:6]})
    parts.append({"kind": "outro", "title": OUTRO_TITLE, "words": w_outro, "brief": outline.get("outro", "")})
    return parts


def _outline_prompt(p: dict, idea: dict, seconds: int, template: str, sources_text: str) -> tuple[str, str]:
    minutes = max(2, round(seconds / 60))
    n = "3-4" if minutes <= 4 else "4-6" if minutes <= 9 else "5-7"
    system = ("Kamu kepala penulis channel YouTube dokumenter berbahasa Indonesia. Kamu merancang struktur video "
              "panjang yang ditonton sampai habis (retensi tinggi).")
    user = f"""Rancang outline video YouTube ±{minutes} menit.
Judul/ide: "{idea.get('title', '')}" — {idea.get('description') or idea.get('angle', '')}
Gaya: {TONE.get(template, TONE['custom'])}.

Isi:
- "cold_open": rencana PEMBUKA 20-40 detik. Mulai langsung dari momen/fakta/pertanyaan paling menarik
  (bukan "halo guys", bukan perkenalan channel), janjikan apa yang akan terjawab di akhir video.
- "chapters": {n} bab berurutan yang membangun cerita (konteks → inti → puncak/twist → dampak).
  "title" maks 5 kata, jelas & menarik (dipakai sebagai chapter YouTube). "summary" 1-2 kalimat isi bab.
  "points" 2-5 poin konkret (fakta/kejadian/angka) yang dibahas. "share" = persen durasi bab (total ±100).
- "outro": rencana PENUTUP: rangkum jawaban dari janji pembuka + satu pertanyaan untuk komentar
  (jangan minta like/subscribe).
{prompts.FACT_RULES}
{prompts.sources_block(sources_text)}
Balas JSON: {{"title": "...", "cold_open": "...", "chapters": [{{"title": "...", "summary": "...",
"points": ["..."], "share": 20}}], "outro": "..."}}"""
    return system, user


def _part_prompt(p: dict, idea: dict, outline_txt: str, part: dict, nxt: dict | None, prev_tail: str,
                 template: str, sources_text: str) -> tuple[str, str]:
    lo, hi = int(part["words"] * 0.85), part["words"]
    if part["kind"] == "intro":
        job = ("Tulis PEMBUKA (cold open). Kalimat pertama langsung menghentak: fakta/pertanyaan/momen paling "
               "menarik. Bangun rasa penasaran, lalu janjikan apa yang akan terjawab. Jangan menyapa penonton, "
               "jangan menyebut nama channel, jangan menyebut kata 'bab'.")
    elif part["kind"] == "outro":
        job = ("Tulis PENUTUP: jawab janji di pembuka, rangkum 2-3 hal terpenting dengan kalimat baru (bukan "
               "mengulang), lalu akhiri dengan SATU pertanyaan yang memancing komentar. Jangan minta like/subscribe.")
    else:
        job = (f"Tulis isi bab \"{part['title']}\": {part.get('brief', '')}\nPoin yang wajib dibahas: "
               + "; ".join(part.get("points") or []) + ".\nMasuk ke bab secara natural (tanpa menyebut 'bab'). "
               + (f"Kalimat terakhir = jembatan rasa penasaran ke bagian berikutnya (\"{nxt['title']}\") tanpa "
                  f"membocorkan isinya." if nxt and nxt["kind"] == "chapter" else
                  "Akhiri dengan kalimat yang menutup pembahasan ini."))
    system = prompts.NARRATION_SYSTEM.replace(
        '"hook": "satu kalimat hook thumbnail maks 7-10 kata, clickbait, bikin penasaran, tanpa typo, tanpa tanda kutip"',
        '"hook": ""')
    user = f"""Video YouTube panjang: "{idea.get('title', '')}". Gaya narasi: {TONE.get(template, TONE['custom'])}.
Outline keseluruhan (untuk konteks, JANGAN tulis bagian lain):
{outline_txt}

TUGAS: {job}
Panjang {lo}-{hi} kata (dibacakan narator ±{round(hi / 2.3)} detik). Kalimat pendek-sedang yang enak didengar,
variasikan ritme. Tanpa judul, label, nomor, emoji, atau tanda kurung arahan.
{('Sambung mulus dari akhir bagian sebelumnya: "' + prev_tail + '"') if prev_tail else ''}
{prompts.FACT_RULES}{prompts.narration_sources(sources_text)}"""
    return system, user


def _outline_text(parts: list[dict]) -> str:
    out = []
    for i, x in enumerate(parts):
        pts = f" — {'; '.join(x['points'])}" if x.get("points") else ""
        out.append(f"{i}. {x['title']}: {x.get('brief', '')}{pts}")
    return "\n".join(out)


def write(pid: str, params: dict, progress) -> dict:
    """Aksi 'Tulis naskah per bab' (video panjang). Menulis narasi berbab lalu memecahnya jadi scene."""
    p = store.load(pid)
    idea = scriptgen._idea(p)
    template = params.get("template") or p["script"].get("narr_template") or p["research"].get("narr_template") or "fakta"
    if template not in scriptgen.NARR_TEMPLATES:
        template = "fakta"
    seconds = scriptgen.duration_of(p, params)
    wmin, wmax = scriptgen.word_range(p, seconds)
    use_src = params.get("use_sources", template in ("fakta", "tragedi"))
    sources = scriptgen._sources(p, idea, params, progress) if use_src else []
    stext = factsource.as_prompt(sources)

    progress(f"[1/3] Outline: AI merancang pembuka, bab & penutup (±{round(seconds / 60)} menit)…")
    outline = llm.chat_json(*_outline_prompt(p, idea, seconds, template, stext), params.get("provider"),
                            temperature=0.6, schema=OUTLINE_SCHEMA)
    parts = _parts_plan(outline, wmax)
    otxt = _outline_text(parts)
    texts, tail = [], ""
    for i, part in enumerate(parts):
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        progress(f"[2/3] Menulis bagian {i + 1}/{len(parts)}: {part['title']} (±{part['words']} kata)…")
        res = llm.chat_json(*_part_prompt(p, idea, otxt, part, nxt, tail, template,
                                          stext if part["kind"] == "chapter" or i == 0 else ""),
                            params.get("provider"), temperature=0.7)
        txt = str(res.get("narasi") or res.get("narration") or "").strip()
        txt = "\n".join(ln for ln in txt.splitlines() if not ln.strip().startswith("#")).strip()
        if not txt:
            raise RuntimeError(f"AI tidak mengembalikan teks untuk bagian '{part['title']}'. Coba lagi.")
        texts.append(txt)
        sents = scriptgen.sentences(txt)
        tail = " ".join(sents[-2:])[:400]
    narration = "\n\n".join(f"## {part['title']}\n{t}" for part, t in zip(parts, texts))
    words = sum(len(t.split()) for t in texts)
    hook = idea.get("hook") or idea.get("title", "")

    def apply(q):
        q["script"].update({"narration": narration, "hook_text": q["script"].get("hook_text") or hook,
                            "narr_template": template, "narr_duration": seconds, "narr_mode": "bab",
                            "outline": {"title": outline.get("title") or idea.get("title"), "parts": parts},
                            "title": idea.get("title") or q["script"].get("title")})
        q["title"] = q["script"]["title"] or q["title"]
        store.touch_stage(q, "script")
        store.log(q, f"AI: naskah per bab — {len(parts) - 2} bab + pembuka & penutup, {words} kata "
                     f"(target {wmin}-{wmax})")
    store.update(pid, apply)
    progress(f"[3/3] Memecah {len(parts)} bagian jadi scene + arahan visual…")
    out = scriptgen.split(pid, params, progress)
    return {"words": words, "target": [wmin, wmax], "chapters": len(parts), **out}


# ---------------------------------------------------------------- bab → kartu judul & chapter YouTube
def chapters(p: dict) -> list[dict]:
    """[{title, scene_id, n}] — n = nomor bab (0 untuk pembuka/penutup)."""
    return [c for c in (p["script"].get("chapters") or []) if c.get("scene_id")]


def chapter_marks(p: dict) -> list[tuple[float, str]]:
    """(detik, judul) per bab dari timing VO. Kosong bila tidak memenuhi aturan chapter YouTube
    (mulai 00:00, minimal 3 bab, tiap bab ≥ 10 detik)."""
    tim = {t["scene_id"]: float(t["start"]) for t in p["audio"].get("scene_timings") or []}
    marks = [(tim[c["scene_id"]], c["title"]) for c in chapters(p) if c["scene_id"] in tim]
    if not marks:
        return []
    marks.sort()
    marks[0] = (0.0, marks[0][1])
    out = [marks[0]]
    for t, title in marks[1:]:
        if t - out[-1][0] >= 10:
            out.append((t, title))
    return out if len(out) >= 3 else []


def fmt_ts(sec: float, long: bool) -> str:
    s = int(sec)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if long else f"{s // 60:02d}:{s % 60:02d}"


def chapters_text(p: dict) -> str:
    marks = chapter_marks(p)
    if not marks:
        return ""
    long = (p["audio"].get("duration") or 0) >= 3600
    return "Bab:\n" + "\n".join(f"{fmt_ts(t, long)} {title}" for t, title in marks)


def ass_chapters(p: dict, s: dict) -> tuple[str, str]:
    """Kartu judul bab (kiri atas, 3.5 dtk) di awal setiap bab — kecuali pembuka. Hanya video panjang."""
    if p["mode"] != "scratch" or p["format"] != "long" or not p["render"].get("chapter_cards", True):
        return "", ""
    tim = {t["scene_id"]: float(t["start"]) for t in p["audio"].get("scene_timings") or []}
    chs = [c for c in chapters(p) if c["scene_id"] in tim and c.get("n")]
    if not chs:
        return "", ""
    from .subtitles import _esc, _t
    W, H = config.RESOLUTIONS[p["format"]]
    font = s.get("font") or "Poppins SemiBold"
    fs = int(H * 0.05)
    styles = (f"Style: ChapK,{font},{int(fs * 0.5)},&H0000C8FF,&H0000C8FF,&H50101010,&H50101010,-1,0,0,0,100,100,4,0,3,14,0,7,0,0,0,1\n"
              f"Style: ChapT,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H50101010,&H50101010,-1,0,0,0,100,100,0,0,3,18,0,7,0,0,0,1")
    x, y = int(W * 0.05), int(H * 0.07)
    ev = []
    for c in chs:
        t0 = tim[c["scene_id"]] + 0.15
        t1 = t0 + 3.6
        ev.append(f"Dialogue: 4,{_t(t0)},{_t(t1)},ChapK,,0,0,0,,"
                  f"{{\\an7\\move({x - 30},{y},{x},{y},0,260)\\fad(200,300)}}BAB {c['n']}")
        ev.append(f"Dialogue: 4,{_t(t0 + 0.12)},{_t(t1)},ChapT,,0,0,0,,"
                  f"{{\\an7\\move({x - 30},{y + int(fs * 0.95)},{x},{y + int(fs * 0.95)},0,300)\\fad(220,300)}}"
                  f"{_esc(c['title'])}")
    return styles, "\n".join(ev) + "\n"


def parse_parts(narration: str) -> list[tuple[str | None, str]]:
    """Narasi berbab ("## Judul" di baris sendiri) → [(judul | None, teks)]. Tanpa '##' → satu bagian."""
    parts: list[list] = [[None, []]]
    for ln in (narration or "").splitlines():
        m = re.match(r"^\s*##\s*(.+?)\s*$", ln)
        if m:
            parts.append([m.group(1).strip()[:60], []])
        else:
            parts[-1][1].append(ln)
    out = [(t, "\n".join(x).strip()) for t, x in parts]
    return [(t, x) for t, x in out if x]
