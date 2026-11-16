"""Pembungkus FFmpeg. Semua perintah dijalankan dengan cwd = folder proyek dan path RELATIF,
supaya filter `ass=` tidak bermasalah dengan escaping path Windows (C\\:...)."""
import json
import subprocess
from pathlib import Path

from .. import config


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None) -> None:
    """Jalankan ffmpeg. Bila job yang memanggil diminta batal, proses ffmpeg dihentikan (render panjang
    bisa dibatalkan dari UI)."""
    from .. import jobs
    cmd = [config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *map(str, args)]
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise FFmpegError("ffmpeg tidak ditemukan. Install ffmpeg dan pastikan ada di PATH (atau set FFMPEG_BIN).")
    while True:
        try:
            _, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if jobs.cancelled():
                proc.kill()
                proc.communicate()
                raise jobs.JobCancelled()
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg gagal:\n{' '.join(cmd)}\n{(err or '')[-2000:]}")


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


_encoder: str | None = None
HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")  # NVIDIA, Intel, AMD


def _encoder_works(enc: str) -> bool:
    """Encoder yang terdaftar belum tentu jalan (driver/GPU) → uji encode 3 frame."""
    pix = "nv12" if enc == "h264_qsv" else "yuv420p"
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=s=320x240:r=30:d=0.2", "-frames:v", "3", "-pix_fmt", pix,
                        "-c:v", enc, "-f", "null", "-"], capture_output=True, timeout=30)
    return r.returncode == 0


def video_encoder() -> str:
    """VIDEO_ENCODER di .env: auto (default) | libx264 | h264_nvenc | h264_qsv | h264_amf."""
    global _encoder
    if _encoder is None:
        want = (config.VIDEO_ENCODER or "auto").lower()
        if want != "auto":
            _encoder = want
        else:
            _encoder = "libx264"
            for enc in HW_ENCODERS:
                try:
                    if _encoder_works(enc):
                        _encoder = enc
                        break
                except (OSError, subprocess.TimeoutExpired):
                    continue
    return _encoder


def video_codec_args(enc: str | None = None, intermediate: bool = False) -> list[str]:
    """Pengaturan encode final sesuai rekomendasi upload YouTube: H.264 High, GOP = setengah frame rate,
    2 B-frame, 4:2:0, frame rate konstan. intermediate=True: file antara (potongan klip, klip bernilai tambah)
    yang masih akan di-encode ulang → kualitas sedikit lebih tinggi & libx264 memakai preset cepat."""
    enc = enc or video_encoder()
    gop = ["-g", str(config.FPS // 2), "-bf", "2", "-r", str(config.FPS)]
    q = 3 if intermediate else 0
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", str(21 - q), "-b:v", "0", "-profile:v", "high",
                "-pix_fmt", "yuv420p", *gop]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-global_quality", str(21 - q), "-preset", "medium", "-profile:v", "high",
                "-pix_fmt", "nv12", *gop]
    if enc == "h264_amf":
        return ["-c:v", enc, "-quality", "quality", "-rc", "cqp", "-qp_i", str(20 - q), "-qp_p", str(22 - q),
                "-profile:v", "high", "-pix_fmt", "yuv420p", *gop]
    return ["-c:v", "libx264", "-preset", "veryfast" if intermediate else "medium", "-crf", str(20 - q),
            "-profile:v", "high", "-pix_fmt", "yuv420p", *gop]


def run_encode(pre: list, post: list, cwd: Path | None = None, intermediate: bool = False) -> str:
    """Jalankan encode dengan encoder terbaik (GPU bila ada); bila encoder hardware gagal, ulangi dengan libx264.
    pre = argumen input+filter, post = argumen audio+output. Mengembalikan nama encoder yang dipakai."""
    global _encoder
    enc = video_encoder()
    try:
        run([*pre, *video_codec_args(enc, intermediate), *post], cwd=cwd)
        return enc
    except FFmpegError:  # (JobCancelled bukan FFmpegError → pembatalan tidak memicu ulang dengan libx264)
        if enc == "libx264":
            raise
        _encoder = "libx264"  # jangan coba hardware lagi di sesi ini
        run([*pre, *video_codec_args("libx264", intermediate), *post], cwd=cwd)
        return "libx264"


AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]


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
