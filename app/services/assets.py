"""Tahap 3 (scratch): cari & unduh stock footage gratis dari Pexels / Pixabay."""
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import httpx

from .. import config, store
from . import llm, prompts, vision

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


CACHE_TTL = 24 * 3600  # aturan API Pixabay: hasil pencarian wajib di-cache 24 jam


def _cached_get(url: str, params: dict, key_param: str = "key", client: httpx.Client | None = None) -> dict:
    """GET JSON dengan cache disk 24 jam (kunci cache tanpa API key)."""
    ident = json.dumps({"u": url, **{k: v for k, v in params.items() if k != key_param}}, sort_keys=True)
    f = config.CACHE_DIR / "search" / (hashlib.sha1(ident.encode()).hexdigest() + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    r = (client or HTTP).get(url, params=params)
    if r.status_code == 429:
        raise RuntimeError("Batas Pixabay tercapai (100 permintaan/menit). Tunggu 1 menit lalu coba lagi.")
    r.raise_for_status()
    data = r.json()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def search_pixabay(query: str, orientation: str, n: int) -> list[dict]:
    if not config.PIXABAY_API_KEY:
        return []
    # Pixabay video tidak punya filter orientasi → ambil lebih banyak, lalu urutkan yang cocok di depan.
    data = _cached_get("https://pixabay.com/api/videos/",
                       {"key": config.PIXABAY_API_KEY, "q": query[:100], "per_page": 20, "safesearch": "true"})
    out = []
    for h in data.get("hits", []):
        vids = h.get("videos") or {}
        f = vids.get("large") if (vids.get("large") or {}).get("url") else vids.get("medium") or {}
        if not f.get("url"):
            continue
        thumb = (vids.get("medium") or {}).get("thumbnail") or (vids.get("tiny") or {}).get("thumbnail")
        out.append({"provider": "pixabay", "type": "video", "id": h["id"], "thumb": thumb, "url": f["url"],
                    "duration": h.get("duration"), "width": f.get("width"), "height": f.get("height"),
                    "page_url": h.get("pageURL"), "author": h.get("user"), "tags": h.get("tags") or "",
                    "license": "Pixabay Content License"})
    want_portrait = orientation == "portrait"
    # sort stabil: orientasi cocok dulu, urutan relevansi Pixabay tetap terjaga di dalam tiap kelompok
    out.sort(key=lambda c: ((c.get("height") or 0) > (c.get("width") or 0)) != want_portrait)
    return out[:max(3, n)]


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _commons_license(short: str) -> tuple[bool, bool]:
    """(boleh dipakai, wajib atribusi). Hanya PD/CC0/CC BY/CC BY-SA — tolak NC/ND/fair use/lainnya."""
    s = (short or "").lower()
    if "nc" in s.split("-") or "-nd" in s or "fair" in s or "non-commercial" in s:
        return False, False
    if s.startswith(("public domain", "pd")) or "cc0" in s:
        return True, False
    if s.startswith("cc by"):
        return True, True
    return False, False


def search_commons(query: str, n: int = 6) -> list[dict]:
    """Wikimedia Commons: foto artefak/tempat bersejarah yang tidak ada di situs stok.
    Lisensi dicek per file; atribusi disimpan untuk deskripsi YouTube."""
    from .factsource import HTTP as WM  # klien dengan User-Agent sesuai kebijakan Wikimedia
    data = _cached_get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6, "gsrlimit": n * 2, "prop": "imageinfo", "iiprop": "url|size|extmetadata",
        "iiurlwidth": 1600}, client=WM)
    out = []
    pages = sorted((data.get("query", {}).get("pages") or {}).values(), key=lambda x: x.get("index", 0))
    for pg in pages:
        ii = (pg.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        lic = (md.get("LicenseShortName") or {}).get("value", "")
        allowed, attrib = _commons_license(lic)
        if not allowed or (ii.get("width") or 0) < 700 or not ii.get("thumburl"):
            continue
        title = re.sub(r"^File:|\.[a-z]{3,4}$", "", pg.get("title", ""), flags=re.I)
        desc = _strip_html((md.get("ImageDescription") or {}).get("value", ""))[:200]
        out.append({"provider": "commons", "type": "image", "id": str(pg.get("pageid")), "thumb": ii["thumburl"],
                    "url": ii["thumburl"], "width": ii.get("thumbwidth"), "height": ii.get("thumbheight"),
                    "page_url": ii.get("descriptionurl"), "author": _strip_html((md.get("Artist") or {}).get("value", ""))[:80],
                    "license": lic, "license_url": (md.get("LicenseUrl") or {}).get("value", ""),
                    "requires_attribution": attrib, "title": title,
                    "tags": f"{title}. {desc}".lower()})
        if len(out) >= n:
            break
    return out


AI_STYLE = ("photorealistic, cinematic lighting, highly detailed, historically and culturally accurate, "
            "no text, no watermark, no logo")


def search_ai(prompt: str, orientation: str, n: int = 2) -> list[dict]:
    """Gambar AI gratis (Pollinations, model FLUX, tanpa API key). URL-nya sendiri memicu pembuatan gambar
    (±5-10 dtk). Dipakai bila stok & Commons tidak punya visual yang cocok. Ditandai sintetis."""
    from urllib.parse import quote
    w, h = (720, 1280) if orientation == "portrait" else (1280, 720)
    full = f"{prompt}, {AI_STYLE}"
    out = []
    from . import imagegen
    if imagegen.available():
        # Cloudflare FLUX: gambar dibuat saat dipilih (_download mengenali skema "cfimg:"), tanpa watermark.
        for seed in range(1, n + 1):
            out.append({"provider": "ai", "type": "image", "id": f"cf{abs(hash(full)) % 10 ** 8}_{seed}", "thumb": "",
                        "url": f"cfimg:{w}x{h}:{seed}:{quote(full)}", "width": w, "height": h,
                        "page_url": "https://developers.cloudflare.com/workers-ai/", "author": "AI (Cloudflare FLUX)",
                        "license": "Gambar buatan AI", "synthetic": True, "tags": f"ai generated, {prompt}".lower()})
        return out
    for seed in range(1, n + 1):
        url = (f"https://image.pollinations.ai/prompt/{quote(full)}?width={w}&height={h}"
               f"&nologo=true&seed={seed}&model=flux")
        out.append({"provider": "ai", "type": "image", "id": f"ai{abs(hash(full)) % 10 ** 8}_{seed}", "thumb": url,
                    "url": url, "width": w, "height": h, "page_url": "https://pollinations.ai",
                    "author": "AI (Pollinations/FLUX)", "license": "Gambar buatan AI", "synthetic": True,
                    "tags": f"ai generated, {prompt}".lower()})
    return out


def credits(p: dict) -> str:
    """Blok 'Kredit visual' untuk deskripsi YouTube (wajib bagi CC BY / CC BY-SA)."""
    lines = []
    for slot in (p.get("assets", {}).get("scenes") or {}).values():
        for sel in [slot.get("selected") or {}, *(slot.get("extras") or [])]:
            if not sel.get("requires_attribution"):
                continue
            line = f"• \"{sel.get('title') or 'Gambar'}\" oleh {sel.get('author') or 'tidak diketahui'}, {sel.get('license')}"
            if sel.get("page_url"):
                line += f" — {sel['page_url']}"
            if line not in lines:
                lines.append(line)
    from . import enhance
    lines += [x for x in enhance.credits(p) if x not in lines]
    return ("Kredit visual & sumber:\n" + "\n".join(lines)) if lines else ""


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
    if provider in ("all", "commons"):
        try:
            res += search_commons(query, 4 if provider == "all" else n)
        except (httpx.HTTPError, RuntimeError):
            pass
    if provider == "ai":
        return search_ai(query, orientation, 3)
    if not res and provider != "commons" and not (config.PEXELS_API_KEY or config.PIXABAY_API_KEY):
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
    from .factsource import UA  # Wikimedia menolak (403) unduhan tanpa User-Agent deskriptif
    if url.startswith("cfimg:"):  # gambar AI Cloudflare: dibuat sekarang
        from urllib.parse import unquote
        from . import imagegen
        _, size, seed, prompt = url.split(":", 3)
        w, h = (int(x) for x in size.split("x"))
        dest.write_bytes(imagegen.cf_image(unquote(prompt), w, h, int(seed)))
        return
    headers = UA if "wikimedia.org" in url or "wikipedia.org" in url else None
    with HTTP.stream("GET", url, headers=headers) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)


def _crop_watermark(path: Path) -> None:
    """Pollinations versi gratis tanpa akun menaruh watermark kecil di pojok kanan bawah → buang 6% bawah."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.crop((0, 0, im.width, int(im.height * 0.94))).save(path, "JPEG", quality=92)
    except Exception:  # noqa: BLE001 — gagal crop bukan masalah fatal
        pass


def select_candidate(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    sid, idx = params["scene_id"], int(params["index"])
    cand = p["assets"]["scenes"][sid]["candidates"][idx]
    ext = ".mp4" if cand["type"] == "video" else ".jpg"
    rel = f"assets/{sid}_{cand['provider']}_{cand['id']}{ext}"
    dest = store.project_dir(pid) / rel
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists():
        progress("Mengunduh aset…" if cand["provider"] != "ai" else "Membuat gambar AI (±5-10 dtk)…")
        _download(cand["url"], dest)
        if cand["provider"] == "ai" and not cand["url"].startswith("cfimg:"):
            _crop_watermark(dest)

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


VISION_MAX = 3


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
    commons_q: dict[str, str] = {}
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
                cq = (r.get("commons_query") or "").strip()
                if cq:
                    commons_q[r.get("id")] = cq
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
        # Foto asli dari Wikimedia Commons (artefak/tempat bersejarah) ditaruh di depan bila ada.
        if commons_q.get(s["id"]):
            try:
                for c in search_commons(commons_q[s["id"]], 3):
                    seen.add((c["provider"], c["id"]))
                    found.append({**c, "query": commons_q[s["id"]]})
            except (httpx.HTTPError, RuntimeError) as e:
                store.update(pid, lambda p, e=e: store.log(p, f"Cari Commons gagal: {e}"))
        for q in qs:
            try:
                res = search(p, q, provider="pixabay" if not config.PEXELS_API_KEY else "all", n=6)
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

    # 3a) Penilaian murah dulu: AI menilai SEMUA scene dari tag (1 panggilan per 6 scene).
    picks: dict[str, dict] = {}
    for batch in _chunks([s for s in scenes if cands[s["id"]]], 6):
        progress(f"AI menilai kecocokan footage dari tag ({len(picks) + len(batch)}/{len(scenes)} scene)…")
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

    # 3b) Juri visual: AI MELIHAT lembar kontak thumbnail, hanya untuk scene yang belum "cocok".
    #     Model vision gratis punya batas per menit yang ketat → maks VISION_MAX scene per proses.
    if params.get("vision", True) and llm.vision_available():
        doubtful = [s for s in scenes if cands[s["id"]] and (picks.get(s["id"]) or {}).get("fit") != "cocok"]
        for i, s in enumerate(doubtful[:VISION_MAX]):
            progress(f"AI melihat thumbnail kandidat scene {scenes.index(s) + 1} "
                     f"(penilaian tag belum yakin, {i + 1}/{min(len(doubtful), VISION_MAX)})…")
            try:
                v = vision.pick(topic, s["text"].replace("*", ""),
                                [c.get("thumb") or "" for c in cands[s["id"]]], sorted(avoid))
            except Exception as e:  # noqa: BLE001 — gagal visual → tetap pakai penilaian tag
                msg = f"Juri visual gagal di scene {scenes.index(s) + 1}: {str(e)[:160]}"
                store.update(pid, lambda p, msg=msg: store.log(p, msg))
                progress(msg + " — memakai penilaian tag.")
                break
            if v:
                picks[s["id"]] = {**v, "reason": "👁 " + v["reason"][:160]}

    # 4) simpan & unduh
    done, weak = 0, 0
    orient = "portrait" if p["format"] == "short" else "landscape"
    for i, s in enumerate(scenes):
        found = cands[s["id"]]
        ai_prompt = (used_q[s["id"]] or [s.get("visual_query") or topic])[0]
        if params.get("ai_fallback", True):
            if not found:  # stok & Commons kosong → gambar AI supaya scene tidak polos
                found = search_ai(ai_prompt, orient, 2)
                picks[s["id"]] = {"index": 0, "fit": "lumayan",
                                  "reason": "🎨 Tidak ada stok/foto yang cocok — memakai gambar AI (cek akurasinya)"}
            elif (picks.get(s["id"]) or {}).get("fit") == "lemah":
                found = found + search_ai(ai_prompt, orient, 2)  # opsi tambahan untuk dipilih manual

        def save(p, s=s, found=found):
            slot = p["assets"]["scenes"].setdefault(s["id"], {})
            slot["query"] = ", ".join(used_q[s["id"]])
            slot["candidates"] = found
            slot.pop("extras", None)  # variasi lama milik pilihan lama
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
        words = {w.lower() for q in used_q[s["id"]] for w in q.split()}
        # Unduhan gagal (403/timeout/link kedaluwarsa) → coba kandidat relevan berikutnya, maks 3 percobaan.
        order = [idx] + sorted((k for k in range(len(found)) if k != idx), key=lambda k: -_tag_score(found[k], words))
        ok = False
        for attempt, k in enumerate(order[:3]):
            try:
                pk2 = pick if k == idx else {"fit": "lumayan", "reason": "Pilihan pertama gagal diunduh — memakai kandidat berikutnya (cek)."}
                select_candidate(pid, {"scene_id": s["id"], "index": k, "pick": pk2}, lambda m: None)
                idx, ok = k, True
                break
            except (httpx.HTTPError, RuntimeError) as e:
                store.update(pid, lambda p, e=e, s=s: store.log(p, f"Unduh aset {s['id']} gagal: {str(e).splitlines()[0]}"))
                if attempt < 2 and len(order) > attempt + 1:
                    progress(f"Scene {i + 1}: unduhan gagal — mencoba kandidat lain…")
        if not ok:
            continue
        done += 1
        if p["format"] == "long" and params.get("extras", True):
            add_extras(pid, s, found, idx, words, progress)
    return {"filled": done, "weak": weak}


def add_extras(pid: str, scene: dict, found: list[dict], idx: int, words: set[str], progress) -> int:
    """Video panjang: scene 10+ detik diberi 1-2 footage variasi (kandidat relevan berikutnya) supaya potongan
    dinamis tidak memutar klip yang sama terus. Gambar AI dilewati (lambat & kurang akurat)."""
    n_words = len(scene["text"].replace("*", "").split())
    want = 0 if n_words < 22 else 1 if n_words < 45 else 2
    if not want:
        return 0
    pool = [c for k, c in enumerate(found) if k != idx and c.get("provider") != "ai" and c.get("url")]
    pool.sort(key=lambda c: (c.get("type") != "video", -_tag_score(c, words)))
    d = store.project_dir(pid)
    extras = []
    for c in pool[:want]:
        ext = ".mp4" if c["type"] == "video" else ".jpg"
        rel = f"assets/{scene['id']}_x_{c['provider']}_{c['id']}{ext}"
        try:
            if not (d / rel).exists():
                _download(c["url"], d / rel)
            extras.append({**c, "path": rel})
        except httpx.HTTPError:
            continue
    if extras:
        progress(f"Scene {scene['id']}: +{len(extras)} footage variasi")
        store.update(pid, lambda p: p["assets"]["scenes"].setdefault(scene["id"], {}).__setitem__("extras", extras))
    return len(extras)


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
