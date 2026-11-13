"""Google Drive sebagai penyimpanan file (gratis 15 GB).
Struktur: <Root "Shiroe Syn Studio">/Projects/<id - judul>/{script.json, vo.wav, final.mp4, ...}"""
import mimetypes
from pathlib import Path

from .. import config
from .google_auth import service, set_state, state

FOLDER = "application/vnd.google-apps.folder"


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def find(name: str, parent: str, mime: str | None = None) -> str | None:
    q = f"name = '{_q(name)}' and '{parent}' in parents and trashed = false"
    if mime:
        q += f" and mimeType = '{mime}'"
    res = service("drive", "v3").files().list(q=q, fields="files(id)", pageSize=1).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def ensure_folder(name: str, parent: str = "root") -> str:
    fid = find(name, parent, FOLDER)
    if fid:
        return fid
    meta = {"name": name, "mimeType": FOLDER, "parents": [parent]}
    return service("drive", "v3").files().create(body=meta, fields="id").execute()["id"]


def root_folder() -> str:
    if config.DRIVE_ROOT_FOLDER_ID:
        return config.DRIVE_ROOT_FOLDER_ID
    fid = state().get("drive_root")
    if not fid:
        fid = ensure_folder("Shiroe Syn Studio")
        set_state(drive_root=fid)
    return fid


def project_folder(pid: str, title: str) -> str:
    projects = ensure_folder("Projects", root_folder())
    existing = service("drive", "v3").files().list(
        q=f"name contains '{_q(pid)}' and '{projects}' in parents and mimeType = '{FOLDER}' and trashed = false",
        fields="files(id)", pageSize=1).execute().get("files", [])
    return existing[0]["id"] if existing else ensure_folder(f"{pid} - {title[:60]}", projects)


def upload(local: Path, parent: str, name: str | None = None) -> str:
    """Upload (atau timpa bila nama sama) — resumable untuk file video besar."""
    from googleapiclient.http import MediaFileUpload

    name = name or local.name
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(local), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
    drv = service("drive", "v3")
    fid = find(name, parent)
    if fid:
        req = drv.files().update(fileId=fid, media_body=media, fields="id")
    else:
        req = drv.files().create(body={"name": name, "parents": [parent]}, media_body=media, fields="id")
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def download(file_id: str, dest: Path) -> Path:
    from googleapiclient.http import MediaIoBaseDownload

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = service("drive", "v3").files().get_media(fileId=file_id)
    with open(dest, "wb") as f:
        dl = MediaIoBaseDownload(f, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
    return dest


def list_children(folder_id: str) -> list[dict]:
    out, token = [], None
    drv = service("drive", "v3")
    while True:
        res = drv.files().list(q=f"'{folder_id}' in parents and trashed = false",
                               fields="nextPageToken, files(id, name, mimeType)",
                               pageSize=200, pageToken=token).execute()
        out += res.get("files", [])
        token = res.get("nextPageToken")
        if not token:
            return out
