"""'Buat video' sekali klik ala CapCut script-to-video (Dari Nol, satu video).

Masukan: topik ATAU naskah sendiri + rasio (short/long) + durasi + gaya visual + suara + gaya subtitle.
Topik → AI menulis narasi (short: template narasi; long: per bab). Naskah sendiri → dipakai apa adanya, hanya
dipecah jadi scene (baris "## Judul" = bab). Lalu shorts.run_pipeline mengerjakan visual → suara → musik →
subtitle → render → thumbnail → judul, semua tahap disetujui. Hasilnya dibuka di editor (web: renderEditor),
tempat teks & gambar per potongan bisa diganti lalu dirender ulang (aksi research.autopilot)."""
from .. import store
from . import longform, scriptgen, shorts


def _first_line(text: str) -> str:
    s = scriptgen.sentences(text.replace("#", " "))
    t = (s[0] if s else text).strip()
    return t if len(t) <= 70 else t[:67].rsplit(" ", 1)[0] + "…"


def run(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    if p["mode"] != "scratch":
        raise RuntimeError("Buat video hanya untuk proyek Dari Nol.")
    topic = (params.get("topic") or "").strip()
    script = (params.get("script") or "").strip()
    if not topic and not script:
        raise RuntimeError("Tulis topik atau tempel naskahnya dulu.")
    long = p["format"] == "long"
    dur = int(params.get("duration") or (480 if long else 50))
    dur = max(120, min(1200, dur)) if long else max(15, min(90, dur))
    o = shorts.options({"template": params.get("template") or "fakta", "duration": min(dur, 90),
                        "visual": params.get("visual") or "ai", "style": params.get("style") or "3d",
                        "voice_preset": params.get("voice_preset") or ("naratif" if long else "antusias"),
                        "voice": params.get("voice") or "", "sub_style": params.get("sub_style"),
                        "music": params.get("music", True), "metadata": True})
    title = (params.get("title") or topic or _first_line(script)).strip()
    if p.get("quick") and p["script"].get("scenes"):  # dijalankan ulang (server restart) → lanjutkan saja
        return {"status": shorts.run_pipeline(pid, o, progress)}

    def setup(q):
        idea = {"title": title, "description": params.get("brief") or "", "hook": "", "released": False}
        q["title"] = title
        q["research"].update({"keyword": topic or title, "ideas": [idea], "selected": 0, "duration": dur,
                              "narr_template": o["template"]})
        q["quick"] = {"opts": {k: v for k, v in params.items() if k != "script"}, "own_script": bool(script)}
        q["assets"]["visual_mode"] = "ai_story" if o["visual"] == "ai" else "stock"
        if o["visual"] == "ai":
            q["assets"].setdefault("story", {})["style"] = o["style"]
        store.log(q, f"Buat video: {'naskah sendiri' if script else 'topik'} · {o['visual']} · {dur} dtk")
    store.update(pid, setup)
    shorts._apply_voice(pid, o)
    store.update(pid, lambda q: q.pop("batch", None))  # _apply_voice menulis opsi ke 'batch' (milik Shorts massal)
    store.update(pid, lambda q: store.approve(q, "research"))

    step = lambda m: progress(m)  # noqa: E731
    if script:
        store.update(pid, lambda q: q["script"].update({"narration": script, "title": title}))
        progress("Memecah naskah jadi scene…")
        shorts._retry_429(lambda: scriptgen.split(pid, {}, lambda m: progress(f"Naskah: {m}")), step)
    elif long:
        shorts._retry_429(lambda: longform.write(pid, {}, lambda m: progress(f"Naskah: {m}")), step)
    else:
        shorts._retry_429(lambda: scriptgen.narrate(pid, {"template": o["template"], "duration": dur},
                                                    lambda m: progress(f"Naskah: {m}")), step)
    res = shorts.run_pipeline(pid, o, step)
    store.update(pid, lambda q: q["quick"].update({"done": True}))
    return {"status": res}
