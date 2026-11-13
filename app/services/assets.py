"""Tahap 3 (scratch): cari & unduh stock footage gratis dari Pexels / Pixabay."""
import shutil
from pathlib import Path

import httpx

from .. import config, store
from . import llm, prompts

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
                        "height": f.get("height"), "page_url": v.get("url"), "tags": _slug_tags(v.get("url")),
                        "author": (v.get("user") or {}).get("name"), "license": "Pexels License"})
    return out


def _slug_tags(url: str | None) -> str:
    """Pexels tidak memberi tag, tapi URL-nya berisi deskripsi: /video/aerial-view-of-rice-field-123/."""
    slug = (url or "").rstrip("/").rsplit("/", 1)[-1]
    words = [w for w in slug.split("-") if w and not w.isdigit()]
    return ", ".join(words)


def search_pexels_photos(query: str, orientation: str, n: int) -> list[dict]:
    if not config.PEXELS_API_KEY:
        return []
    r = HTTP.get("https://api.pexels.com/v1/search",
                 params={"query": query, "orientation": orientation, "per_page": n},
                 headers={"Authorization": config.PEXELS_API_KEY})
    r.raise_for_status()
    return [{"provider": "pexels", "type": "image", "id": ph["id"], "thumb": ph["src"]["medium"],
             "url": ph["src"]["large2x"], "page_url": ph.get("url"), "author": ph.get("photographer"),
             "tags": ph.get("alt") or _slug_tags(ph.get("url")), "license": "Pexels License"}
            for ph in r.json().get("photos", [])]


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
                    "page_url": h.get("pageURL"), "author": h.get("user"), "tags": h.get("tags") or "",
                    "license": "Pixabay Content License"})
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
        slot = p["assets"]["scenes"][sid]
        slot["selected"] = {**cand, "path": rel}
        # Penilaian AI hanya berlaku untuk pilihan AI; pilihan manual menghapusnya.
        if params.get("pick"):
            slot["pick"] = params["pick"]
        else:
            slot.pop("pick", None)
        store.touch_stage(p, "assets")
    store.update(pid, apply)
    return {"path": rel}


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _orientation(c: dict) -> str:
    w, h = c.get("width") or 0, c.get("height") or 0
    return "portrait" if h > w else "landscape" if w > h else "square"


def _tag_words(c: dict) -> set[str]:
    return {t.strip().lower() for t in (c.get("tags") or "").replace(",", " ").split()}


def _tag_score(c: dict, words: set[str]) -> int:
    return len(_tag_words(c) & words)


def auto_all(pid: str, params: dict, progress) -> dict:
    """Isi visual semua scene dengan bantuan AI:
    1) AI menulis 3 kata kunci per scene (spesifik → umum, sesuai budaya topik),
    2) cari semua kata kunci di Pexels/Pixabay dan kumpulkan kandidat + tag-nya,
    3) AI memilih kandidat yang paling cocok dengan narasi (fallback: skor kecocokan tag),
    4) unduh pilihan. params.redo=True → pilih ulang juga scene yang sudah punya visual."""
    p = store.load(pid)
    redo = bool(params.get("redo"))
    scenes = [s for s in p["script"]["scenes"]
              if redo or not (p["assets"]["scenes"].get(s["id"]) or {}).get("selected")]
    if not scenes:
        progress("Semua scene sudah punya visual.")
        return {"filled": 0}
    topic = p["script"].get("title") or p["title"]

    # 1) kata kunci
    queries: dict[str, list[str]] = {}
    avoid: set[str] = set()
    for batch in _chunks(scenes, 8):
        progress(f"AI menyusun kata kunci footage untuk {len(scenes)} scene…")
        try:
            res = llm.chat_json(*prompts.footage_queries(topic, batch), temperature=0.4)
            avoid |= {a.strip().lower() for a in res.get("avoid", []) if isinstance(a, str) and a.strip()}
            for r in res.get("scenes", []):
                qs = [q.strip() for q in r.get("queries", []) if isinstance(q, str) and q.strip()]
                if qs:
                    queries[r.get("id")] = qs[:3]
        except llm.LLMError as e:
            progress("AI gagal menyusun kata kunci — memakai kata kunci dari naskah…")
            store.update(pid, lambda p, e=e: store.log(p, f"Kata kunci footage AI gagal: {e}"))

    # 2) cari kandidat
    cands: dict[str, list[dict]] = {}
    used_q: dict[str, list[str]] = {}
    for i, s in enumerate(scenes):
        qs = queries.get(s["id"]) or [s.get("visual_query") or s["text"].replace("*", "")[:40]]
        used_q[s["id"]] = qs
        progress(f"Mencari footage scene {i + 1}/{len(scenes)}: {' · '.join(qs)}")
        seen, found = set(), []
        for q in qs:
            try:
                res = search(p, q, n=6)
            except httpx.HTTPError as e:
                store.update(pid, lambda p, e=e, q=q: store.log(p, f"Cari '{q}' gagal: {e}"))
                continue
            for c in res:
                key = (c["provider"], c["id"])
                if key not in seen:
                    seen.add(key)
                    found.append({**c, "query": q})
        # Buang kandidat yang tag-nya jelas salah era/budaya (kecuali narasinya memang menyebut kata itu).
        narr = s["text"].lower()
        bad = {w for w in avoid if w not in narr}
        clean = [c for c in found if not (_tag_words(c) & bad)]
        if len(clean) < len(found):
            progress(f"Scene {i + 1}: {len(found) - len(clean)} footage dibuang (tidak sesuai era/budaya topik)")
        cands[s["id"]] = (clean or found)[:12]

    # 3) AI memilih
    picks: dict[str, dict] = {}
    for batch in _chunks([s for s in scenes if cands[s["id"]]], 6):
        progress(f"AI menilai kecocokan footage dengan narasi ({len(picks) + len(batch)}/{len(scenes)} scene)…")
        items = [{"id": s["id"], "text": s["text"].replace("*", ""),
                  "candidates": [{"i": k, "tags": ", ".join((c.get("tags") or "").split(", ")[:12]),
                                  "orientation": _orientation(c), "duration": c.get("duration")}
                                 for k, c in enumerate(cands[s["id"]])]} for s in batch]
        try:
            res = llm.chat_json(*prompts.pick_footage(topic, items), temperature=0.2)
            for r in res.get("picks", []):
                picks[r.get("id")] = r
        except llm.LLMError as e:
            progress("AI gagal menilai — memilih berdasarkan kecocokan tag…")
            store.update(pid, lambda p, e=e: store.log(p, f"Pemilihan footage AI gagal: {e}"))

    # 4) simpan & unduh
    done, weak = 0, 0
    for i, s in enumerate(scenes):
        found = cands[s["id"]]

        def save(p, s=s, found=found):
            slot = p["assets"]["scenes"].setdefault(s["id"], {})
            slot["query"] = ", ".join(used_q[s["id"]])
            slot["candidates"] = found
        store.update(pid, save)
        if not found:
            store.update(pid, lambda p, s=s: store.log(p, f"Aset {s['id']}: tidak ada footage ditemukan"))
            continue
        pk = picks.get(s["id"]) or {}
        idx = pk.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(found):
            words = {w.lower() for q in used_q[s["id"]] for w in q.split()}
            idx = max(range(len(found)), key=lambda k: _tag_score(found[k], words))
            pk = {"fit": "lemah", "reason": "Dipilih dari kecocokan tag (AI tidak memberi pilihan) — mohon dicek."}
        pick = {"fit": pk.get("fit") or "lumayan", "reason": pk.get("reason") or ""}
        weak += pick["fit"] == "lemah"
        progress(f"Mengunduh footage scene {i + 1}/{len(scenes)} ({pick['fit']})…")
        try:
            select_candidate(pid, {"scene_id": s["id"], "index": idx, "pick": pick}, lambda m: None)
            done += 1
        except httpx.HTTPError as e:
            store.update(pid, lambda p, e=e, s=s: store.log(p, f"Unduh aset {s['id']} gagal: {e}"))
    return {"filled": done, "weak": weak}


UPLOAD_EXT = {".mp4", ".mov", ".mkv", ".webm", ".jpg", ".jpeg", ".png", ".webp"}


def save_upload(pid: str, sid: str, filename: str, fileobj) -> dict:
    # Keamanan: sid & ekstensi masuk ke path file → wajib divalidasi (cegah path traversal).
    if sid not in {s["id"] for s in store.load(pid)["script"]["scenes"]}:
        raise ValueError(f"Scene tidak dikenal: {sid}")
    ext = Path(filename.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower() or ".mp4"
    if ext not in UPLOAD_EXT:
        raise ValueError(f"Format file tidak didukung: {ext} (pakai {', '.join(sorted(UPLOAD_EXT))})")
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
