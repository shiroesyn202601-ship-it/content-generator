"""Tahap 3 (scratch): cari & unduh stock footage gratis dari Pexels / Pixabay."""
import shutil
from pathlib import Path

import httpx

from .. import config, store

HTTP = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True)


def _best_file(files: list[dict], target_h: int) -> dict | None:
    mp4 = [f for f in files if (f.get("file_type") or "video/mp4") == "video/mp4" and f.get("link")]
    if not mp4:
        return None
    return min(mp4, key=lambda f: abs((max(f.get("height") or 0, f.get("width") or 0)) - target_h))


def search_pexels(query: str, orientation: str, n: int, target: int) -> list[dict]:
    if not config.PEXELS_API_KEY:
        return []
    r = HTTP.get("https://api.pexels.com/videos/search",
                 params={"query": query, "orientation": orientation, "per_page": n},
                 headers={"Authorization": config.PEXELS_API_KEY})
    r.raise_for_status()
    out = []
    for v in r.json().get("videos", []):
        f = _best_file(v.get("video_files", []), target)
        if f:
            out.append({"provider": "pexels", "type": "video", "id": v["id"], "thumb": v.get("image"),
                        "url": f["link"], "duration": v.get("duration"), "width": f.get("width"),
                        "height": f.get("height"), "page_url": v.get("url"),
                        "author": (v.get("user") or {}).get("name"), "license": "Pexels License"})
    return out


def search_pexels_photos(query: str, orientation: str, n: int) -> list[dict]:
    if not config.PEXELS_API_KEY:
        return []
    r = HTTP.get("https://api.pexels.com/v1/search",
                 params={"query": query, "orientation": orientation, "per_page": n},
                 headers={"Authorization": config.PEXELS_API_KEY})
    r.raise_for_status()
    return [{"provider": "pexels", "type": "image", "id": ph["id"], "thumb": ph["src"]["medium"],
             "url": ph["src"]["large2x"], "page_url": ph.get("url"), "author": ph.get("photographer"),
             "license": "Pexels License"} for ph in r.json().get("photos", [])]


def search_pixabay(query: str, orientation: str, n: int) -> list[dict]:
    if not config.PIXABAY_API_KEY:
        return []
    r = HTTP.get("https://pixabay.com/api/videos/",
                 params={"key": config.PIXABAY_API_KEY, "q": query, "per_page": max(3, n), "safesearch": "true"})
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        vids = h.get("videos") or {}
        f = vids.get("large") if (vids.get("large") or {}).get("url") else vids.get("medium") or {}
        if not f.get("url"):
            continue
        portrait = (f.get("height") or 0) > (f.get("width") or 0)
        if orientation == "portrait" and not portrait and len(out) >= 3:
            continue  # Pixabay tak punya filter orientasi; prioritaskan yang cocok
        thumb = (vids.get("medium") or {}).get("thumbnail") or (vids.get("tiny") or {}).get("thumbnail")
        out.append({"provider": "pixabay", "type": "video", "id": h["id"], "thumb": thumb, "url": f["url"],
                    "duration": h.get("duration"), "width": f.get("width"), "height": f.get("height"),
                    "page_url": h.get("pageURL"), "author": h.get("user"), "license": "Pixabay Content License"})
    return out


def search(p: dict, query: str, provider: str = "all", n: int = 8) -> list[dict]:
    orientation = "portrait" if p["format"] == "short" else "landscape"
    target = 1920
    res = []
    if provider in ("all", "pexels"):
        res += search_pexels(query, orientation, n, target)
    if provider in ("all", "pixabay"):
        res += search_pixabay(query, orientation, n)
    if provider in ("all", "photos") and len(res) < 4:
        res += search_pexels_photos(query, orientation, n)
    if not res and not (config.PEXELS_API_KEY or config.PIXABAY_API_KEY):
        raise RuntimeError("Isi PEXELS_API_KEY / PIXABAY_API_KEY di .env (gratis).")
    return res


def search_scene(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    sid = params["scene_id"]
    scene = next(s for s in p["script"]["scenes"] if s["id"] == sid)
    query = params.get("query") or scene.get("visual_query") or scene["text"][:40]
    progress(f"Mencari '{query}'...")
    cands = search(p, query, params.get("provider", "all"))

    def apply(p):
        slot = p["assets"]["scenes"].setdefault(sid, {})
        slot["query"] = query
        slot["candidates"] = cands
    store.update(pid, apply)
    return {"count": len(cands)}


def _download(url: str, dest: Path) -> None:
    with HTTP.stream("GET", url) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)


def select_candidate(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    sid, idx = params["scene_id"], int(params["index"])
    cand = p["assets"]["scenes"][sid]["candidates"][idx]
    ext = ".mp4" if cand["type"] == "video" else ".jpg"
    rel = f"assets/{sid}_{cand['provider']}_{cand['id']}{ext}"
    dest = store.project_dir(pid) / rel
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        progress("Mengunduh aset...")
        _download(cand["url"], dest)

    def apply(p):
        p["assets"]["scenes"][sid]["selected"] = {**cand, "path": rel}
        store.touch_stage(p, "assets")
    store.update(pid, apply)
    return {"path": rel}


def auto_all(pid: str, params: dict, progress) -> dict:
    """Cari + pilih kandidat pertama untuk setiap scene yang belum punya visual."""
    p = store.load(pid)
    done = 0
    for i, s in enumerate(p["script"]["scenes"]):
        slot = p["assets"]["scenes"].get(s["id"]) or {}
        if slot.get("selected"):
            continue
        progress(f"Scene {i + 1}/{len(p['script']['scenes'])}: {s.get('visual_query')}")
        try:
            search_scene(pid, {"scene_id": s["id"]}, progress)
            select_candidate(pid, {"scene_id": s["id"], "index": 0}, progress)
            done += 1
        except (IndexError, KeyError, httpx.HTTPError) as e:
            store.update(pid, lambda p, e=e, s=s: store.log(p, f"Aset {s['id']} gagal: {e}"))
    return {"filled": done}


def save_upload(pid: str, sid: str, filename: str, fileobj) -> dict:
    ext = Path(filename).suffix.lower() or ".mp4"
    rel = f"assets/{sid}_manual{ext}"
    dest = store.project_dir(pid) / rel
    dest.parent.mkdir(exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(fileobj, f)
    typ = "image" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"

    def apply(p):
        p["assets"]["scenes"].setdefault(sid, {})["selected"] = {
            "provider": "manual", "type": typ, "path": rel, "license": "milik sendiri"}
        store.touch_stage(p, "assets")
    return store.update(pid, apply)
