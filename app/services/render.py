"""
Tahap 6: perakitan video akhir dengan FFmpeg.

scratch : per scene -> segmen video (durasi = timing VO) -> concat -> mix VO + musik (ducking
          otomatis saat narator bicara) + SFX -> burn-in subtitle ASS
clipper : clip.mp4 (audio asli) + musik opsional (ducking) + SFX -> burn-in subtitle ASS
"""
from pathlib import Path

from .. import config, store
from . import audio_lib, brand, fonts, subtitles, usage
from . import ffmpeg_utils as ff

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _resolve_fit(d: Path, asset_rel: str, fit: str, W: int, H: int) -> str:
    """auto: gambar yang orientasinya beda dengan video (mis. foto landscape di Shorts) → 'blur' supaya
    objeknya utuh; video → 'center' (isi penuh layar)."""
    if fit in ("center", "blur"):
        return fit
    if asset_rel.lower().endswith(IMG_EXT):
        try:
            s = next(x for x in ff.probe(d / asset_rel)["streams"] if x.get("codec_type") == "video")
            img_portrait = int(s["height"]) >= int(s["width"])
            return "center" if img_portrait == (H >= W) else "blur"
        except (StopIteration, KeyError, ValueError, ff.FFmpegError):
            return "center"
    return "center"


PUNCH = 1.12  # zoom "punch-in" untuk shot bernomor ganjil (jump cut ala Shorts)


def _scene_segment(d: Path, asset_rel: str | None, frames: int, out_rel: str, W: int, H: int,
                   fit: str = "auto", shot: int = 0, offset: float = 0.0) -> None:
    """Satu shot dengan jumlah frame PERSIS (bukan -t detik) supaya visual tidak bergeser dari VO.
    shot  = nomor shot di dalam scene (variasi: punch-in / arah gerak bergantian),
    offset = detik ke berapa video aset dimulai (shot berikutnya melanjutkan klip, bukan mengulang)."""
    enc = ["-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out_rel]
    if not asset_rel:
        # Scene tanpa visual: latar gelap polos supaya render tidak gagal.
        ff.run(["-f", "lavfi", "-i", f"color=c=0x111318:s={W}x{H}:r={config.FPS}",
                "-vf", "format=yuv420p", *enc], cwd=d)
        return
    fit = _resolve_fit(d, asset_rel, fit, W, H)
    n = max(frames, 1)
    if asset_rel.lower().endswith(IMG_EXT):
        # Ken Burns. Gambar dibaca SEKALI (tanpa -loop) lalu zoompan menghasilkan semua frame:
        # jauh lebih cepat daripada men-scale ulang gambar 4K di setiap frame. Skala 2x mengurangi jitter.
        # Gerak bergantian per shot: zoom-in tengah → geser kiri→kanan → zoom-out → geser kanan→kiri.
        mode = shot % 4
        if mode == 0:
            z, x = f"1+0.12*on/{n}", "iw/2-(iw/zoom/2)"
        elif mode == 1:
            z, x = "1.15", f"(iw-iw/zoom)*on/{n}"
        elif mode == 2:
            z, x = f"1.14-0.12*on/{n}", "iw/2-(iw/zoom/2)"
        else:
            z, x = "1.15", f"(iw-iw/zoom)*(1-on/{n})"
        zoom = (f"zoompan=z='{z}':d={frames}:x='{x}':y='ih/2-(ih/zoom/2)'"
                f":s={W}x{H}:fps={config.FPS},setsar=1,format=yuv420p")
        ff.run(["-i", asset_rel, "-filter_complex", f"[0:v]{ff.fit_filter(W * 2, H * 2, fit)},{zoom}[v]",
                "-map", "[v]", *enc], cwd=d)
    else:
        punch = (f",scale={int(W * PUNCH) // 2 * 2}:{int(H * PUNCH) // 2 * 2},crop={W}:{H}"
                 if shot % 2 == 1 else "")
        vf = f"{ff.fit_filter(W, H, fit)}{punch},fps={config.FPS},format=yuv420p"
        if offset > 0.05:  # klip pendek: lanjutkan dari awal lagi (modulo durasi), bukan seek melewati akhir
            try:
                offset %= max(ff.duration(d / asset_rel) - 0.3, 0.5)
            except (ff.FFmpegError, ValueError, KeyError):
                offset = 0.0
        seek = ["-ss", f"{offset:.3f}"] if offset > 0.05 else []
        ff.run(["-stream_loop", "-1", *seek, "-i", asset_rel, "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]",
                *enc], cwd=d)


TRANS_FRAMES = 9  # 0.3 dtk
TRANSITIONS = {"fade": "fade", "geser": "slideleft", "zoom": "zoomin", "hitam": "fadeblack", "lingkaran": "circleopen"}


def _join_with_transitions(d: Path, shots: list[dict], extras: list[int], kind: str) -> None:
    """Gabungkan shot: xfade di pergantian scene (extras[j] > 0), sambung biasa di tempat lain.
    Offset xfade = jumlah durasi persis shot sebelumnya → visual tetap sinkron dengan VO."""
    fps = config.FPS
    inputs, parts = [], []
    for j in range(len(shots)):
        inputs += ["-i", f"render/seg_{j:03d}.mp4"]
        # xfade mensyaratkan timebase & frame rate identik di kedua input
        parts.append(f"[{j}:v]fps={fps},setpts=PTS-STARTPTS,settb=1/{fps},setsar=1[s{j}]")
    acc, acc_frames = "[s0]", shots[0]["frames"]
    for j in range(1, len(shots)):
        out = f"[v{j}]"
        tf = extras[j - 1]
        if tf:
            parts.append(f"{acc}[s{j}]xfade=transition={kind}:duration={tf / fps:.4f}:offset={acc_frames / fps:.4f}{out}")
        else:
            # concat mengubah timebase → kembalikan ke 1/fps supaya xfade berikutnya mau menerima
            parts.append(f"{acc}[s{j}]concat=n=2:v=1:a=0,settb=1/{fps}{out}")
        acc, acc_frames = out, acc_frames + shots[j]["frames"]
    parts.append(f"{acc}format=yuv420p,settb=1/{fps},fps={fps}[vout]")
    ff.run([*inputs, "-filter_complex", ";".join(parts), "-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "18", "render/visual.mp4"], cwd=d)


def _plan_shots(scenes: list[dict], timings: dict, sentences: list[dict], total: float,
                max_shot: float, dynamic: bool) -> list[dict]:
    """Rencana shot. Statis: 1 shot per scene. Dinamis: potong di awal tiap kalimat VO, lalu shot yang
    masih > max_shot dibagi rata. Batas frame dibulatkan sekali (kumulatif) → tidak bergeser dari VO."""
    fps = config.FPS
    starts = [0.0] + [float(timings[s["id"]]["start"]) for s in scenes[1:]] + [total]
    cuts = []  # (detik, scene_idx)
    for i, s in enumerate(scenes):
        a, b = starts[i], starts[i + 1]
        pts = [a]
        if dynamic:
            pts += [float(x["start"]) for x in sentences
                    if x.get("scene_id") == s["id"] and a + 0.8 < float(x["start"]) < b - 0.8]
        pts = sorted(set(pts)) + [b]
        for k in range(len(pts) - 1):
            seg_a, seg_b = pts[k], pts[k + 1]
            parts = max(1, int((seg_b - seg_a) / max_shot + 0.999)) if dynamic else 1
            for j in range(parts):
                cuts.append((seg_a + (seg_b - seg_a) * j / parts, i))
    bounds = [round(t * fps) for t, _ in cuts] + [round(total * fps)]
    bounds[0] = 0
    for k in range(1, len(bounds)):
        bounds[k] = max(bounds[k], bounds[k - 1] + 1)
    shots, prev_scene, k_in_scene = [], None, 0
    for idx, (t, scene_idx) in enumerate(cuts):
        k_in_scene = k_in_scene + 1 if scene_idx == prev_scene else 0
        prev_scene = scene_idx
        shots.append({"scene": scene_idx, "frames": bounds[idx + 1] - bounds[idx], "shot": k_in_scene,
                      "offset": (bounds[idx] / fps) - starts[scene_idx]})
    return shots


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
        # Musik: fade-in 1 dtk & fade-out 1.5 dtk di akhir video (tidak terpotong mendadak).
        fo = max(0.0, total - 1.5)
        parts.append(f"[{idx}:a]{fmt},volume={vol},atrim=0:{total:.3f},afade=t=in:d=1,"
                     f"afade=t=out:st={fo:.3f}:d=1.5[mus]")
        parts.append("[mus][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
        mix += ["[main]", "[duck]"]
    else:
        parts.append(f"{base}{fmt},apad[main]")
        mix.append("[main]")
    for k, (idx, at, vol) in enumerate(sfx):
        ms = int(at * 1000)
        parts.append(f"[{idx}:a]{fmt},volume={vol},adelay={ms}|{ms}[sfx{k}]")
        mix.append(f"[sfx{k}]")
    # Loudness standar platform: -14 LUFS integrated, true peak -1.5 dB (loudnorm bekerja di 192 kHz,
    # jadi dikembalikan ke 48 kHz).
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if len(mix) == 1:
        parts.append(f"{mix[0]}{norm}[aout]")
    else:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,{norm}[aout]")
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


@usage.stepped("Render video")
def run(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    W, H = config.RESOLUTIONS[p["format"]]
    (d / "render").mkdir(exist_ok=True)
    progress("Menulis subtitle...")
    subtitles.export(p)
    font_dir = store.fonts_rel(d)  # data/fonts relatif dari folder proyek
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
        # Gaya potongan: "dinamis" (default Shorts) = shot baru tiap kalimat VO, maks 4 dtk per shot,
        # punch-in bergantian → ritme cepat yang menahan penonton; "statis" = 1 shot per scene.
        cut = p["render"].get("cut_style") or ("dinamis" if p["format"] == "short" else "statis")
        shots = _plan_shots(scenes, timings, p["audio"].get("sentences") or [], total,
                            4.0 if p["format"] == "short" else 7.0, cut == "dinamis")
        # Transisi antar-scene (ala CapCut). Shot terakhir sebelum pergantian scene dirender lebih panjang
        # sebanyak durasi transisi, lalu xfade tumpang-tindih → total durasi tetap = durasi VO.
        trans = p["render"].get("transition") or ("fade" if cut == "dinamis" else "none")
        tf = TRANS_FRAMES if trans in TRANSITIONS else 0
        extras = [tf if tf and j + 1 < len(shots) and shots[j + 1]["scene"] != sh["scene"]
                  and min(sh["frames"], shots[j + 1]["frames"]) > tf * 2 else 0 for j, sh in enumerate(shots)]
        for j, sh in enumerate(shots):
            s = scenes[sh["scene"]]
            slot = p["assets"]["scenes"].get(s["id"]) or {}
            sel = slot.get("selected") or {}
            asset = sel.get("path") if sel.get("path") and (d / sel["path"]).is_file() else None
            out = f"render/seg_{j:03d}.mp4"
            progress(f"Shot {j + 1}/{len(shots)} · scene {sh['scene'] + 1} ({sh['frames'] / config.FPS:.1f}s)"
                     + ("" if asset else " — tanpa visual, memakai latar polos"))
            _scene_segment(d, asset, sh["frames"] + extras[j], out, W, H, slot.get("fit") or "auto", sh["shot"],
                           sh["offset"])
            concat.append(f"file 'seg_{j:03d}.mp4'")
        progress("Menggabungkan scene" + (f" + transisi {trans}…" if any(extras) else "…"))
        if any(extras):
            _join_with_transitions(d, shots, extras, TRANSITIONS[trans])
        else:
            (d / "render" / "concat.txt").write_text("\n".join(concat), encoding="utf-8")
            ff.run(["-f", "concat", "-safe", "0", "-i", "render/concat.txt", "-c", "copy", "render/visual.mp4"], cwd=d)
        extra, music, sfx = _extra_audio_inputs(p, 2)
        graph = [f"[0:v]{sub_filter}[vout]"] + _audio_graph("[1:a]", 2, music, sfx, total)
        progress(f"Mixing audio + burn-in subtitle ({ff.video_encoder()})…")
        enc = ff.run_encode(["-i", "render/visual.mp4", "-i", p["audio"]["vo_path"], *extra,
                             "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]"],
                            [*ff.AUDIO_ARGS, "-t", f"{total:.3f}", "-movflags", "+faststart", "render/final.mp4"], cwd=d)
    else:
        from . import enhance
        clip = enhance.video_path(p)  # versi nilai tambah (komentar, B-roll, dll.) bila ada & masih cocok
        total = ff.duration(d / clip)
        extra, music, sfx = _extra_audio_inputs(p, 1)
        if ff.has_audio(d / clip):
            base_graph, base = [], "[0:a]"
        else:
            base_graph, base = [f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[silent]"], "[silent]"
        # Branding global (logo + overlay, data/brand.json) — dipasang di bawah subtitle.
        n_in = 1 + extra.count("-i")
        b_args, b_lines, vlabel = brand.graph(n_in, "[0:v]", W, H, total) if p["render"].get("brand", True) else ([], [], "[0:v]")
        if b_lines:
            progress("Memasang logo/overlay…")
        graph = b_lines + [f"{vlabel}{sub_filter}[vout]"] + base_graph + _audio_graph(base, 1, music, sfx, total)
        progress(f"Mixing audio + burn-in subtitle ({ff.video_encoder()})…")
        enc = ff.run_encode(["-i", clip, *extra, *b_args, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]"],
                            [*ff.AUDIO_ARGS, "-t", f"{total:.3f}", "-movflags", "+faststart", "render/final.mp4"], cwd=d)

    usage.ai("Render", "FFmpeg", enc, None, "encoder video")
    drive_id = None
    drive_bg = config.google_enabled() and params.get("drive_background")
    if config.google_enabled() and not drive_bg:
        progress("Upload hasil render ke Google Drive...")
        from . import sync
        drive_id = sync.upload_artifact(p, "render/final.mp4", f"{p['id']} - final.mp4")

    def apply(p):
        p["render"].update({"output_path": "render/final.mp4", "duration": round(total, 2),
                            "drive_file_id": drive_id})
        store.touch_stage(p, "render")
        store.log(p, f"Render selesai ({total:.1f}s, encoder {enc})")
    store.update(pid, apply)
    if drive_bg:  # produksi massal: upload Drive di latar belakang supaya klip berikutnya langsung dikerjakan
        import threading
        threading.Thread(target=_drive_upload_bg, args=(pid,), daemon=True).start()
    return {"path": "render/final.mp4"}


def _drive_upload_bg(pid: str) -> None:
    from . import sync
    try:
        fid = sync.upload_artifact(store.load(pid), "render/final.mp4", f"{pid} - final.mp4")
        store.update(pid, lambda q: q["render"].update({"drive_file_id": fid}))
    except Exception as e:  # noqa: BLE001 — gagal upload tidak merusak hasil render
        store.update(pid, lambda q, e=e: store.log(q, f"Upload Drive (latar belakang) gagal: {e}"))
