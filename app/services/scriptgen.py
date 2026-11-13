"""Tahap 2 (scratch): kerangka cerita + naskah per scene dengan penanda emosi."""
from .. import store
from ..store import EMOTION_VOICE, MOODS
from . import llm, prompts


def normalize_scenes(scenes: list[dict]) -> list[dict]:
    """Pastikan setiap scene punya field kanonik & id unik (dipakai juga saat edit manual)."""
    out = []
    for i, s in enumerate(scenes):
        emo = (s.get("emotion") or "netral").lower()
        mood = (s.get("mood") or "santai").lower()
        out.append({
            "id": s.get("id") or f"s{i + 1}",
            "text": (s.get("text") or "").strip(),
            "emotion": emo if emo in EMOTION_VOICE else "netral",
            "mood": mood if mood in MOODS else "santai",
            "visual_query": (s.get("visual_query") or "").strip(),
            "sfx": (s.get("sfx") or "").strip(),
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


def generate(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    idea = p["research"]["ideas"][p["research"]["selected"]]
    budget = word_budget(p)
    progress(f"Menyiapkan bahan naskah dari ide '{idea.get('title', '')}' (target ≤{budget} kata)…")
    sys_, usr = prompts.script(idea, p["format"], p["language"], p["voice"]["preset"], budget)
    # Temperature rendah: naskah edukasi butuh akurasi, bukan kreativitas liar (mengurangi fakta karangan).
    res = llm.chat_json(sys_, usr, params.get("provider"), temperature=0.4)
    n = _count_words(res.get("scenes", []))
    if n > budget * 1.1:
        progress(f"Naskah {n} kata (> {budget}) — AI memadatkan supaya durasi pas…")
        sys2, usr2 = prompts.shorten_script(res, budget)
        try:
            shorter = llm.chat_json(sys2, usr2, params.get("provider"), temperature=0.3)
            if shorter.get("scenes") and _count_words(shorter["scenes"]) < n:
                res = {**res, **shorter}
        except llm.LLMError as e:
            progress(f"Gagal memadatkan ({e}) — naskah dipakai apa adanya.")
    progress("Merapikan naskah menjadi scene…")
    scenes = normalize_scenes([{**s, "id": f"s{i + 1}"} for i, s in enumerate(res.get("scenes", []))])

    def apply(p):
        p["script"].update({"title": res.get("title") or idea.get("title"),
                            "framework": res.get("framework", {}), "scenes": scenes})
        p["title"] = p["script"]["title"] or p["title"]
        store.touch_stage(p, "script")
        store.log(p, f"AI: naskah {len(scenes)} scene, {_count_words(scenes)} kata (target ≤{budget})")
    store.update(pid, apply)
    return {"scenes": len(scenes), "words": _count_words(scenes), "budget": budget}
