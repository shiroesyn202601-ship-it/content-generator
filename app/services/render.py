"""
Tahap 6: perakitan video akhir dengan FFmpeg.

scratch : per scene -> segmen video (durasi = timing VO) -> concat -> mix VO + musik (ducking
          otomatis saat narator bicara) + SFX -> burn-in subtitle ASS
clipper : clip.mp4 (audio asli) + musik opsional (ducking) + SFX -> burn-in subtitle ASS
"""
from pathlib import Path

from .. import config, store
from . import audio_lib, fonts, subtitles
from . import ffmpeg_utils as ff

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _scene_segment(d: Path, asset_rel: str | None, frames: int, out_rel: str, W: int, H: int) -> None:
    """Satu segmen scene dengan jumlah frame PERSIS (bukan -t detik) supaya visual tidak bergeser dari VO."""
    enc = ["-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out_rel]
    if not asset_rel:
        # Scene tanpa visual: latar gelap polos supaya render tidak gagal.
        ff.run(["-f", "lavfi", "-i", f"color=c=0x111318:s={W}x{H}:r={config.FPS}",
                "-vf", "format=yuv420p", *enc], cwd=d)
    elif asset_rel.lower().endswith(IMG_EXT):
        # Ken Burns pelan. Gambar dibaca SEKALI (tanpa -loop) lalu zoompan menghasilkan semua frame:
        # jauh lebih cepat daripada men-scale ulang gambar 4K di setiap frame. Skala 2x mengurangi jitter.
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='1+0.12*on/{max(frames, 1)}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":s={W}x{H}:fps={config.FPS},format=yuv420p")
        ff.run(["-i", asset_rel, "-vf", vf, *enc], cwd=d)
    else:
        vf = f"{ff.fit_filter(W, H, 'center')},fps={config.FPS},format=yuv420p"
        ff.run(["-stream_loop", "-1", "-i", asset_rel, "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", *enc],
               cwd=d)


def _scene_frames(scenes: list[dict], timings: dict, total: float) -> list[int]:
    """Batas frame kumulatif: scene i tampil dari awal VO-nya sampai awal scene berikutnya
    (jeda antar-scene ikut dihitung), scene terakhir sampai akhir video. Pembulatan per batas,
    bukan per durasi, sehingga error tidak menumpuk."""
    fps = config.FPS
    bounds = [0] + [round(float(timings[s["id"]]["start"]) * fps) for s in scenes[1:]] + [round(total * fps)]
    for k in range(1, len(bounds)):  # jaga urutan naik & minimal 1 frame
        bounds[k] = max(bounds[k], bounds[k - 1] + 1)
    return [bounds[k + 1] - bounds[k] for k in range(len(scenes))]


def _audio_graph(base: str, n_inputs_before: int, music: tuple | None, sfx: list, total: float):
    """Bangun filter_complex audio. base = label audio utama (VO / audio klip).
    music = (input_idx, volume) ; sfx = [(input_idx, at_sec, volume)]"""
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    parts, mix = [], []
    # apad: audio utama dipanjangkan dengan hening sampai akhir video, sehingga amix(duration=first)
    # tidak memotong musik/SFX lebih awal (panjang akhir dibatasi -t total).
    if music:
        idx, vol = music
        parts.append(f"{base}{fmt},apad,asplit=2[main][sc]")
        parts.append(f"[{idx}:a]{fmt},volume={vol},atrim=0:{total:.3f}[mus]")
        parts.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
        mix += ["[main]", "[duck]"]
    else:
        parts.append(f"{base}{fmt},apad[main]")
        mix.append("[main]")
    for k, (idx, at, vol) in enumerate(sfx):
        ms = int(at * 1000)
        parts.append(f"[{idx}:a]{fmt},volume={vol},adelay={ms}|{ms}[sfx{k}]")
        mix.append(f"[sfx{k}]")
    if len(mix) == 1:
        parts.append(f"{mix[0]}anull[aout]")
    else:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    return parts


def _extra_audio_inputs(p: dict, first_idx: int):
    args, music, sfx = [], None, []
    idx = first_idx
    m = p["audio"].get("music") or {}
    if m.get("enabled") and m.get("file"):
        args += ["-stream_loop", "-1", "-i", str(audio_lib.local_path(m["file"]))]
        music = (idx, float(m.get("volume", 0.12)))
        idx += 1
    for s in p["audio"].get("sfx", []):
        if s.get("item"):
            args += ["-i", str(audio_lib.local_path(s["item"]))]
            sfx.append((idx, float(s.get("at", 0)), float(s.get("volume", 0.6))))
            idx += 1
    return args, music, sfx


def run(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    W, H = config.RESOLUTIONS[p["format"]]
    (d / "render").mkdir(exist_ok=True)
    progress("Menulis subtitle...")
    subtitles.export(p)
    font_dir = "../../fonts"  # data/fonts relatif dari folder proyek
    if p["subtitles"].get("burn", True):
        progress("Memeriksa font subtitle…")
        warn = fonts.ensure(p["subtitles"].get("style", {}).get("font", ""))
        if warn:
            progress(warn)
            store.update(pid, lambda q: store.log(q, warn))
    sub_filter = f"ass=subs.ass:fontsdir={font_dir}" if p["subtitles"].get("burn", True) else "null"

    if p["mode"] == "scratch":
        timings = {t["scene_id"]: t for t in p["audio"]["scene_timings"]}
        total = float(p["audio"]["duration"]) + 0.6
        concat = []
        scenes = p["script"]["scenes"]
        missing = [s["id"] for s in scenes if s["id"] not in timings]
        if missing:
            raise RuntimeError(f"Timing VO tidak ada untuk scene {', '.join(missing)} — render ulang voice over.")
        frames = _scene_frames(scenes, timings, total)
        for i, s in enumerate(scenes):
            sel = (p["assets"]["scenes"].get(s["id"]) or {}).get("selected") or {}
            asset = sel.get("path") if sel.get("path") and (d / sel["path"]).is_file() else None
            out = f"render/seg_{i:03d}.mp4"
            progress(f"Scene {i + 1}/{len(scenes)} ({frames[i] / config.FPS:.1f}s)"
                     + ("" if asset else " — tanpa visual, memakai latar polos"))
            _scene_segment(d, asset, frames[i], out, W, H)
            concat.append(f"file 'seg_{i:03d}.mp4'")
        (d / "render" / "concat.txt").write_text("\n".join(concat), encoding="utf-8")
        progress("Menggabungkan scene...")
        ff.run(["-f", "concat", "-safe", "0", "-i", "render/concat.txt", "-c", "copy", "render/visual.mp4"], cwd=d)
        extra, music, sfx = _extra_audio_inputs(p, 2)
        graph = [f"[0:v]{sub_filter}[vout]"] + _audio_graph("[1:a]", 2, music, sfx, total)
        progress("Mixing audio + burn-in subtitle...")
        ff.run(["-i", "render/visual.mp4", "-i", p["audio"]["vo_path"], *extra,
                "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart",
                "render/final.mp4"], cwd=d)
    else:
        clip = p["assets"]["clip_path"]
        total = ff.duration(d / clip)
        extra, music, sfx = _extra_audio_inputs(p, 1)
        if ff.has_audio(d / clip):
            base_graph, base = [], "[0:a]"
        else:
            base_graph, base = [f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[silent]"], "[silent]"
        graph = [f"[0:v]{sub_filter}[vout]"] + base_graph + _audio_graph(base, 1, music, sfx, total)
        progress("Mixing audio + burn-in subtitle...")
        ff.run(["-i", clip, *extra, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart",
                "render/final.mp4"], cwd=d)

    drive_id = None
    if config.google_enabled():
        progress("Upload hasil render ke Google Drive...")
        from . import sync
        drive_id = sync.upload_artifact(p, "render/final.mp4", f"{p['id']} - final.mp4")

    def apply(p):
        p["render"].update({"output_path": "render/final.mp4", "duration": round(total, 2),
                            "drive_file_id": drive_id})
        store.touch_stage(p, "render")
        store.log(p, f"Render selesai ({total:.1f}s)")
    store.update(pid, apply)
    return {"path": "render/final.mp4"}
