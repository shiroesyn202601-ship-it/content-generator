"""Job runner sederhana untuk proses berat (download, whisper, TTS, render, upload).
UI melakukan polling ke /api/jobs/<id>."""
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def submit(project_id: str, kind: str, fn, *args, **kwargs) -> str:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "project_id": project_id, "kind": kind, "status": "running",
           "progress": "", "result": None, "error": None}
    with _lock:
        _jobs[jid] = job

    def progress(msg: str):
        job["progress"] = msg

    def run():
        try:
            job["result"] = fn(*args, progress=progress, **kwargs)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001 — error ditampilkan ke UI
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()

    _pool.submit(run)
    return jid


def get(jid: str) -> dict | None:
    return _jobs.get(jid)


def active_for(project_id: str) -> list[dict]:
    return [j for j in _jobs.values() if j["project_id"] == project_id and j["status"] == "running"]
