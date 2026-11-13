"""Google Sheets sebagai database gratis. Skema tab (baris 1 = header):"""
from .. import config
from .google_auth import service, set_state, state

SCHEMA = {
    "Projects": ["project_id", "created_at", "updated_at", "mode", "format", "title", "current_stage",
                 "stage_status", "keyword_or_source", "drive_folder_id", "project_json_drive_id",
                 "final_video_drive_id", "youtube_video_id", "publish_at", "privacy", "notes"],
    "Scripts": ["project_id", "order", "scene_id", "emotion", "mood", "text", "visual_query", "sfx",
                "start", "end"],
    "Assets": ["project_id", "scene_or_clip", "type", "provider", "author", "license", "page_url",
               "local_path", "logged_at"],
    "Schedule": ["project_id", "title", "publish_at", "privacy", "status", "youtube_video_id", "url",
                 "uploaded_at", "error"],
    "Pronunciation": ["term", "phonetic", "note"],
}


def sheet_id() -> str:
    if config.SHEET_ID:
        return config.SHEET_ID
    sid = state().get("sheet_id")
    if not sid:
        body = {"properties": {"title": "Shiroe Syn Studio DB"},
                "sheets": [{"properties": {"title": t}} for t in SCHEMA]}
        sid = service("sheets", "v4").spreadsheets().create(body=body, fields="spreadsheetId").execute()["spreadsheetId"]
        set_state(sheet_id=sid)
        for tab, hdr in SCHEMA.items():
            _values().update(spreadsheetId=sid, range=f"{tab}!A1", valueInputOption="RAW",
                             body={"values": [hdr]}).execute()
    return sid


def ensure_schema() -> str:
    """Buat tab/header yang belum ada (aman dipanggil berulang)."""
    sid = sheet_id()
    meta = service("sheets", "v4").spreadsheets().get(spreadsheetId=sid).execute()
    have = {s["properties"]["title"] for s in meta["sheets"]}
    missing = [t for t in SCHEMA if t not in have]
    if missing:
        service("sheets", "v4").spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
            {"addSheet": {"properties": {"title": t}}} for t in missing]}).execute()
    for tab, hdr in SCHEMA.items():
        _values().update(spreadsheetId=sid, range=f"{tab}!A1", valueInputOption="RAW",
                         body={"values": [hdr]}).execute()
    return sid


def _values():
    return service("sheets", "v4").spreadsheets().values()


def _row(tab: str, d: dict) -> list:
    return [_cell(d.get(h, "")) for h in SCHEMA[tab]]


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        import json
        v = json.dumps(v, ensure_ascii=False)
    s = str(v) if not isinstance(v, (int, float)) else v
    return s[:45000] if isinstance(s, str) else s   # batas sel Sheets = 50.000 karakter


def read_all(tab: str) -> list[dict]:
    rows = _values().get(spreadsheetId=sheet_id(), range=f"{tab}!A:Z").execute().get("values", [])
    if not rows:
        return []
    hdr = rows[0]
    return [dict(zip(hdr, r + [""] * (len(hdr) - len(r)))) for r in rows[1:]]


def upsert(tab: str, key: str, d: dict) -> None:
    sid = sheet_id()
    col = _values().get(spreadsheetId=sid, range=f"{tab}!A:A").execute().get("values", [])
    keys = [r[0] if r else "" for r in col]
    if d[key] in keys:
        n = keys.index(d[key]) + 1
        _values().update(spreadsheetId=sid, range=f"{tab}!A{n}", valueInputOption="RAW",
                         body={"values": [_row(tab, d)]}).execute()
    else:
        append(tab, [d])


def append(tab: str, rows: list[dict]) -> None:
    if rows:
        _values().append(spreadsheetId=sheet_id(), range=f"{tab}!A1", valueInputOption="RAW",
                         insertDataOption="INSERT_ROWS", body={"values": [_row(tab, r) for r in rows]}).execute()


def replace_rows(tab: str, key: str, key_value: str, rows: list[dict]) -> None:
    """Ganti semua baris milik satu proyek (dipakai untuk tab Scripts/Assets)."""
    sid = sheet_id()
    keep = [r for r in read_all(tab) if r.get(key) != key_value]
    data = [SCHEMA[tab]] + [_row(tab, r) for r in keep] + [_row(tab, r) for r in rows]
    _values().clear(spreadsheetId=sid, range=f"{tab}!A:Z").execute()
    _values().update(spreadsheetId=sid, range=f"{tab}!A1", valueInputOption="RAW", body={"values": data}).execute()


def get_pronunciation() -> dict:
    return {r["term"].strip().lower(): r["phonetic"].strip() for r in read_all("Pronunciation")
            if r.get("term", "").strip() and r.get("phonetic", "").strip()}


def save_pronunciation(d: dict) -> None:
    old = {r["term"].strip().lower(): r.get("note", "") for r in read_all("Pronunciation")}
    sid = sheet_id()
    data = [SCHEMA["Pronunciation"]] + [[k, v, old.get(k, "")] for k, v in sorted(d.items())]
    _values().clear(spreadsheetId=sid, range="Pronunciation!A:Z").execute()
    _values().update(spreadsheetId=sid, range="Pronunciation!A1", valueInputOption="RAW",
                     body={"values": data}).execute()
