"""Shiroe Syn Content Studio — backend API (FastAPI)."""
import shutil

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, jobs, store
from .services import (assets, audio_lib, clipper, render, research, scriptgen, subtitles, sync, tts,
                       youtube)

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
    "script.generate":     (scriptgen.generate, "script", True),
    "script.transcribe":   (clipper.transcribe, "script", True),
    "script.find_clips":   (clipper.find_clips, "script", True),
    "assets.search":       (assets.search_scene, "assets", True),
    "assets.select":       (assets.select_candidate, "assets", True),
    "assets.auto_all":     (assets.auto_all, "assets", True),
    "assets.cut_clip":     (clipper.cut_clip, "assets", True),
    "audio.render_vo":     (tts.render_vo, "audio", True),
    "audio.auto_plan":     (audio_lib.auto_plan, "audio", True),
    "subtitles.build":     (subtitles.build, "subtitles", True),
    "subtitles.apply":     (subtitles.apply_lines, "subtitles", False),
    "subtitles.regroup":   (subtitles.regroup, "subtitles", False),
    "render.run":          (render.run, "render", True),
    "publish.metadata":    (youtube.gen_metadata, "publish", True),
    "publish.upload":      (youtube.upload, "publish", True),
}


def _load(pid: str) -> dict:
    try:
        return store.load(pid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Proyek tidak ditemukan")


@app.get("/api/meta")
def meta():
    return {"stages": store.STAGES, "labels": store.STAGE_LABELS, "voice_presets": store.VOICE_PRESETS,
            "emotions": list(store.EMOTION_VOICE), "moods": store.MOODS, "google": config.google_enabled(),
            "sub_presets": store.SUB_PRESETS,
            "llm": config.LLM_PROVIDER,
            "keys": {"pexels": bool(config.PEXELS_API_KEY), "pixabay": bool(config.PIXABAY_API_KEY)}}


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
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _load(pid)
    shutil.rmtree(store.project_dir(pid), ignore_errors=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/fork")
def fork(pid: str, body: dict = Body(...)):
    """Clipper: jadikan kandidat klip lain sebagai proyek baru (sumber & transkrip ikut disalin)."""
    src = _load(pid)
    p = store.new_project("clipper", src["format"], "", language=src["language"])
    old_dir, new_dir = store.project_dir(pid), store.project_dir(p["id"])
    shutil.copytree(old_dir / "source", new_dir / "source", dirs_exist_ok=True)
    idx = int(body["clip_index"])
    clip = src["script"]["clips"][idx]
    p.update({"title": clip.get("title") or src["title"], "current_stage": 1, "voice": src["voice"]})
    p["research"] = src["research"]
    p["script"] = {**src["script"], "selected_clip": idx}
    p["stage_status"].update({"research": "approved", "script": "draft"})
    store.log(p, f"Fork dari {pid}, klip {clip['id']}")
    return store.save(p)


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
    try:
        p = store.update(pid, lambda p: store.approve(p, stage))
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
