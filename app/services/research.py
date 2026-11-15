"""Tahap 1: Ide & riset (scratch) / ambil video sumber (clipper)."""
import shutil
from pathlib import Path

from .. import config, store
from . import llm, prompts


def youtube_search(keyword: str, n: int = 12) -> list[dict]:
    """Riset kompetitor gratis tanpa API key: metadata hasil pencarian YouTube via yt-dlp."""
    import yt_dlp

    opts = {"quiet": True, "skip_download": True, "extract_flat": True, "noprogress": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{keyword}", download=False)
    except Exception:
        return []
    out = []
    for e in info.get("entries") or []:
        out.append({"title": e.get("title"), "views": e.get("view_count"), "channel": e.get("channel"),
                    "duration": e.get("duration"), "url": e.get("url") or f"https://youtu.be/{e.get('id')}"})
    return out


def _video_meta(url: str) -> dict:
    """Detail satu video (tanggal unggah, like) — ±10 dtk karena butuh pemecah JS YouTube (Deno)."""
    import yt_dlp

    opts = {"quiet": True, "skip_download": True, "noplaylist": True, "noprogress": True}
    deno = config.find_deno()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        i = ydl.extract_info(url, download=False)
    return {"upload_date": i.get("upload_date"), "views": i.get("view_count"), "likes": i.get("like_count"),
            "duration": i.get("duration")}


def enrich_trends(videos: list[dict], top: int = 6, progress=lambda m: None) -> list[dict]:
    """Tambahkan umur video & views/hari untuk video teratas (paralel). Views/hari menunjukkan topik yang
    sedang NAIK, bukan sekadar video lama yang menumpuk views bertahun-tahun."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date

    ranked = sorted(videos, key=lambda v: -(v.get("views") or 0))[:top]
    progress(f"Riset tren: mengambil tanggal unggah {len(ranked)} video teratas (±10-20 dtk)…")

    def one(v):
        try:
            return v, _video_meta(v["url"])
        except Exception:  # noqa: BLE001 — satu video gagal tidak menggagalkan riset
            return v, None
    with ThreadPoolExecutor(max_workers=3) as ex:
        for v, meta in ex.map(one, ranked):
            if not meta or not meta.get("upload_date"):
                continue
            up = date(int(meta["upload_date"][:4]), int(meta["upload_date"][4:6]), int(meta["upload_date"][6:]))
            age = max((date.today() - up).days, 1)
            v.update(upload_date=up.isoformat(), age_days=age, likes=meta.get("likes"),
                     views=meta.get("views") or v.get("views"))
            v["views_per_day"] = round((v["views"] or 0) / age, 1)
    return videos


def autocomplete(keyword: str, lang: str = "id") -> list[str]:
    """Saran pencarian YouTube (yang sedang dicari orang) — endpoint publik tak resmi, boleh gagal."""
    import httpx
    try:
        r = httpx.get("https://suggestqueries.google.com/complete/search",
                      params={"client": "firefox", "ds": "yt", "q": keyword, "hl": lang}, timeout=10)
        data = r.json()
        return [s for s in data[1] if isinstance(s, str) and s.lower() != keyword.lower()][:10]
    except Exception:  # noqa: BLE001
        return []


def default_duration(p: dict) -> int:
    return int(p["research"].get("duration") or (50 if p["format"] == "short" else 480))


def _clean_idea(it: dict) -> dict | None:
    if not isinstance(it, dict):
        return None
    title = str(it.get("judul") or it.get("title") or "").strip()
    if not title:
        return None
    desc = str(it.get("deskripsi") or it.get("description") or it.get("angle") or "").strip()
    hook = str(it.get("hook") or "").strip().strip('"*')
    return {"title": title, "description": desc, "hook": hook or title,
            "keywords": [str(k) for k in (it.get("keywords") or []) if k][:10]}


def generate_ideas(pid: str, params: dict, progress) -> dict:
    """Generator ide gaya tab "Generate Narasi": template prompt yang bisa diedit + {numberOfIdeas},
    {videoDuration}, {viralTopic}. append=True → "Tambah ide baru" (judul lama dikecualikan)."""
    p = store.load(pid)
    r = p["research"]
    keyword = (params.get("keyword") or r.get("keyword") or p["title"]).strip()
    count = max(1, min(50, int(params.get("count") or r.get("idea_count") or 20)))
    duration = int(params.get("duration") or default_duration(p))
    template = params.get("template") if params.get("template") is not None else r.get("idea_template")
    append = bool(params.get("append"))
    trends = params.get("trends", r.get("use_trends", True))
    old = list(r.get("ideas") or []) if append else []
    competitors, suggestions = (r.get("competitors") or [], r.get("suggestions") or []) if append else ([], [])
    if trends and not append:
        progress(f"Riset: mencari video populer di YouTube untuk '{keyword[:60]}'…")
        competitors = youtube_search(keyword[:100])
        if competitors:
            competitors = enrich_trends(competitors, progress=progress)
        progress("Riset: mengambil saran pencarian YouTube (yang sedang dicari orang)…")
        suggestions = autocomplete(keyword[:100], p["language"])
    progress(f"AI membuat {count} ide{' baru' if append else ''} untuk video {duration} detik…")
    sys_, usr = prompts.ideas_from_template(template, keyword, count, duration,
                                            [i.get("title", "") for i in old], competitors if trends else [],
                                            suggestions if trends else [])
    res = llm.chat_json(sys_, usr, params.get("provider"), temperature=0.9)
    raw = res.get("ideas") if isinstance(res, dict) else res
    new = [x for x in (_clean_idea(i) for i in (raw or [])) if x]
    if not new:
        raise RuntimeError("AI tidak mengembalikan ide. Coba lagi atau sederhanakan template prompt.")
    progress(f"Menyimpan {len(new)} ide ke proyek…")

    def apply(q):
        rr = q["research"]
        rr.update({"keyword": keyword, "idea_count": count, "duration": duration, "use_trends": bool(trends)})
        if params.get("template") is not None:
            rr["idea_template"] = template
        if append:
            rr["ideas"] = (rr.get("ideas") or []) + new
        else:
            rr.update({"ideas": new, "selected": None, "competitors": competitors, "suggestions": suggestions,
                       "insight": res.get("insight", "") if isinstance(res, dict) else ""})
        store.touch_stage(q, "research")
        store.log(q, f"AI: {len(new)} ide{' tambahan' if append else ''} untuk '{keyword[:60]}'")
    store.update(pid, apply)
    return {"count": len(new)}


def download_source(pid: str, params: dict, progress) -> dict:
    """Clipper: unduh video YouTube dengan yt-dlp (maks 1080p, mp4)."""
    import yt_dlp

    url = params["url"].strip()
    out_dir = store.project_dir(pid) / "source"
    # Kosongkan dulu: kalau tidak, yt-dlp melihat source.mp4 lama & melewati unduhan (video tertukar).
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(exist_ok=True)

    def hook(d):
        if d.get("status") == "downloading":
            progress(f"Mengunduh {d.get('_percent_str', '').strip()} ({d.get('_eta_str', '')})")
        elif d.get("status") == "finished":
            progress("Menggabungkan audio+video...")

    opts = {
        # Utamakan H.264 (avc1): AV1/VP9 sangat berat di-decode CPU laptop saat potong & render.
        "format": ("bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
                   "b[height<=1080][ext=mp4]/b"),
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "noplaylist": True,
        # koneksi putus / fragmen gagal → ulangi, bukan langsung gagal; fragmen diunduh paralel
        "retries": 10, "fragment_retries": 10, "concurrent_fragment_downloads": 4, "socket_timeout": 30,
    }
    if config.FFMPEG != "ffmpeg":
        opts["ffmpeg_location"] = str(Path(config.FFMPEG).parent)
    deno = config.find_deno()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        hint = ("" if deno else " — Deno belum terpasang (wajib untuk YouTube sejak Nov 2025): jalankan "
                "`winget install DenoLand.Deno`, lalu restart aplikasi.")
        raise RuntimeError(f"Unduh gagal: {e}{hint} Coba juga perbarui: pip install -U \"yt-dlp[default]\"") from e
    # Ambil path final dari yt-dlp (bukan glob, yang bisa menangkap sisa .part / .f137.mp4).
    req = (info.get("requested_downloads") or [{}])[0]
    final = Path(req.get("filepath") or "")
    if not final.is_file():
        files = [f for f in out_dir.glob("source.*") if f.suffix in (".mp4", ".mkv", ".webm")]
        if not files:
            raise RuntimeError("Download gagal: file tidak ditemukan.")
        final = files[0]
    src = {"url": url, "title": info.get("title"), "channel": info.get("channel"),
           "duration": info.get("duration"), "path": f"source/{final.name}",
           "license": info.get("license"), "description": (info.get("description") or "")[:2000],
           # sinyal penonton gratis: grafik "paling sering diputar ulang" & bab video
           "heatmap": [{"s": round(h.get("start_time", 0), 1), "e": round(h.get("end_time", 0), 1),
                        "v": round(h.get("value", 0), 3)} for h in info.get("heatmap") or [] if isinstance(h, dict)],
           "chapters": [{"s": round(c.get("start_time", 0), 1), "title": c.get("title", "")}
                        for c in info.get("chapters") or [] if isinstance(c, dict)]}

    def apply(p):
        p["research"]["source"] = src
        if p["title"] in ("", "Proyek baru"):
            p["title"] = src["title"] or p["title"]
        store.touch_stage(p, "research")
        store.log(p, f"Sumber diunduh: {src['title']}")
    store.update(pid, apply)
    return src
