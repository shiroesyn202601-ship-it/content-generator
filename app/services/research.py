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


def generate_ideas(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    keyword = (params.get("keyword") or p["research"].get("keyword") or p["title"]).strip()
    progress(f"Riset: mencari video populer di YouTube untuk '{keyword}'…")
    competitors = youtube_search(keyword)
    if competitors and params.get("trends", True):
        competitors = enrich_trends(competitors, progress=progress)
    progress("Riset: mengambil saran pencarian YouTube (yang sedang dicari orang)…")
    suggestions = autocomplete(keyword, p["language"])
    progress(f"Riset selesai: {len(competitors)} video pembanding, {len(suggestions)} saran pencarian. "
             "Menyiapkan bahan untuk AI…" if competitors else
             "Riset YouTube tidak menemukan hasil — AI menyusun ide tanpa pembanding…")
    sys_, usr = prompts.ideas(keyword, p["format"], p["language"], competitors, suggestions)
    res = llm.chat_json(sys_, usr, params.get("provider"))
    progress(f"Menyimpan {len(res.get('ideas', []))} ide ke proyek…")

    def apply(p):
        p["research"].update({"keyword": keyword, "competitors": competitors, "suggestions": suggestions,
                              "insight": res.get("insight", ""), "ideas": res.get("ideas", []),
                              "selected": None})
        store.touch_stage(p, "research")
        store.log(p, f"AI: {len(res.get('ideas', []))} ide untuk '{keyword}'")
    store.update(pid, apply)
    return {"count": len(res.get("ideas", []))}


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
           "license": info.get("license")}

    def apply(p):
        p["research"]["source"] = src
        if p["title"] in ("", "Proyek baru"):
            p["title"] = src["title"] or p["title"]
        store.touch_stage(p, "research")
        store.log(p, f"Sumber diunduh: {src['title']}")
    store.update(pid, apply)
    return src
