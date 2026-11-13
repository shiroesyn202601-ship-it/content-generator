"""Job runner sederhana untuk proses berat (download, whisper, TTS, render, upload).
UI melakukan polling ke /api/jobs/<id>.

Setiap job mencatat langkah-langkahnya (steps) supaya UI bisa menjelaskan sedang menunggu apa.
Kode yang dalam (mis. llm.py) bisa melapor lewat jobs.report() tanpa perlu menerima callback."""
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_local = threading.local()  # progress callback milik job yang sedang berjalan di thread ini
MAX_STEPS = 30


def report(msg: str) -> None:
    """Laporkan langkah yang sedang dikerjakan ke job aktif (diabaikan bila tidak di dalam job)."""
    fn = getattr(_local, "progress", None)
    if fn:
        fn(msg)


def submit(project_id: str, kind: str, fn, *args, **kwargs) -> str:
    jid = uuid.uuid4().hex[:10]
    now = time.time()
    job = {"id": jid, "project_id": project_id, "kind": kind, "status": "running",
           "progress": "", "result": None, "error": None,
           "started_at": now, "step_started_at": now, "steps": []}
    with _lock:
        # Bersihkan job selesai yang sudah > 1 jam supaya memori tidak terus membesar.
        for old in [k for k, j in _jobs.items() if j.get("finished_at") and now - j["finished_at"] > 3600]:
            del _jobs[old]
        _jobs[jid] = job

    def progress(msg: str):
        if msg == job["progress"]:
            return
        t = time.time()
        # Langkah persentase (mis. "Transkrip 41%") cukup memperbarui baris terakhir, bukan menambah baris.
        steps = job["steps"]
        if steps and _same_step(steps[-1]["msg"], msg):
            steps[-1]["msg"] = msg
        else:
            if steps:
                steps[-1]["took"] = round(t - steps[-1]["at"], 1)
            steps.append({"msg": msg, "at": t, "took": None})
            del steps[:-MAX_STEPS]
            job["step_started_at"] = t
        job["progress"] = msg

    def run():
        _local.progress = progress
        try:
            job["result"] = fn(*args, progress=progress, **kwargs)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001 — error ditampilkan ke UI
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            _local.progress = None
            job["finished_at"] = time.time()
            if job["steps"] and job["steps"][-1]["took"] is None:
                job["steps"][-1]["took"] = round(job["finished_at"] - job["steps"][-1]["at"], 1)

    _pool.submit(run)
    return jid


def _same_step(a: str, b: str) -> bool:
    """Dua pesan dianggap langkah yang sama bila hanya beda angka (progress persen/hitungan)."""
    strip = lambda s: "".join(c for c in s if not c.isdigit() and c not in "%.,/()")  # noqa: E731
    return strip(a) == strip(b)


def get(jid: str) -> dict | None:
    j = _jobs.get(jid)
    if j is None:
        return None
    end = j.get("finished_at") or time.time()
    return {**j, "steps": [dict(s) for s in j["steps"]],
            "elapsed": round(end - j["started_at"], 1),
            "step_elapsed": round(end - j["step_started_at"], 1)}


def active_for(project_id: str) -> list[dict]:
    """Job yang masih berjalan untuk proyek ini (ringkas; dipakai UI untuk menyambung ulang polling)."""
    with _lock:
        running = [j for j in _jobs.values() if j["project_id"] == project_id and j["status"] == "running"]
    return [{"id": j["id"], "kind": j["kind"], "progress": j["progress"]} for j in running]
