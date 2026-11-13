"""Sinkronisasi ke Google setiap kali sebuah tahap disetujui.
Gagal sinkron TIDAK memblokir pekerjaan (dicatat di log proyek, bisa diulang)."""
import json
import threading

from .. import config, store
from . import gdrive, gsheets

# file yang di-backup ke Drive saat tahap tertentu disetujui
ARTIFACTS = {
    "audio": ["audio/vo.wav"],
    "subtitles": ["subs.srt", "subs.ass"],
}


def _folder(p: dict) -> str:
    if not p["drive"].get("folder_id"):
        fid = gdrive.project_folder(p["id"], p["title"])
        store.update(p["id"], lambda q: q["drive"].__setitem__("folder_id", fid))
        p["drive"]["folder_id"] = fid
    return p["drive"]["folder_id"]


def upload_artifact(p: dict, rel: str, name: str | None = None) -> str:
    local = store.project_dir(p["id"]) / rel
    fid = gdrive.upload(local, _folder(p), name or rel.replace("/", "_"))
    store.update(p["id"], lambda q: q["drive"]["files"].__setitem__(rel, fid))
    return fid


def project_row(p: dict) -> dict:
    src = (p["research"].get("source") or {}).get("url") or p["research"].get("keyword", "")
    return {
        "project_id": p["id"], "created_at": p["created_at"], "updated_at": p["updated_at"],
        "mode": p["mode"], "format": p["format"], "title": p["title"],
        "current_stage": store.STAGES[min(p["current_stage"], len(store.STAGES) - 1)],
        "stage_status": p["stage_status"], "keyword_or_source": src,
        "drive_folder_id": p["drive"].get("folder_id"),
        "project_json_drive_id": p["drive"]["files"].get("project.json"),
        "final_video_drive_id": p["render"].get("drive_file_id"),
        "youtube_video_id": p["publish"].get("video_id"),
        "publish_at": p["publish"].get("publish_at"), "privacy": p["publish"].get("privacy"),
    }


def sync_project(pid: str, stage: str, progress=lambda m: None) -> dict:
    if not config.google_enabled():
        return {"skipped": "Google belum terhubung"}
    p = store.load(pid)
    progress("Sinkron ke Google Drive...")
    _folder(p)
    for rel in ARTIFACTS.get(stage, []):
        if (store.project_dir(pid) / rel).exists():
            upload_artifact(p, rel)
    upload_artifact(p, "project.json")
    p = store.load(pid)

    progress("Sinkron ke Google Sheets...")
    gsheets.upsert("Projects", "project_id", project_row(p))
    if stage == "script" and p["mode"] == "scratch":
        gsheets.replace_rows("Scripts", "project_id", pid, [
            {"project_id": pid, "order": i + 1, **{k: s.get(k) for k in
             ("emotion", "mood", "text", "visual_query", "sfx")}, "scene_id": s["id"]}
            for i, s in enumerate(p["script"]["scenes"])])
    if stage == "audio" and p["mode"] == "scratch":
        t = {x["scene_id"]: x for x in p["audio"]["scene_timings"]}
        gsheets.replace_rows("Scripts", "project_id", pid, [
            {"project_id": pid, "order": i + 1, "scene_id": s["id"], "emotion": s["emotion"], "mood": s["mood"],
             "text": s["text"], "visual_query": s["visual_query"], "sfx": s["sfx"],
             "start": t.get(s["id"], {}).get("start"), "end": t.get(s["id"], {}).get("end")}
            for i, s in enumerate(p["script"]["scenes"])])
    if stage == "assets":
        rows = []
        for sid, slot in p["assets"]["scenes"].items():
            sel = slot.get("selected")
            if sel:
                rows.append({"project_id": pid, "scene_or_clip": sid, "type": sel.get("type"),
                             "provider": sel.get("provider"), "author": sel.get("author"),
                             "license": sel.get("license"), "page_url": sel.get("page_url"),
                             "local_path": sel.get("path"), "logged_at": store.now_iso()})
        if p["mode"] == "clipper":
            src = p["research"]["source"]
            rows.append({"project_id": pid, "scene_or_clip": "source", "type": "video", "provider": "youtube",
                         "author": src.get("channel"), "license": src.get("license") or "cek izin pemilik",
                         "page_url": src.get("url"), "local_path": src.get("path"), "logged_at": store.now_iso()})
        gsheets.replace_rows("Assets", "project_id", pid, rows)
    return {"ok": True}


_sync_lock = threading.Lock()  # replace_rows = clear + tulis ulang tab → tidak boleh paralel


def safe_sync(pid: str, stage: str, progress=lambda m: None) -> dict:
    try:
        if not _sync_lock.acquire(blocking=False):
            progress("Menunggu sinkron Google lain selesai…")
            _sync_lock.acquire()
        try:
            return sync_project(pid, stage, progress)
        finally:
            _sync_lock.release()
    except Exception as e:
        store.update(pid, lambda p: store.log(p, f"Sinkron Google gagal ({stage}): {e}"))
        return {"error": str(e)}


def restore_from_drive(file_id: str) -> str:
    """Pulihkan proyek dari backup project.json di Drive (mis. pindah komputer)."""
    tmp = config.CACHE_DIR / f"restore_{file_id}.json"
    gdrive.download(file_id, tmp)
    p = json.loads(tmp.read_text(encoding="utf-8"))
    store.save(p)
    return p["id"]
