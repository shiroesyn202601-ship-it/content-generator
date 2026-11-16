"""Tahap 2 (scratch): narasi (template ala tab Generate Narasi) -> scene dengan penanda emosi."""
import re

from .. import store
from ..store import EMOTION_VOICE, MOODS
from . import factsource, llm, prompts


ROLES = ("hook", "setup", "eskalasi", "twist", "payoff", "loop")

# Skema strict (Groq structured outputs): enum emosi/mood/role tidak bisa melenceng, field tidak hilang.
SCENE_SCHEMA = llm.strict({
    "role": {"type": "string", "enum": list(ROLES)}, "text": llm.STR, "on_screen_text": llm.STR,
    "emotion": {"type": "string", "enum": list(EMOTION_VOICE)}, "mood": {"type": "string", "enum": list(MOODS)},
    "visual_query": llm.STR, "sfx": llm.STR, "sources": llm.arr(llm.STR)})
SCENES_SCHEMA = llm.strict({"scenes": llm.arr(SCENE_SCHEMA)})
SCRIPT_SCHEMA = llm.strict({
    "title": llm.STR,
    "framework": llm.strict({k: llm.STR for k in ("hook", "konflik", "eskalasi", "payoff", "cta")}),
    "scenes": llm.arr(SCENE_SCHEMA)})
CLAIMS_SCHEMA = llm.strict({"claims": llm.arr(llm.strict({
    "scene_id": llm.STR, "claim": llm.STR,
    "verdict": {"type": "string", "enum": ["didukung", "tanpa_sumber", "bertentangan"]},
    "source_id": llm.STR, "quote": llm.STR, "fix": llm.STR}))})


def normalize_scenes(scenes: list[dict]) -> list[dict]:
    """Pastikan setiap scene punya field kanonik & id unik (dipakai juga saat edit manual)."""
    out, texts = [], set()
    for i, s in enumerate(scenes):
        key = " ".join((s.get("text") or "").replace("*", "").lower().split())
        if key and key in texts:
            continue  # AI kadang mengulang scene yang sama persis
        texts.add(key)
        emo = (s.get("emotion") or "netral").lower()
        mood = (s.get("mood") or "santai").lower()
        out.append({
            "id": s.get("id") or f"s{i + 1}",
            "text": (s.get("text") or "").strip(),
            "emotion": emo if emo in EMOTION_VOICE else "netral",
            "mood": mood if mood in MOODS else "santai",
            "visual_query": (s.get("visual_query") or "").strip(),
            "sfx": (s.get("sfx") or "").strip(),
            # opsional (struktur retensi): peran scene & teks hook di layar (scene pertama)
            "role": role if (role := (s.get("role") or "").strip().lower()) in ROLES else "",
            "on_screen_text": (s.get("on_screen_text") or "").strip() if i == 0 else "",
            "sources": [str(x) for x in (s.get("sources") or []) if x][:5],
            **({"chapter": s["chapter"]} if isinstance(s.get("chapter"), int) else {}),
        })
    seen = set()
    for i, s in enumerate(out):
        if s["id"] in seen:
            s["id"] = f"s{i + 1}x"
        seen.add(s["id"])
    return out


# Kecepatan bicara edge-tts (kata/detik pada speed 1.0), diukur dengan teks Indonesia.
VOICE_WPS = {"id-ID-ArdiNeural": 2.5, "id-ID-GadisNeural": 2.0}
TARGET_SEC = {"short": 50, "long": 480}  # Shorts: sisakan ruang di bawah 60 dtk untuk jeda & ekor


def word_budget(p: dict, seconds: int | None = None) -> int:
    """Jumlah kata maksimum supaya VO pas target durasi: T = kata/(wps*speed) + (kata/9 kalimat)*jeda."""
    v = p["voice"]
    wps = VOICE_WPS.get(v.get("voice"), 2.2) * float(v.get("speed", 1.0))
    per_word = 1 / wps + (int(v.get("pause_ms", 350)) / 1000) / 9
    return int((seconds or TARGET_SEC[p["format"]]) / per_word)


def _count_words(scenes: list[dict]) -> int:
    return sum(len(s.get("text", "").replace("*", "").split()) for s in scenes)


def _sources(p: dict, idea: dict, params: dict, progress) -> list[dict]:
    """Sumber Wikipedia: pakai yang sudah ada untuk ide ini, atau riset baru."""
    if params.get("use_sources") is False:
        return []
    old = p["research"].get("sources") or []
    if old and p["research"].get("sources_for") == idea.get("title") and not params.get("refresh_sources"):
        return old
    try:
        topic = p["research"].get("keyword") or idea.get("title", "")
        found = factsource.gather(topic, idea, progress)
    except Exception as e:  # noqa: BLE001 — tanpa internet/Wikipedia tetap bisa menulis naskah
        progress(f"Riset Wikipedia gagal ({e}) — naskah ditulis tanpa sumber.")
        return []
    progress(f"Riset fakta selesai: {len(found)} artikel Wikipedia"
             + (": " + ", ".join(s["title"] for s in found) if found else " (tidak ditemukan)"))

    def apply(q):
        q["research"]["sources"] = found
        q["research"]["sources_for"] = idea.get("title")
    store.update(p["id"], apply)
    return found


def run_factcheck(scenes: list[dict], sources: list[dict], provider=None, progress=lambda m: None) -> list[dict]:
    progress("Cek fakta: AI membandingkan setiap klaim dengan sumber Wikipedia…")
    sys_, usr = prompts.factcheck(scenes, factsource.as_prompt(sources))
    res = llm.chat_json(sys_, usr, provider, temperature=0.1, schema=CLAIMS_SCHEMA)
    ids = {s["id"] for s in scenes}
    out = []
    for c in res.get("claims", []):
        v = str(c.get("verdict") or "").lower().replace(" ", "_")
        if c.get("scene_id") not in ids or v not in ("didukung", "tanpa_sumber", "bertentangan"):
            continue
        out.append({"scene_id": c["scene_id"], "claim": str(c.get("claim") or ""), "verdict": v,
                    "source_id": str(c.get("source_id") or ""), "quote": str(c.get("quote") or "")[:300],
                    "fix": str(c.get("fix") or "") if v != "didukung" else ""})
    return out


def factcheck(pid: str, params: dict, progress) -> dict:
    """Aksi 'Cek fakta' (bisa diulang setelah edit manual)."""
    p = store.load(pid)
    scenes = p["script"].get("scenes") or []
    if not scenes:
        raise RuntimeError("Naskah masih kosong.")
    idea = _idea(p) if p["research"].get("selected") is not None else {}
    sources = _sources(p, idea, params, progress)
    claims = run_factcheck(scenes, sources, params.get("provider"), progress)

    def apply(q):
        q["script"]["claims"] = claims
        q["script"]["claims_at"] = store.now_iso()
        store.log(q, "Cek fakta: " + _claims_summary(claims))
    store.update(pid, apply)
    return {"claims": len(claims), "summary": _claims_summary(claims)}


def _claims_summary(claims: list[dict]) -> str:
    n = {v: sum(c["verdict"] == v for c in claims) for v in ("didukung", "tanpa_sumber", "bertentangan")}
    return f"{n['didukung']} didukung, {n['tanpa_sumber']} tanpa sumber, {n['bertentangan']} bertentangan"


NARR_TEMPLATES = ("fakta", "tragedi", "kisah-hidup", "custom")
SPLIT_SCHEMA = llm.strict({
    "title": llm.STR,
    "framework": llm.strict({k: llm.STR for k in ("hook", "konflik", "eskalasi", "payoff", "cta")}),
    "scenes": llm.arr(llm.strict({
        "start": llm.INT, "role": {"type": "string", "enum": list(ROLES)}, "emphasis": llm.arr(llm.STR),
        "on_screen_text": llm.STR, "emotion": {"type": "string", "enum": list(EMOTION_VOICE)},
        "mood": {"type": "string", "enum": list(MOODS)}, "visual_query": llm.STR, "sfx": llm.STR,
        "sources": llm.arr(llm.STR)}))})


def _idea(p: dict) -> dict:
    sel = p["research"].get("selected")
    ideas = p["research"].get("ideas") or []
    if sel is None or not (0 <= sel < len(ideas)):
        raise RuntimeError("Belum ada ide terpilih di tahap 1.")
    return ideas[sel]


def duration_of(p: dict, params: dict | None = None) -> int:
    d = (params or {}).get("duration") or p["script"].get("narr_duration") or p["research"].get("duration")
    return int(d or (50 if p["format"] == "short" else 480))


def word_range(p: dict, seconds: int) -> tuple[int, int]:
    """Rentang kata untuk durasi target, dari kecepatan suara proyek (bukan angka tetap 3.5-4.2 kata/dtk,
    karena Edge-TTS Indonesia hanya +-2-2.5 kata/dtk -> naskah tidak melewati 60 dtk Shorts)."""
    mx = word_budget(p, seconds)
    return int(mx * 0.85), mx


def build_narration_prompt(p: dict, template: str, seconds: int) -> str:
    idea = _idea(p)
    wmin, wmax = word_range(p, seconds)
    return prompts.narration_prompt(template, idea.get("title", ""), idea.get("description") or idea.get("angle", ""),
                                    seconds, wmin, wmax)


def sentences(text: str) -> list[str]:
    """Pecah narasi jadi kalimat (paragraf juga jadi batas)."""
    out = []
    for para in re.split(r"\n", text or ""):
        out += [s.strip() for s in re.split(r"(?<=[.!?…])\s+", para.strip()) if s.strip()]
    return out


def _mark(text: str, words: list[str]) -> str:
    for w in words[:2]:
        w = (w or "").strip(" *")
        if not w or f"*{w}" in text:
            continue
        m = re.search(r"(?<![\w*])" + re.escape(w) + r"(?![\w*])", text, re.I)
        if m:
            text = text[:m.start()] + f"*{m.group(0)}*" + text[m.end():]
    return text


def _split_part(sents: list[str], title: str, fmt: str, provider, n: str | None = None) -> tuple[dict, list[dict]]:
    """Satu bagian narasi → scene (teks disusun kode dari kalimat asli). Return (framework, scenes)."""
    sys_, usr = prompts.split_narration(sents, title, fmt, n)
    res = llm.chat_json(sys_, usr, provider, temperature=0.3, schema=SPLIT_SCHEMA)
    raw = [s for s in (res.get("scenes") or []) if isinstance(s, dict)]
    # Batas scene: start naik & unik, scene pertama mulai dari kalimat 0, sisa kalimat ikut scene terakhir.
    marks, seen = [], set()
    for s in sorted(raw, key=lambda s: int(s.get("start") or 0)):
        st = max(0, min(len(sents) - 1, int(s.get("start") or 0)))
        if st not in seen:
            seen.add(st)
            marks.append((st, s))
    if not marks:
        marks = [(i, {}) for i in range(0, len(sents), 2)]
    if marks[0][0] != 0:
        marks[0] = (0, marks[0][1])
    scenes = []
    for k, (st, s) in enumerate(marks):
        en = marks[k + 1][0] if k + 1 < len(marks) else len(sents)
        text = _mark(" ".join(sents[st:en]), [str(w) for w in (s.get("emphasis") or [])])
        scenes.append({**{x: s.get(x) for x in ("role", "emotion", "mood", "visual_query", "sfx", "on_screen_text",
                                                 "sources")}, "text": text})
    return res.get("framework") or {}, scenes


BOOKEND = ("pembuka", "penutup", "intro", "outro", "prolog", "epilog")


def split(pid: str, params: dict, progress) -> dict:
    """Aksi 'Pecah jadi scene': narasi (teks utuh) -> scene + emosi/mood/visual/SFX. Teks narasi TIDAK diubah:
    AI hanya memilih nomor kalimat awal tiap scene, kalimat disusun ulang oleh kode.
    Narasi berbab (baris "## Judul", video panjang) dipecah per bab → tiap scene tahu babnya (kartu judul bab
    & chapter YouTube), dan bab tetap ada walau narasi diedit manual."""
    from . import longform
    p = store.load(pid)
    narr = (p["script"].get("narration") or "").strip()
    if not narr:
        raise RuntimeError("Narasi masih kosong.")
    title = p["script"].get("title") or _idea(p).get("title", p["title"])
    parts = longform.parse_parts(narr.replace("*", ""))
    chapters, framework, scenes, num = [], {}, [], 0
    titled = any(t for t, _ in parts)
    for k, (ptitle, ptext) in enumerate(parts):
        sents = sentences(ptext)
        if not sents:
            continue
        if titled:
            ptitle = ptitle or longform.INTRO_TITLE
            n_words = len(ptext.split())
            per = max(1, round(n_words / 38))
            rng = f"{max(1, per - 1)}-{per + 1} scene"
            progress(f"AI memecah bagian {k + 1}/{len(parts)} '{ptitle}' ({len(sents)} kalimat) jadi scene…")
        else:
            rng = None
            progress(f"AI memecah narasi ({len(sents)} kalimat) menjadi scene + arahan visual, emosi, musik…")
        fw, sc = _split_part(sents, f"{title} — {ptitle}" if titled else title, p["format"], params.get("provider"), rng)
        framework = framework or fw
        if titled:
            bookend = ptitle.strip().lower() in BOOKEND
            num += 0 if bookend else 1
            chapters.append({"title": ptitle, "n": 0 if bookend else num, "first": len(scenes)})
            for s in sc:
                s["chapter"] = len(chapters) - 1
                if scenes:
                    s["on_screen_text"] = ""
        scenes += sc
    for i, s in enumerate(scenes):
        s["id"] = f"s{i + 1}"
    scenes = normalize_scenes(scenes)
    for c in chapters:
        c["scene_id"] = scenes[min(c.pop("first"), len(scenes) - 1)]["id"] if scenes else None
    claims = []
    sources = (p["research"].get("sources") or []) if p["research"].get("sources_for") == _idea(p).get("title") else []
    if params.get("factcheck", True) and p["script"].get("narr_template") in ("fakta", "tragedi") and sources:
        try:
            claims = run_factcheck(scenes, sources, params.get("provider"), progress)
        except llm.LLMError as e:
            progress(f"Cek fakta gagal ({e}) — bisa diulang dengan tombol Cek Fakta.")

    def apply(q):
        q["script"].update({"framework": framework, "scenes": scenes, "chapters": chapters,
                            "claims": claims, "claims_at": store.now_iso() if claims else None})
        store.touch_stage(q, "script")
        store.log(q, f"AI: narasi dipecah jadi {len(scenes)} scene ({_count_words(scenes)} kata)"
                     + (f", {len(chapters)} bab" if chapters else ""))
    store.update(pid, apply)
    return {"scenes": len(scenes), "words": _count_words(scenes), "chapters": len(chapters),
            "factcheck": _claims_summary(claims) if claims else ""}


def narrate(pid: str, params: dict, progress) -> dict:
    """Aksi 'Generate Narasi' (meniru tab Generate Narasi repo referensi): template Fakta Menarik / Tragedi /
    Kisah Hidup / Custom -> teks narasi + hook thumbnail, lalu otomatis dipecah jadi scene."""
    p = store.load(pid)
    idea = _idea(p)
    template = (params.get("template") or p["script"].get("narr_template")
                or p["research"].get("narr_template") or "fakta")
    if template not in NARR_TEMPLATES:
        template = "fakta"
    seconds = duration_of(p, params)
    if template == "custom":
        prompt = (params.get("custom_prompt") if params.get("custom_prompt") is not None
                  else p["script"].get("narr_custom") or "").strip()
        if not prompt:
            raise RuntimeError("Prompt custom masih kosong.")
    else:
        prompt = build_narration_prompt(p, template, seconds)
    use_src = params.get("use_sources", template in ("fakta", "tragedi"))
    sources = _sources(p, idea, params, progress) if use_src else []
    progress(f"AI menulis narasi '{prompts.TEMPLATE_LABEL[template]}' untuk '{idea.get('title', '')}'"
             + (f" (berdasarkan {len(sources)} sumber)…" if sources else "…"))
    extra = (params.get("extra") or "").strip()  # arahan tambahan (Shorts massal: variasi hook, opini kreator)
    res = llm.chat_json(prompts.NARRATION_SYSTEM, prompt + (f"\n\nARAHAN TAMBAHAN (wajib):\n{extra}" if extra else "")
                        + prompts.narration_sources(factsource.as_prompt(sources)),
                        params.get("provider"), temperature=0.7)
    narr = str(res.get("narasi") or res.get("narration") or "").strip()
    if not narr:
        raise RuntimeError("AI tidak mengembalikan narasi. Coba lagi.")
    hook = str(res.get("hook") or "").strip().strip('"*') or idea.get("hook") or idea.get("title", "")
    words = len(narr.split())
    wmin, wmax = word_range(p, seconds)

    def apply(q):
        q["script"].update({"narration": narr, "hook_text": hook, "narr_template": template,
                            "narr_duration": seconds, "narr_prompt": prompt,
                            "title": idea.get("title") or q["script"].get("title")})
        if template == "custom":
            q["script"]["narr_custom"] = prompt
        q["title"] = q["script"]["title"] or q["title"]
        store.touch_stage(q, "script")
        store.log(q, f"AI: narasi {prompts.TEMPLATE_LABEL[template]} {words} kata (target {wmin}-{wmax})")
    store.update(pid, apply)
    out = {"words": words, "target": [wmin, wmax], "sources": len(sources)}
    if params.get("split", True):
        out.update(split(pid, params, progress))
    return out


def generate(pid: str, params: dict, progress) -> dict:
    """Dipakai produksi massal: narasi (template proyek) + pecah scene."""
    return narrate(pid, params, progress)
