"""Pembungkus FFmpeg. Semua perintah dijalankan dengan cwd = folder proyek dan path RELATIF,
supaya filter `ass=` tidak bermasalah dengan escaping path Windows (C\\:...)."""
import json
import subprocess
from pathlib import Path

from .. import config


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None) -> None:
    cmd = [config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise FFmpegError("ffmpeg tidak ditemukan. Install ffmpeg dan pastikan ada di PATH (atau set FFMPEG_BIN).")
    if r.returncode != 0:
        raise FFmpegError(f"ffmpeg gagal:\n{' '.join(cmd)}\n{r.stderr[-2000:]}")


def probe(path: Path) -> dict:
    cmd = [config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise FFmpegError(f"ffprobe gagal untuk {path}: {r.stderr[-500:]}")
    return json.loads(r.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"].get("duration") or 0)


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def to_wav(src: Path, dst: Path, sr: int = 24000) -> None:
    run(["-i", src, "-ac", "1", "-ar", sr, "-sample_fmt", "s16", dst])


def fit_filter(w: int, h: int, mode: str = "center") -> str:
    """Filter video agar sumber apa pun pas di resolusi target.
    center = crop penuh (isi layar) | blur = latar blur + video utuh di tengah | none = letterbox hitam."""
    if mode == "blur":
        return (f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"boxblur=20:2[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if mode == "none":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
