"""Shiroe Syn Content Studio — backend API (FastAPI)."""
import secrets
import shutil

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, jobs, store
from .services import (assets, audio_lib, batch, clipper, fonts, prompts, render, research, scriptgen, story,
                       subtitles, sync, thumbs, tts, youtube)

app = FastAPI(title="Shiroe Syn Content Studio")
WEB = config.ROOT / "web"

# UI di GitHub Pages (https://<user>.github.io) memanggil backend di laptop (http://127.0.0.1:8000)
import inspect  # noqa: E402

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

_cors = dict(allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
# Starlette baru menolak preflight Private Network Access (400) kecuali opsi ini aktif.
if "allow_private_network" in inspect.signature(CORSMiddleware.__init__).parameters:
    _cors["allow_private_network"] = True
app.add_middleware(CORSMiddleware, **_cors)


LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.middleware("http")
async def lan_pin_guard(request, call_next):
    """Mode tablet/LAN: perangkat lain di Wi-Fi wajib memasukkan PIN. Laptop sendiri (loopback, termasuk
    UI GitHub Pages yang memanggil 127.0.0.1) tidak perlu PIN."""
    client = request.client.host if request.client else ""
    if config.APP_PIN and client not in LOOPBACK and request.method != "OPTIONS":
        pin = (request.headers.get("x-app-pin") or request.cookies.get("app_pin")
               or request.query_params.get("pin") or "")
        path = request.url.path
        public = path == "/" or path.startswith("/static/") or path == "/api/pin-check"
        if not public and not secrets.compare_digest(pin, config.APP_PIN):
            return JSONResponse({"detail": "PIN"}, status_code=401)
    return await call_next(request)


@app.get("/api/pin-check")
def pin_check(pin: str = ""):
    ok = not config.APP_PIN or secrets.compare_digest(pin, config.APP_PIN)
    if not ok:
        raise HTTPException(401, "PIN salah")
    return {"ok": True}


@app.middleware("http")
async def private_network_access(request, call_next):
    """Chrome 'Private Network Access': halaman publik yang memanggil localhost butuh header ini."""
    resp = await call_next(request)
    if request.headers.get("access-control-request-private-network"):
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


# ---------- registry aksi: nama -> (fungsi, tahap, berjalan sebagai job?) ----------
ACTIONS = {
    "research.ideas":      (research.generate_ideas, "research", True),
    "research.download":   (research.download_source, "research", True),
    "research.batch":      (batch.produce, "research", True),
    "script.generate":     (scriptgen.generate, "script", True),
    "script.factcheck":    (scriptgen.factcheck, "script", True),
    "script.narrate":      (scriptgen.narrate, "script", True),
    "script.split":        (scriptgen.split, "script", True),
    "script.transcribe":   (clipper.transcribe, "script", True),
    "script.find_clips":   (clipper.find_clips, "script", True),
    "assets.search":       (assets.search_scene, "assets", True),
    "assets.select":       (assets.select_candidate, "assets", True),
    "assets.auto_all":     (assets.auto_all, "assets", True),
    "assets.cut_clip":     (clipper.cut_clip, "script", True),   # Clipper: tahap Momen & Potong (gabungan)
    "script.produce_clips": (clipper.produce_clips, "script", True),
    "research.auto_clip":  (clipper.auto_clip, "research", True),
    "assets.story_setup":  (story.setup, "assets", True),
    "assets.story_chars":  (story.characters, "assets", True),
    "assets.story_scenes": (story.scenes, "assets", True),
    "audio.render_vo":     (tts.render_vo, "audio", True),
    "audio.auto_plan":     (audio_lib.auto_plan, "audio", True),
    "subtitles.build":     (subtitles.build, "subtitles", True),
    "subtitles.apply":     (subtitles.apply_lines, "subtitles", False),
    "subtitles.regroup":   (subtitles.regroup, "subtitles", False),
    "render.run":          (render.run, "render", True),
    "publish.metadata":    (youtube.gen_metadata, "publish", True),
    "publish.upload":      (youtube.upload, "publish", True),
    "thumbnail.frames":    (thumbs.frames, "thumbnail", True),
    "thumbnail.render":    (thumbs.render_design, "thumbnail", True),
    "thumbnail.auto":      (thumbs.auto, "thumbnail", True),
    "thumbnail.remove":    (thumbs.remove, "thumbnail", False),
    "publish.thumbnail":   (thumbs.upload, "publish", True),
}


def _load(pid: str) -> dict:
    try:
        return store.load(pid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Proyek tidak ditemukan")


@app.get("/api/meta")
def meta():
    return {"stages": store.STAGES, "labels": store.STAGE_LABELS,
            "labels_by_mode": {"scratch": store.STAGE_LABELS, "clipper": store.STAGE_LABELS_CLIPPER},
            "stage_help": store.STAGE_HELP, "voice_presets": store.VOICE_PRESETS,
            "emotions": list(store.EMOTION_VOICE), "moods": store.MOODS, "google": config.google_enabled(),
            "sub_presets": store.SUB_PRESETS, "sub_templates": store.SUB_TEMPLATES,
            "sub_user_templates": store.user_templates(), "sub_defaults": subtitles.STYLE_DEFAULTS,
            "sub_choices": subtitles.CHOICES, "fonts": fonts.FONT_NAMES,
            "thumb_templates": thumbs.THUMB_TEMPLATES, "thumb_defaults": thumbs.DESIGN_DEFAULTS,
            "clipper_merged": store.CLIPPER_MERGED,
            "llm": config.LLM_PROVIDER,
            "keys": {"pexels": bool(config.PEXELS_API_KEY), "pixabay": bool(config.PIXABAY_API_KEY),
                     "pollinations": bool(config.POLLINATIONS_KEY)},
            "story_styles": story.STYLE_LABEL,
            "idea_template": prompts.IDEA_TEMPLATE, "narr_templates": prompts.TEMPLATE_LABEL}


@app.get("/api/health")
def health(ping: bool = False):
    from .services import health as hc
    return hc.check(ping_llm=ping)


# ---------- proyek ----------
@app.get("/api/projects")
def projects():
    return store.list_projects()


@app.post("/api/projects")
def create(body: dict = Body(...)):
    try:
        return store.new_project(body.get("mode", "scratch"), body.get("format", "short"),
                                 body.get("title", ""), body.get("keyword", ""), body.get("language", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _load(pid)
    p["_jobs"] = jobs.active_for(pid)
    p["_interrupted"] = jobs.recent_interrupted(pid)
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, with_children: bool = False):
    """with_children: Clipper — ikut hapus video hasil (proyek anak). Tanpa itu anak jadi proyek mandiri."""
    _load(pid)
    kids = [q["id"] for q in store.list_projects() if q.get("parent") == pid]
    shutil.rmtree(store.project_dir(pid), ignore_errors=True)
    for k in kids:
        if with_children:
            shutil.rmtree(store.project_dir(k), ignore_errors=True)
        else:
            store.update(k, lambda q: (q.pop("parent", None), store.log(q, f"Proyek sumber {pid} dihapus")))
    return {"ok": True, "children": len(kids), "deleted_children": with_children}


@app.post("/api/projects/{pid}/fork")
def fork(pid: str, body: dict = Body(...)):
    """Clipper: jadikan kandidat klip lain sebagai proyek baru (sumber & transkrip ikut disalin)."""
    _load(pid)
    return clipper.fork_clip(pid, int(body["clip_index"]))


@app.post("/api/projects/{pid}/narration-prompt")
def narration_prompt(pid: str, body: dict = Body(default={})):
    """Pratinjau prompt narasi (modal 'Opsi Prompt Cerita') untuk template & durasi tertentu."""
    p = _load(pid)
    tpl = body.get("template") or "fakta"
    secs = scriptgen.duration_of(p, body)
    wmin, wmax = scriptgen.word_range(p, secs)
    if tpl not in scriptgen.NARR_TEMPLATES or tpl == "custom":
        tpl = "fakta"
    try:
        prompt = scriptgen.build_narration_prompt(p, tpl, secs)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"prompt": prompt, "words": [wmin, wmax], "duration": secs}


# ---------- gerbang Human-in-the-Loop ----------
@app.put("/api/projects/{pid}/stage/{stage}")
def save_stage(pid: str, stage: str, data: dict = Body(...)):
    """Tombol 'Simpan Edit Manual'."""
    if stage not in store.STAGES:
        raise HTTPException(404)
    p = _load(pid)
    if stage == "script" and p["mode"] == "scratch" and "scenes" in data:
        data["scenes"] = scriptgen.normalize_scenes(data["scenes"])
    # Lewat store.update (di bawah lock) supaya tidak menimpa hasil job yang sedang menulis proyek.
    try:
        return store.update(pid, lambda p: store.save_stage(p, stage, data))
    except PermissionError as e:
        raise HTTPException(409, str(e))


@app.post("/api/projects/{pid}/stage/{stage}/approve")
def approve(pid: str, stage: str):
    """Tombol 'Setujui & Lanjut'. Sinkron Google berjalan di background."""
    _load(pid)
    if stage not in store.STAGES:
        raise HTTPException(404)
    def do(p):
        store.approve(p, stage)
        # Clipper: "Momen & Potong" = tahap script + assets sekaligus
        if p["mode"] == "clipper" and stage == "script":
            store.approve(p, "assets")
    try:
        p = store.update(pid, do)
    except (PermissionError, ValueError) as e:
        raise HTTPException(409, str(e))
    jid = jobs.submit(pid, f"sync.{stage}", lambda progress: sync.safe_sync(pid, stage, progress))
    return {"project": p, "sync_job": jid}


@app.put("/api/projects/{pid}/voice")
def save_voice(pid: str, body: dict = Body(...)):
    def apply(p):
        p["voice"].update(body)
        if "pronunciation" in body:
            p["audio"]["pronunciation"] = p["voice"].pop("pronunciation")
        store.touch_stage(p, "audio")
    _load(pid)
    return store.update(pid, apply)


@app.post("/api/projects/{pid}/action/{name}")
def action(pid: str, name: str, params: dict = Body(default={})):
    if name not in ACTIONS:
        raise HTTPException(404, f"Aksi tidak dikenal: {name}")
    fn, stage, as_job = ACTIONS[name]
    p = _load(pid)
    if store.STAGES.index(stage) > p["current_stage"]:
        raise HTTPException(409, "Tahap ini belum terbuka. Setujui tahap sebelumnya dulu.")
    # Cegah klik ganda / dua tab: aksi yang sama (atau render vs upload) tidak boleh jalan bersamaan.
    busy = {j["kind"] for j in jobs.active_for(pid)}
    clash = busy & ({name} | ({"render.run", "publish.upload"} if name in ("render.run", "publish.upload") else set()))
    if clash:
        raise HTTPException(409, f"Proses '{next(iter(clash))}' masih berjalan untuk proyek ini. Tunggu sampai selesai.")
    if as_job:
        return {"job": jobs.submit(pid, name, fn, pid, params)}
    try:
        return {"result": fn(pid, params, lambda m: None), "project": store.load(pid)}
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    if not jobs.cancel(jid):
        raise HTTPException(409, "Proses ini sudah selesai atau tidak ditemukan.")
    return {"ok": True}


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "Proses ini tidak ditemukan — kemungkinan server di-restart/reload saat proses "
                                 "berjalan. Jalankan ulang aksinya.")
    return j


# ---------- upload file manual ----------
@app.post("/api/projects/{pid}/upload-source")
def upload_source(pid: str, file: UploadFile = File(...)):
    _load(pid)
    # Keamanan: ekstensi masuk ke nama file → whitelist (nama seperti "x.mp4\..\..\evil" ditolak).
    name = (file.filename or "video.mp4").replace("\\", "/").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ("mp4", "mov", "mkv", "webm", "m4v"):
        raise HTTPException(400, "Format video tidak didukung (pakai mp4, mov, mkv, webm, m4v).")
    d = store.project_dir(pid) / "source"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)  # buang sumber lama supaya tidak tertukar
    d.mkdir(exist_ok=True)
    dest = d / f"source.{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    def apply(p):
        try:
            from .services import ffmpeg_utils as ff
            dur = ff.duration(dest)
        except Exception:
            dur = None
        p["research"]["source"] = {"url": None, "title": file.filename, "path": f"source/{dest.name}",
                                   "duration": dur, "license": "milik sendiri / berizin"}
        store.touch_stage(p, "research")
    return store.update(pid, apply)


@app.post("/api/projects/{pid}/assets/{sid}/upload")
def upload_asset(pid: str, sid: str, file: UploadFile = File(...)):
    _load(pid)
    try:
        return assets.save_upload(pid, sid, file.filename or "asset.mp4", file.file)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{pid}/story/char/{cid}/upload")
def upload_char(pid: str, cid: str, file: UploadFile = File(...)):
    """Unggah gambar referensi karakter sendiri (mis. foto/ilustrasi tokoh yang sudah jadi)."""
    _load(pid)
    try:
        return story.save_char_upload(pid, cid, file.file)
    except Exception as e:  # noqa: BLE001 — format gambar tidak dikenal, karakter tidak ada
        raise HTTPException(400, f"Gagal memakai gambar: {e}")


@app.get("/api/projects/{pid}/snap")
def snap(pid: str, start: float, end: float):
    p = _load(pid)
    st, en = clipper.snap(p["script"]["transcript"]["segments"], start, end)
    return {"start": st, "end": en}


# ---------- suara ----------
@app.get("/api/voices")
def voices():
    try:
        return tts.list_voices()
    except Exception as e:
        raise HTTPException(502, f"Gagal mengambil daftar suara Edge-TTS: {e}")


@app.post("/api/tts/preview")
def tts_preview(body: dict = Body(...)):
    try:
        audio = tts.preview(body["text"], body.get("voice", {}), body.get("emotion", "netral"),
                            body.get("pronunciation"))
    except Exception as e:
        raise HTTPException(502, str(e))
    return Response(audio, media_type="audio/mpeg")


@app.get("/api/pronunciation")
def get_pron():
    return tts.load_pronunciation()


@app.put("/api/pronunciation")
def put_pron(body: dict = Body(...)):
    tts.save_pronunciation(body)
    return tts.load_pronunciation()


@app.get("/api/library/{kind}")
def library(kind: str, refresh: bool = False):
    try:
        return audio_lib.list_library(kind, refresh)
    except ValueError:
        raise HTTPException(404)


@app.get("/api/library-file")
def library_file(source: str, ref: str, name: str = ""):
    """Stream file musik/SFX untuk pratinjau di browser."""
    if source not in ("local", "drive"):
        raise HTTPException(400)
    if source == "local" and not (config.DATA_DIR / ref).resolve().is_relative_to(config.DATA_DIR.resolve()):
        raise HTTPException(403)
    try:
        path = audio_lib.local_path({"source": source, "ref": ref, "name": name})
    except ValueError:
        raise HTTPException(400, "Nama file tidak valid")
    except Exception as e:  # noqa: BLE001 — mis. Google belum terhubung / file Drive tidak ada
        raise HTTPException(502, f"Gagal mengambil file library: {e}")
    if not path.resolve().is_relative_to(config.DATA_DIR.resolve()) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/schedule")
def schedule():
    out = []
    for item in store.list_projects():
        p = store.load(item["id"])
        if p["publish"].get("video_id") or p["publish"].get("publish_at"):
            out.append({"id": p["id"], "title": p["publish"].get("title") or p["title"],
                        "publish_at": p["publish"].get("publish_at"), "status": p["publish"].get("status"),
                        "url": p["publish"].get("url")})
    return sorted(out, key=lambda x: x["publish_at"] or "")


# ---------- template subtitle buatan sendiri ----------
@app.get("/api/sub-templates")
def sub_templates():
    return store.user_templates()


@app.post("/api/sub-templates")
def save_sub_template(body: dict = Body(...)):
    try:
        return store.save_user_template(str(body.get("name", "")), dict(body.get("style") or {}))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/sub-templates/{key}")
def delete_sub_template(key: str):
    return store.delete_user_template(key)


@app.post("/api/projects/{pid}/subtitles/preview")
def subtitle_preview(pid: str, body: dict = Body(...)):
    """Pratinjau AKURAT: satu frame dirender libass (sama persis dengan hasil render) pada detik t."""
    p = _load(pid)
    try:
        png = subtitles.preview_frame(p, body.get("style") or p["subtitles"]["style"], float(body.get("t") or 0))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Pratinjau gagal: {e}")
    return Response(png, media_type="image/png")


@app.post("/api/projects/{pid}/thumbnail/preview")
def thumbnail_preview(pid: str, body: dict = Body(...)):
    """Pratinjau live editor thumbnail (JPEG, tidak disimpan)."""
    import io
    import tempfile
    p = _load(pid)
    d = store.project_dir(pid)
    frame = body.get("frame") or p["thumbnail"].get("frame")
    if not frame or not (d / frame).resolve().is_relative_to(d.resolve()) or not (d / frame).is_file():
        raise HTTPException(400, "Pilih frame dulu.")
    info = next((f for f in p["thumbnail"].get("frames") or [] if f["path"] == frame), None)
    design = dict(body.get("design") or {})
    design.setdefault("text", thumbs.default_text(p))
    with tempfile.TemporaryDirectory() as td:
        out = f"{td}/prev.jpg"
        try:
            thumbs.compose(d / frame, info, design, p["format"], out)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Pratinjau gagal: {e}")
        data = io.BytesIO(open(out, "rb").read()).getvalue()
    return Response(data, media_type="image/jpeg")


@app.post("/api/projects/{pid}/thumbnail/upload")
def thumbnail_upload(pid: str, file: UploadFile = File(...)):
    _load(pid)
    try:
        return thumbs.save_upload(pid, file.filename or "x.jpg", file.file)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/fonts/{name}")
def font_file(name: str):
    """File font untuk pratinjau subtitle di browser (@font-face) — sama dengan font render."""
    f = fonts.path(name)
    if not f or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "max-age=86400"})


# ---------- file media proyek (preview video/audio di UI) ----------
@app.get("/media/{pid}/{path:path}")
def media(pid: str, path: str):
    base = store.project_dir(pid).resolve()
    f = (base / path).resolve()
    if not f.is_relative_to(base) or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
