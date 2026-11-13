"""Tahap 2 (scratch): kerangka cerita + naskah per scene dengan penanda emosi."""
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


def word_budget(p: dict) -> int:
    """Jumlah kata maksimum supaya VO pas target durasi: T = kata/(wps*speed) + (kata/9 kalimat)*jeda."""
    v = p["voice"]
    wps = VOICE_WPS.get(v.get("voice"), 2.2) * float(v.get("speed", 1.0))
    per_word = 1 / wps + (int(v.get("pause_ms", 350)) / 1000) / 9
    return int(TARGET_SEC[p["format"]] / per_word)


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
    idea = p["research"]["ideas"][p["research"]["selected"]] if p["research"].get("selected") is not None else {}
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


def generate(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    idea = p["research"]["ideas"][p["research"]["selected"]]
    budget = word_budget(p)
    sources = _sources(p, idea, params, progress)
    src_text = factsource.as_prompt(sources)
    progress(f"Menulis naskah dari ide '{idea.get('title', '')}' (target ≤{budget} kata"
             + (f", berdasarkan {len(sources)} sumber)…" if sources else ", tanpa sumber)…"))
    sys_, usr = prompts.script(idea, p["format"], p["language"], p["voice"]["preset"], budget, src_text)
    # Temperature rendah: naskah edukasi butuh akurasi, bukan kreativitas liar (mengurangi fakta karangan).
    res = llm.chat_json(sys_, usr, params.get("provider"), temperature=0.4, schema=SCRIPT_SCHEMA)
    n = _count_words(res.get("scenes", []))
    if n < budget * 0.65:
        progress(f"Naskah hanya {n} kata (target {int(budget * 0.8)}-{budget}) — AI mengembangkan isinya…")
        sys2, usr2 = prompts.expand_script(res, int(budget * 0.8), budget, src_text)
        try:
            longer = llm.chat_json(sys2, usr2, params.get("provider"), temperature=0.4, schema=SCENES_SCHEMA)
            if longer.get("scenes") and n < _count_words(longer["scenes"]) <= budget * 1.1:
                res = {**res, **longer}
                n = _count_words(res["scenes"])
        except llm.LLMError as e:
            progress(f"Gagal mengembangkan naskah ({e}) — dipakai apa adanya.")
    if n > budget * 1.1:
        progress(f"Naskah {n} kata (> {budget}) — AI memadatkan supaya durasi pas…")
        sys2, usr2 = prompts.shorten_script(res, budget)
        try:
            shorter = llm.chat_json(sys2, usr2, params.get("provider"), temperature=0.3, schema=SCENES_SCHEMA)
            if shorter.get("scenes") and _count_words(shorter["scenes"]) < n:
                res = {**res, **shorter}
        except llm.LLMError as e:
            progress(f"Gagal memadatkan ({e}) — naskah dipakai apa adanya.")
    progress("Merapikan naskah menjadi scene…")
    scenes = normalize_scenes([{**s, "id": f"s{i + 1}"} for i, s in enumerate(res.get("scenes", []))])
    claims = []
    if params.get("factcheck", True):
        try:
            claims = run_factcheck(scenes, sources, params.get("provider"), progress)
        except llm.LLMError as e:
            progress(f"Cek fakta gagal ({e}) — bisa diulang dengan tombol Cek Fakta.")

    def apply(p):
        p["script"].update({"title": res.get("title") or idea.get("title"),
                            "framework": res.get("framework", {}), "scenes": scenes,
                            "claims": claims, "claims_at": store.now_iso()})
        p["title"] = p["script"]["title"] or p["title"]
        store.touch_stage(p, "script")
        store.log(p, f"AI: naskah {len(scenes)} scene, {_count_words(scenes)} kata (target ≤{budget}); "
                     f"cek fakta: {_claims_summary(claims)}")
    store.update(pid, apply)
    return {"scenes": len(scenes), "words": _count_words(scenes), "budget": budget,
            "sources": len(sources), "factcheck": _claims_summary(claims)}
