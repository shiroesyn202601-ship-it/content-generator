"""Tahap 1: Ide & riset (scratch) / ambil video sumber (clipper)."""
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


def generate_ideas(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    keyword = (params.get("keyword") or p["research"].get("keyword") or p["title"]).strip()
    progress(f"Riset: mencari video populer di YouTube untuk '{keyword}'…")
    competitors = youtube_search(keyword)
    progress(f"Riset selesai: {len(competitors)} video pembanding. Menyiapkan bahan untuk AI…"
             if competitors else "Riset YouTube tidak menemukan hasil — AI menyusun ide tanpa pembanding…")
    sys_, usr = prompts.ideas(keyword, p["format"], p["language"], competitors)
    res = llm.chat_json(sys_, usr, params.get("provider"))
    progress(f"Menyimpan {len(res.get('ideas', []))} ide ke proyek…")

    def apply(p):
        p["research"].update({"keyword": keyword, "competitors": competitors,
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
    out_dir.mkdir(exist_ok=True)

    def hook(d):
        if d.get("status") == "downloading":
            progress(f"Mengunduh {d.get('_percent_str', '').strip()} ({d.get('_eta_str', '')})")
        elif d.get("status") == "finished":
            progress("Menggabungkan audio+video...")

    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "noplaylist": True,
    }
    if config.FFMPEG != "ffmpeg":
        opts["ffmpeg_location"] = str(Path(config.FFMPEG).parent)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(out_dir.glob("source.*"))
    if not files:
        raise RuntimeError("Download gagal: file tidak ditemukan.")
    src = {"url": url, "title": info.get("title"), "channel": info.get("channel"),
           "duration": info.get("duration"), "path": f"source/{files[0].name}",
           "license": info.get("license")}

    def apply(p):
        p["research"]["source"] = src
        if p["title"] in ("", "Proyek baru"):
            p["title"] = src["title"] or p["title"]
        store.touch_stage(p, "research")
        store.log(p, f"Sumber diunduh: {src['title']}")
    store.update(pid, apply)
    return src
