"""
Library musik & SFX bebas royalti, dicocokkan otomatis dengan mood naskah.

Struktur yang diharapkan (di Google Drive ATAU lokal di data/library/):
  music/<mood>/*.mp3     contoh: music/tegang/dark-pulse.mp3, music/ceria/ukulele.mp3
  sfx/*.mp3 (atau sfx/<kategori>/*.mp3)   contoh: sfx/whoosh-1.mp3, sfx/boom.mp3
Sumber gratis: YouTube Audio Library, Pixabay Music/SFX, freesound (CC0).
"""
import time
from pathlib import Path

from .. import config, store

LOCAL_LIB = config.DATA_DIR / "library"
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac")
_cache: dict[str, tuple[float, list]] = {}


def _local_items(kind: str) -> list[dict]:
    base = LOCAL_LIB / kind
    base.mkdir(parents=True, exist_ok=True)
    out = []
    for f in base.rglob("*"):
        if f.suffix.lower() in AUDIO_EXT:
            mood = f.parent.name.lower() if f.parent != base else ""
            out.append({"source": "local", "ref": str(f.relative_to(config.DATA_DIR)).replace("\\", "/"),
                        "name": f.name, "mood": mood})
    return out


def _drive_items(kind: str) -> list[dict]:
    folder = config.DRIVE_MUSIC_FOLDER_ID if kind == "music" else config.DRIVE_SFX_FOLDER_ID
    if not (folder and config.google_enabled()):
        return []
    from . import gdrive
    out = []
    for item in gdrive.list_children(folder):
        if item["mimeType"] == "application/vnd.google-apps.folder":
            for f in gdrive.list_children(item["id"]):
                if f["name"].lower().endswith(AUDIO_EXT):
                    out.append({"source": "drive", "ref": f["id"], "name": f["name"], "mood": item["name"].lower()})
        elif item["name"].lower().endswith(AUDIO_EXT):
            out.append({"source": "drive", "ref": item["id"], "name": item["name"], "mood": ""})
    return out


def list_library(kind: str, refresh: bool = False) -> list[dict]:
    if kind not in ("music", "sfx"):
        raise ValueError(kind)
    hit = _cache.get(kind)
    if hit and not refresh and time.time() - hit[0] < 600:
        return hit[1]
    items = _local_items(kind)
    try:
        items += _drive_items(kind)
    except Exception as e:
        print("Drive library:", e)
    _cache[kind] = (time.time(), items)
    return items


def local_path(item: dict) -> Path:
    """Pastikan file tersedia lokal (download dari Drive ke cache bila perlu)."""
    if item["source"] == "local":
        return config.DATA_DIR / item["ref"]
    dest = config.CACHE_DIR / f"{item['ref']}_{item['name']}"
    if not dest.exists():
        from . import gdrive
        gdrive.download(item["ref"], dest)
    return dest


def pick_music(mood: str) -> dict | None:
    items = list_library("music")
    same = [i for i in items if i["mood"] == mood]
    return (same or items or [None])[0]


def pick_sfx(name: str) -> dict | None:
    if not name:
        return None
    name = name.lower()
    items = list_library("sfx")
    for i in items:
        if name in i["name"].lower() or name == i["mood"]:
            return i
    return None


def dominant_mood(p: dict) -> str:
    score: dict[str, float] = {}
    timing = {t["scene_id"]: t["end"] - t["start"] for t in p["audio"].get("scene_timings", [])}
    for s in p["script"].get("scenes", []):
        score[s["mood"]] = score.get(s["mood"], 0) + timing.get(s["id"], 1)
    return max(score, key=score.get) if score else "santai"


def auto_plan(pid: str, params: dict, progress) -> dict:
    """Pilih musik latar (mood dominan) + SFX di awal tiap scene. User bisa mengganti di UI."""
    p = store.load(pid)
    progress("Membaca library audio...")
    list_library("music", refresh=True)
    list_library("sfx", refresh=True)
    mood = params.get("mood") or (dominant_mood(p) if p["mode"] == "scratch" else "santai")
    music = pick_music(mood)
    sfx = []
    if p["mode"] == "scratch":
        timing = {t["scene_id"]: t["start"] for t in p["audio"].get("scene_timings", [])}
        for s in p["script"]["scenes"]:
            item = pick_sfx(s.get("sfx", ""))
            if item and s["id"] in timing:
                sfx.append({"at": timing[s["id"]], "item": item, "volume": 0.6, "label": s["sfx"],
                            "scene_id": s["id"]})

    def apply(p):
        p["audio"]["music"].update({"mood": mood, "file": music,
                                    "enabled": bool(music) and p["audio"]["music"].get("enabled", True)})
        p["audio"]["sfx"] = sfx
        store.touch_stage(p, "audio")
        store.log(p, f"Audio otomatis: musik={music and music['name']} ({mood}), sfx={len(sfx)}")
    store.update(pid, apply)
    return {"mood": mood, "music": music, "sfx": len(sfx)}
