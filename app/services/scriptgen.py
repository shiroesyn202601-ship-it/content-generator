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


def generate(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    idea = p["research"]["ideas"][p["research"]["selected"]]
    progress("AI menulis kerangka & naskah...")
    sys_, usr = prompts.script(idea, p["format"], p["language"], p["voice"]["preset"])
    res = llm.chat_json(sys_, usr, params.get("provider"))
    scenes = normalize_scenes([{**s, "id": f"s{i + 1}"} for i, s in enumerate(res.get("scenes", []))])

    def apply(p):
        p["script"].update({"title": res.get("title") or idea.get("title"),
                            "framework": res.get("framework", {}), "scenes": scenes})
        p["title"] = p["script"]["title"] or p["title"]
        store.touch_stage(p, "script")
        store.log(p, f"AI: naskah {len(scenes)} scene")
    store.update(pid, apply)
    return {"scenes": len(scenes)}
