"""Shorts massal (Dari Nol) — ala "script to video" CapCut, tapi BANYAK video sekaligus.

Alur untuk kreator: ketik topik (mis. "hewan") → AI membuat banyak ide + hook → centang ide yang disukai →
satu tombol "Buat video". Tiap ide jadi video sendiri tanpa klik per scene:
    narasi (template) → scene → visual (stok+foto asli / gambar AI karakter konsisten) → voice over →
    musik & SFX → subtitle → render → 3 thumbnail → judul/deskripsi/tag → siap di tab Upload.

Struktur data sama seperti Clipper: proyek WADAH (p["batch"], berisi ide & pengaturan) + proyek ANAK per video
(field parent, folder di data/projects/<wadah>/clips/<id>/). Tiap anak tetap proyek biasa → bisa dibuka di
wizard per tahap untuk diperbaiki lalu dirender ulang (gerbang manusia tetap ada).

Berurutan per video (LLM gratis punya batas token/menit; render berat). Pipeline melewati tahap yang sudah
disetujui → tombol "Coba lagi" / "Lanjutkan" cukup memanggil ulang. Satu video gagal tidak menghentikan lainnya.
"""
import time

from .. import store
from . import scriptgen

DEFAULTS = {
    "template": "fakta",      # template narasi (prompts.TEMPLATE_LABEL)
    "duration": 50,           # detik
    "visual": "stock",        # stock = stok Pixabay/Pexels + foto Wikimedia (cadangan gambar AI) | ai = Cerita AI
    "style": "3d",            # gaya gambar untuk visual=ai (story.STYLES)
    "voice_preset": "antusias",
    "voice": "",              # kosong = suara bawaan preset
    "sub_style": None,        # dict gaya subtitle (template), None = bawaan Shorts
    "music": True,
    "transition": "",         # "" = fade otomatis
    "review": False,          # True = berhenti setelah naskah (cek dulu), lanjutkan dengan tombol "Lanjutkan"
    "metadata": True,         # judul/deskripsi/tag otomatis
}


def options(o: dict | None) -> dict:
    o = {**DEFAULTS, **{k: v for k, v in (o or {}).items() if k in DEFAULTS and v is not None}}
    o["duration"] = max(15, min(90, int(o["duration"] or 50)))
    if o["visual"] not in ("stock", "ai"):
        o["visual"] = "stock"
    if o["voice_preset"] not in store.VOICE_PRESETS:
        o["voice_preset"] = "antusias"
    return o


def _cancelled(e: Exception) -> bool:
    from .. import jobs
    return isinstance(e, jobs.JobCancelled)


def _retry_429(fn, sub):
    """Batas token per menit LLM gratis habis setelah video sebelumnya → tunggu pulih lalu ulangi (maks 2x)."""
    for attempt in range(3):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if _cancelled(e) or "429" not in str(e) or attempt == 2:
                raise
            for left in range(65, 0, -5):
                sub(f"batas per menit AI tercapai — menunggu {left} dtk…")
                time.sleep(5)


def _approve(pid: str, stage: str) -> None:
    store.update(pid, lambda q: store.approve(q, stage))


def run_pipeline(cid: str, o: dict, sub, stop_after_script: bool = False) -> str:
    """Jalankan semua tahap yang belum disetujui untuk satu video. Return 'review' | 'done'."""
    from . import assets, audio_lib, render, story, subtitles, thumbs, tts, youtube

    def st(stage):
        return store.load(cid)["stage_status"][stage]

    if st("research") != "approved":
        _approve(cid, "research")
    if st("script") != "approved":
        q = store.load(cid)
        if not q["script"].get("scenes"):
            sub("menulis narasi…")
            _retry_429(lambda: scriptgen.narrate(cid, {"template": o["template"], "duration": o["duration"]}, sub), sub)
        if stop_after_script:
            return "review"
        _approve(cid, "script")

    if st("assets") != "approved":
        q = store.load(cid)
        if o["visual"] == "ai":
            if not (q["assets"].get("story") or {}).get("scenes"):
                _retry_429(lambda: story.setup(cid, {"style": o["style"]}, sub), sub)
            q = store.load(cid)
            if any(not c.get("image") for c in (q["assets"].get("story") or {}).get("characters") or []):
                story.characters(cid, {}, sub)
            story.scenes(cid, {}, sub)
        else:
            store.update(cid, lambda x: x["assets"].update({"visual_mode": "stock"}))
            _retry_429(lambda: assets.auto_all(cid, {"vision": False}, sub), sub)
        q = store.load(cid)
        missing = [s["id"] for s in q["script"]["scenes"] if not (q["assets"]["scenes"].get(s["id"]) or {}).get("selected")]
        if missing:  # unduhan gagal / server gambar sibuk → coba sekali lagi hanya untuk scene yang kosong
            sub(f"{len(missing)} scene belum punya visual — mencoba lagi…")
            if o["visual"] == "ai":
                for sid in missing:
                    story.scenes(cid, {"scene_id": sid}, sub)
            else:
                assets.auto_all(cid, {"vision": False}, sub)
        _approve(cid, "assets")

    if st("audio") != "approved":
        tts.render_vo(cid, {}, sub)
        store.update(cid, lambda x: x["audio"]["music"].update({"enabled": bool(o["music"])}))
        if o["music"]:
            audio_lib.auto_plan(cid, {}, sub)
        _approve(cid, "audio")

    if st("subtitles") != "approved":
        if o.get("sub_style"):
            store.update(cid, lambda x: x["subtitles"].update({"style": dict(o["sub_style"])}))
        subtitles.build(cid, {}, sub)
        _approve(cid, "subtitles")

    if st("render") != "approved":
        store.update(cid, lambda x: x["render"].update({"cut_style": "dinamis", "transition": o.get("transition") or ""}))
        render.run(cid, {"drive_background": True}, sub)
        _approve(cid, "render")

    if st("thumbnail") != "approved":
        thumbs.auto(cid, {}, sub)
        _approve(cid, "thumbnail")

    if o.get("metadata", True) and not store.load(cid)["publish"].get("title"):
        try:
            _retry_429(lambda: youtube.gen_metadata(cid, {}, sub), sub)
        except Exception as e:  # noqa: BLE001 — judul bisa dibuat nanti di tab Upload
            if _cancelled(e):
                raise
            store.update(cid, lambda q, e=e: store.log(q, f"Judul/deskripsi otomatis gagal: {e}"))
    return "done"


def _new_child(src: dict, i: int, o: dict) -> dict:
    idea = src["research"]["ideas"][i]
    title = (idea.get("title") or f"Ide {i + 1}").strip()
    p = store.new_project("scratch", "short", title, src["research"].get("keyword", ""), src["language"],
                          parent=src["id"])

    def setup(q):
        q["voice"] = dict(src["voice"])
        q["research"].update({k: src["research"].get(k) for k in ("keyword", "insight", "sources")})
        q["research"].update({"ideas": [idea], "selected": 0, "duration": o["duration"], "narr_template": o["template"]})
        q["batch_idea"] = idea.get("title")
        store.log(q, f"Shorts massal dari proyek {src['id']} (ide {i + 1})")
    store.update(p["id"], setup)
    store.update(src["id"], lambda q: q["research"]["ideas"][i].__setitem__("video", p["id"]))
    return store.load(p["id"])


def _apply_voice(pid: str, o: dict) -> None:
    def fn(q):
        v = {**store.VOICE_PRESETS[o["voice_preset"]], "preset": o["voice_preset"],
             "emphasis_boost": q["voice"].get("emphasis_boost", True)}
        if o.get("voice"):
            v["voice"] = o["voice"]
        q["voice"] = v
        q["batch"] = {**(q.get("batch") or {}), "opts": o}
    store.update(pid, fn)


def produce(pid: str, params: dict, progress) -> dict:
    """Aksi 'Buat video' di proyek wadah Shorts massal.
    params: indexes (ide yang dicentang) dan/atau children (video yang mau dilanjutkan / dicoba lagi), opts."""
    src = store.load(pid)
    if not src.get("batch"):
        raise RuntimeError("Proyek ini bukan proyek Shorts massal.")
    o = options({**((src.get("batch") or {}).get("opts") or {}), **(params.get("opts") or {})})
    _apply_voice(pid, o)
    src = store.load(pid)
    ideas = src["research"].get("ideas") or []
    jobs_ = []  # (judul, id anak | None, indeks ide | None)
    for i in [int(x) for x in params.get("indexes") or [] if 0 <= int(x) < len(ideas)]:
        vid = ideas[i].get("video")
        try:
            if vid and store.load(vid).get("parent") == pid:
                jobs_.append((ideas[i].get("title"), vid, None))  # sudah ada videonya → lanjutkan saja
                continue
        except (FileNotFoundError, ValueError):
            pass
        jobs_.append((ideas[i].get("title"), None, i))
    for cid in params.get("children") or []:
        if not any(j[1] == cid for j in jobs_):
            try:
                jobs_.append((store.load(cid)["title"], cid, None))
            except (FileNotFoundError, ValueError):
                continue
    if not jobs_:
        raise RuntimeError("Centang minimal satu ide.")
    store.update(pid, lambda q: q["batch"].update({"status": "running", "error": None}))
    created, review, failed = [], [], []
    for n, (title, cid, i) in enumerate(jobs_, 1):
        tag = f"Video {n}/{len(jobs_)}"

        def sub(msg, tag=tag, title=title):
            progress(f"{tag}: {(title or '')[:48]} — {msg}")
        step = "persiapan"
        try:
            if cid is None:
                sub("menyiapkan proyek…")
                cid = _new_child(store.load(pid), i, o)["id"]
            else:
                store.update(cid, lambda q: q.update({"voice": dict(store.load(pid)["voice"])})
                             if q["stage_status"]["audio"] != "approved" else None)
            step = "produksi"
            res = run_pipeline(cid, o, sub, stop_after_script=bool(o["review"]) and not params.get("children"))
            (review if res == "review" else created).append({"id": cid, "title": title})
        except Exception as e:  # noqa: BLE001
            if _cancelled(e):
                store.update(pid, lambda q: q["batch"].update({"status": "cancelled"}))
                raise
            q = store.load(cid) if cid else None
            if q:
                step = store.STAGE_LABELS[store.STAGES[q["current_stage"]]]
                store.update(cid, lambda x, e=e: store.log(x, f"Shorts massal berhenti di {step}: {e}"))
            failed.append({"id": cid, "title": title, "step": step, "error": str(e)[:200]})
            sub(f"gagal di {step}: {str(e)[:120]}")
    summary = (f"{len(created)} video jadi" + (f", {len(review)} menunggu cek naskah" if review else "")
               + (f", {len(failed)} perlu dicek" if failed else ""))
    store.update(pid, lambda q: (q["batch"].update({"status": "done", "last": summary}), store.log(q, f"Shorts massal: {summary}")))
    return {"created": created, "review": review, "failed": failed}


def autopilot(pid: str, params: dict, progress) -> dict:
    """'Lanjutkan otomatis': satu proyek Dari Nol (short / long) dikerjakan dari tahap sekarang sampai thumbnail
    dengan pengaturan yang sudah ada di proyek (visual stok, suara proyek, gaya subtitle proyek)."""
    p = store.load(pid)
    if p["mode"] != "scratch":
        raise RuntimeError("Lanjutkan otomatis hanya untuk proyek Dari Nol.")
    if p["research"].get("selected") is None:
        raise RuntimeError("Pilih satu ide dulu di tahap 1.")
    vm = (p["assets"].get("visual_mode") or "stock")
    o = options({"template": p["script"].get("narr_template") or p["research"].get("narr_template") or "fakta",
                 "duration": min(90, scriptgen.duration_of(p)), "visual": "ai" if vm == "ai_story" else "stock",
                 "style": (p["assets"].get("story") or {}).get("style") or "3d",
                 "music": p["audio"]["music"].get("enabled", True), "transition": p["render"].get("transition") or "",
                 "metadata": True, **(params.get("opts") or {})})
    if p["format"] == "long" and not p["script"].get("scenes"):
        from . import longform
        longform.write(pid, {}, lambda m: progress(f"Naskah: {m}"))
    step = lambda m: progress(m)  # noqa: E731
    res = run_pipeline(pid, o, step)
    return {"status": res}
