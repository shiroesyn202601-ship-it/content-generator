"""
Tahap 7: upload + penjadwalan otomatis via YouTube Data API v3.

Penjadwalan memakai fitur bawaan YouTube `status.publishAt`: video diunggah sebagai PRIVATE
dan YouTube sendiri yang mempublikasikannya pada waktu yang ditentukan — tidak perlu
aplikasi/komputer tetap menyala saat jam tayang.

PENTING (batasan Google):
- Project API yang belum lolos audit: semua video hasil upload API dikunci PRIVATE.
  Ajukan "YouTube API Services Audit" agar bisa publik/terjadwal.
- Kuota gratis 10.000 unit/hari; 1 upload ~1.600 unit (=> sekitar 6 upload/hari).
"""
from datetime import datetime, timezone

from .. import config, store
from . import llm, prompts
from .google_auth import service


def to_rfc3339_utc(local_dt: str) -> str:
    """'2026-11-20T19:00' (waktu lokal komputer) -> '2026-11-20T12:00:00Z'"""
    dt = datetime.fromisoformat(local_dt)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # anggap zona waktu komputer
    if dt <= datetime.now(timezone.utc):
        raise ValueError("Waktu tayang harus di masa depan.")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gen_metadata(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    progress("AI menulis judul, deskripsi, tag...")
    sys_, usr = prompts.metadata(p)
    res = llm.chat_json(sys_, usr, params.get("provider"), temperature=0.7)

    def apply(p):
        pub = p["publish"]
        pub["title"] = (res.get("title") or p["title"])[:100]
        pub["description"] = res.get("description", "")
        pub["tags"] = res.get("tags", [])[:15]
        if p["format"] == "short" and "#shorts" not in pub["description"].lower():
            pub["description"] += "\n\n#Shorts"
        store.touch_stage(p, "publish")
    store.update(pid, apply)
    return res


def upload(pid: str, params: dict, progress) -> dict:
    from googleapiclient.http import MediaFileUpload

    if not config.google_enabled():
        raise RuntimeError("Google belum terhubung (python run.py --setup-google).")
    p = store.load(pid)
    pub = p["publish"]
    status = {"privacyStatus": pub.get("privacy") or "private",
              "selfDeclaredMadeForKids": bool(pub.get("made_for_kids"))}
    if pub.get("publish_at"):
        status["privacyStatus"] = "private"            # wajib private untuk jadwal
        status["publishAt"] = to_rfc3339_utc(pub["publish_at"])
    body = {
        "snippet": {"title": pub["title"][:100], "description": pub.get("description", "")[:5000],
                    "tags": pub.get("tags", []), "categoryId": str(pub.get("category_id") or "22"),
                    "defaultLanguage": p["language"], "defaultAudioLanguage": p["language"]},
        "status": status,
    }
    video = store.project_dir(pid) / p["render"]["output_path"]
    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    req = service("youtube", "v3").videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st:
            progress(f"Upload YouTube {st.progress() * 100:.0f}%")
    vid = resp["id"]
    url = f"https://youtu.be/{vid}"
    sched = "scheduled" if status.get("publishAt") else status["privacyStatus"]

    def apply(p):
        p["publish"].update({"video_id": vid, "url": url, "status": sched, "uploaded_at": store.now_iso()})
        p["stage_status"]["publish"] = "approved"
        store.log(p, f"YouTube: {url} ({sched})")
    store.update(pid, apply)

    try:
        from . import gsheets, sync
        gsheets.upsert("Schedule", "project_id", {
            "project_id": pid, "title": pub["title"], "publish_at": status.get("publishAt", ""),
            "privacy": status["privacyStatus"], "status": sched, "youtube_video_id": vid, "url": url,
            "uploaded_at": store.now_iso(), "error": ""})
        sync.safe_sync(pid, "publish")
    except Exception as e:
        print("Sheets schedule:", e)
    return {"video_id": vid, "url": url, "status": sched}
