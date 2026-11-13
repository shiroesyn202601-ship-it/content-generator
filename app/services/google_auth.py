"""OAuth Google (satu login untuk Drive + Sheets + YouTube).
Pakai akun Google pribadi (bukan service account) karena service account tidak punya kuota
Drive sendiri dan tidak bisa upload ke channel YouTube Anda."""
import json

from .. import config

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
STATE_FILE = config.DATA_DIR / "google_state.json"   # id spreadsheet/folder yang dibuat otomatis


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not config.GOOGLE_TOKEN.exists():
        raise RuntimeError("Google belum terhubung. Jalankan: python run.py --setup-google")
    creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.GOOGLE_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def service(name: str, version: str):
    # objek service googleapiclient tidak thread-safe -> buat baru per pemanggilan
    from googleapiclient.discovery import build
    return build(name, version, credentials=credentials(), cache_discovery=False)


def interactive_setup() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not config.GOOGLE_CLIENT_SECRET.exists():
        raise SystemExit(f"File OAuth tidak ditemukan: {config.GOOGLE_CLIENT_SECRET}\n"
                         "Lihat README bagian 'Setup Google'.")
    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    config.GOOGLE_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print("Token tersimpan:", config.GOOGLE_TOKEN)


def state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def set_state(**kw) -> None:
    s = state()
    s.update(kw)
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
