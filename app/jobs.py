"""Job runner sederhana untuk proses berat (download, whisper, TTS, render, upload).
UI melakukan polling ke /api/jobs/<id>.

Setiap job mencatat langkah-langkahnya (steps) supaya UI bisa menjelaskan sedang menunggu apa.
Kode yang dalam (mis. llm.py) bisa melapor lewat jobs.report() tanpa perlu menerima callback.

Ketahanan:
- Status job disimpan ke data/jobs.json. Kalau server restart/reload saat job berjalan, job itu
  ditandai "interrupted" (bukan hilang tanpa jejak) dan UI menampilkan pesan yang jelas.
- Job bisa dibatalkan: progress()/report() melempar JobCancelled, dan proses FFmpeg yang sedang
  berjalan dihentikan (lihat ffmpeg_utils.run → cancelled())."""
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import config

_pool = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict] = {}
_cancel: dict[str, threading.Event] = {}
_lock = threading.Lock()
_local = threading.local()  # progress callback & id job yang sedang berjalan di thread ini
MAX_STEPS = 30
JOBS_FILE = config.DATA_DIR / "jobs.json"
KEEP_SEC = 6 * 3600


class JobCancelled(Exception):
    pass


def report(msg: str) -> None:
    """Laporkan langkah yang sedang dikerjakan ke job aktif (diabaikan bila tidak di dalam job)."""
    fn = getattr(_local, "progress", None)
    if fn:
        fn(msg)


def cancelled() -> bool:
    """True bila job di thread ini diminta batal (dipakai loop panjang, mis. FFmpeg)."""
    ev = _cancel.get(getattr(_local, "jid", None) or "")
    return bool(ev and ev.is_set())


def cancel(jid: str) -> bool:
    ev = _cancel.get(jid)
    j = _jobs.get(jid)
    if not ev or not j or j["status"] != "running":
        return False
    ev.set()
    j["progress"] = "Membatalkan…"
    return True


def _save() -> None:
    """Simpan ringkasan job (tanpa hasil besar) supaya bisa dibaca lagi setelah restart."""
    try:
        with _lock:
            snap = {k: {kk: v for kk, v in j.items() if kk != "result"} for k, j in _jobs.items()}
        tmp = JOBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        tmp.replace(JOBS_FILE)
    except OSError:
        pass


def _load() -> None:
    """Saat server mulai: job yang tercatat 'running' pasti terputus oleh restart/reload."""
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    now = time.time()
    for jid, j in data.items():
        if now - (j.get("finished_at") or j.get("started_at") or 0) > KEEP_SEC:
            continue
        if j.get("status") == "running":
            j.update(status="interrupted", finished_at=now,
                     error="Proses terhenti karena server di-restart/reload saat berjalan. Jalankan ulang aksinya.")
        j.setdefault("result", None)
        _jobs[jid] = j
    _save()


def submit(project_id: str, kind: str, fn, *args, **kwargs) -> str:
    jid = uuid.uuid4().hex[:10]
    now = time.time()
    job = {"id": jid, "project_id": project_id, "kind": kind, "status": "running",
           "progress": "", "result": None, "error": None,
           "started_at": now, "step_started_at": now, "steps": []}
    with _lock:
        for old in [k for k, j in _jobs.items() if j.get("finished_at") and now - j["finished_at"] > KEEP_SEC]:
            del _jobs[old]
            _cancel.pop(old, None)
        _jobs[jid] = job
        _cancel[jid] = threading.Event()
    _save()

    def progress(msg: str):
        if _cancel[jid].is_set():
            raise JobCancelled()
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
        _local.progress, _local.jid = progress, jid
        try:
            job["result"] = fn(*args, progress=progress, **kwargs)
            job["status"] = "done"
        except JobCancelled:
            job["status"] = "cancelled"
            job["error"] = "Dibatalkan oleh pengguna."
        except Exception as e:  # noqa: BLE001 — error ditampilkan ke UI
            if _cancel[jid].is_set():
                job["status"], job["error"] = "cancelled", "Dibatalkan oleh pengguna."
            else:
                job["status"] = "error"
                job["error"] = f"{type(e).__name__}: {e}"
                traceback.print_exc()
        finally:
            _local.progress = _local.jid = None
            job["finished_at"] = time.time()
            if job["steps"] and job["steps"][-1]["took"] is None:
                job["steps"][-1]["took"] = round(job["finished_at"] - job["steps"][-1]["at"], 1)
            _save()

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


def recent_interrupted(project_id: str) -> list[dict]:
    with _lock:
        return [{"id": j["id"], "kind": j["kind"], "error": j.get("error")} for j in _jobs.values()
                if j["project_id"] == project_id and j["status"] == "interrupted"]


_load()
