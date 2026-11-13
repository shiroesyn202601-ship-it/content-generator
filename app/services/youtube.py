"""
Tahap 7: upload + penjadwalan otomatis via YouTube Data API v3.

Penjadwalan memakai fitur bawaan YouTube `status.publishAt`: video diunggah sebagai PRIVATE
dan YouTube sendiri yang mempublikasikannya pada waktu yang ditentukan — tidak perlu
aplikasi/komputer tetap menyala saat jam tayang.

Batasan Google (diperbarui Nov 2026 — cek ulang di developers.google.com/youtube/v3/revision_history):
- videos.insert punya bucket kuota sendiri ("Video Uploads", ±100 upload/hari), terpisah dari
  10.000 unit/hari untuk panggilan lain.
- Sejak 2 Nov 2026 video dari project API yang belum diaudit tidak lagi dikunci private.
  privacyStatus SELALU kita isi (default private) karena bila kosong default-nya kini public.
- Aplikasi OAuth mode "Testing": login (refresh token) kedaluwarsa tiap 7 hari.
"""
import re
import time
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


def _clean(s: str) -> str:
    """YouTube menolak '<' dan '>' di judul/deskripsi (invalidTitle/invalidDescription)."""
    return re.sub(r"[<>]", "", s or "").strip()


def _fit_tags(tags: list) -> list[str]:
    """Total panjang tag maks 500 karakter (tag berspasi dihitung +2 karena diberi kutip)."""
    out, total = [], 0
    for t in tags or []:
        t = _clean(str(t))[:100]
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if total + cost > 500:
            break
        out.append(t)
        total += cost
    return out


def _retryable(e: Exception) -> bool:
    from googleapiclient.errors import HttpError
    if isinstance(e, HttpError):
        return e.resp.status in (500, 502, 503, 504)
    return isinstance(e, (ConnectionError, TimeoutError, OSError))


def gen_metadata(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    progress("AI menulis judul, deskripsi, tag...")
    sys_, usr = prompts.metadata(p)
    res = llm.chat_json(sys_, usr, params.get("provider"), temperature=0.7)

    def apply(p):
        pub = p["publish"]
        pub["title"] = (res.get("title") or p["title"])[:100]
        pub["description"] = res.get("description", "")
        pub["tags"] = _fit_tags(res.get("tags", [])[:15])
        pub["pinned_comment"] = res.get("pinned_comment", "")
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
    if pub.get("video_id") and not params.get("force"):
        raise RuntimeError(f"Proyek ini sudah pernah diunggah ({pub.get('url')}). Upload lagi akan membuat "
                           "video DUPLIKAT — konfirmasi 'upload ulang' bila memang disengaja.")
    if not (p["render"].get("output_path") and (store.project_dir(pid) / p["render"]["output_path"]).is_file()):
        raise RuntimeError("File video final tidak ditemukan. Render ulang di tahap 6.")
    if not (pub.get("title") or "").strip():
        raise RuntimeError("Judul video masih kosong.")
    status = {"privacyStatus": pub.get("privacy") or "private",
              "selfDeclaredMadeForKids": bool(pub.get("made_for_kids")),
              # Pengungkapan konten "diubah/sintetis" yang tampak realistis (kebijakan YouTube).
              "containsSyntheticMedia": bool(pub.get("synthetic_media"))}
    if pub.get("publish_at"):
        status["privacyStatus"] = "private"            # wajib private untuk jadwal
        status["publishAt"] = to_rfc3339_utc(pub["publish_at"])
    body = {
        "snippet": {"title": _clean(pub["title"])[:100], "description": _clean(pub.get("description", ""))[:5000],
                    "tags": _fit_tags(pub.get("tags", [])), "categoryId": str(pub.get("category_id") or "22"),
                    "defaultLanguage": p["language"], "defaultAudioLanguage": p["language"]},
        "status": status,
    }
    video = store.project_dir(pid) / p["render"]["output_path"]
    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    req = service("youtube", "v3").videos().insert(part="snippet,status", body=body, media_body=media)
    resp, fails = None, 0
    while resp is None:
        try:
            st, resp = req.next_chunk()
        except Exception as e:  # noqa: BLE001 — putus koneksi / 5xx: lanjutkan upload resumable
            if not _retryable(e) or fails >= 6:
                raise
            fails += 1
            wait = min(60, 2 ** fails)
            progress(f"Upload terputus ({type(e).__name__}) — melanjutkan dalam {wait} dtk (percobaan {fails}/6)…")
            time.sleep(wait)
            continue
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
