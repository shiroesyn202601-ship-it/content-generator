"""Auto-reframe Clipper: bingkai 9:16 mengikuti wajah seperti "kamera" yang dioperasikan manusia.

Terinspirasi openshorts (MIT) & hotclip (ide saja), tetapi hanya memakai OpenCV 4.x yang sudah terbukti
jalan di laptop (Smart App Control memblokir DLL baru seperti OpenCV 5 / onnxruntime / torch):

1. Baca frame berurutan pada rentang klip.
2. Potongan scene: selisih rata-rata HSV antar-frame (algoritma ContentDetector PySceneDetect) yang
   dibandingkan dengan rata-rata beberapa frame sebelumnya (adaptif → gerak kamera biasa tidak dianggap potong).
3. Wajah: YuNet (cv2.FaceDetectorYN, model ONNX 230 KB, diunduh otomatis) ±6x per detik; cadangan Haar.
4. "Kamera" per shot:
   - semua wajah muat di bingkai 9:16 → bidik tengah kelompok (terkunci, tidak ikut goyang);
   - tidak muat → ikuti wajah terbesar, utamakan subjek yang sedang dibidik (tidak lompat-lompat);
   - zona mati 25% lebar bingkai, perpindahan baru dilakukan setelah 3 deteksi berturut-turut,
     kecepatan geser dibatasi (pelan bila dekat, cepat bila jauh);
   - wajah hilang sebentar → posisi ditahan; di potongan scene kamera langsung melompat (seperti editor).
5. Hasil = jalur posisi x per frame → file perintah `sendcmd` untuk filter crop FFmpeg.
"""
from pathlib import Path

from .. import config

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
DETECT_FPS = 6.0        # deteksi wajah per detik
CUT_MIN = 18.0          # selisih HSV minimum untuk dianggap potongan scene
CUT_RATIO = 3.0         # ... dan harus ≥ 3x rata-rata selisih frame-frame sebelumnya
MIN_SHOT = 0.5          # detik; potongan lebih rapat dari ini diabaikan (kilat/flash)
DEAD_ZONE = 0.25        # bagian lebar bingkai: target bergeser di dalam zona ini → kamera diam
CONFIRM = 3             # jumlah deteksi berturut-turut sebelum kamera mau pindah
SLOW, FAST = 0.35, 1.6  # kecepatan geser (× lebar bingkai per detik)
HOLD_LOST = 1.5         # detik wajah hilang yang masih ditahan


def _model_path() -> Path | None:
    path = config.DATA_DIR / "models" / "face_detection_yunet_2023mar.onnx"
    if path.is_file() and path.stat().st_size > 100_000:
        return path
    try:
        import httpx
        path.parent.mkdir(parents=True, exist_ok=True)
        r = httpx.get(YUNET_URL, follow_redirects=True, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        return path
    except Exception:
        return None  # tanpa internet → cadangan Haar


class _Faces:
    """Detektor wajah: YuNet bila ada, kalau tidak Haar cascade. Mengembalikan [(x, y, w, h, skor)] dalam piksel."""

    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.yunet, self.haar = None, None
        mp = _model_path()
        if mp is not None and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(mp), "", (320, 320), 0.6, 0.3, 50)
            except Exception:
                self.yunet = None
        if self.yunet is None:
            self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
        self.engine = "yunet" if self.yunet is not None else "haar"

    def __call__(self, img) -> list[tuple]:
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, faces = self.yunet.detect(img)
            return [tuple(float(v) for v in f[:4]) + (float(f[14]),) for f in (faces if faces is not None else [])]
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        found = self.haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        if not len(found):
            found = self.haar.detectMultiScale(self.cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        return [(float(x), float(y), float(fw), float(fh), 0.7) for x, y, fw, fh in found]


def analyze(src: Path, start: float, end: float, progress=lambda m: None) -> dict:
    """Baca rentang klip: {fps, width, height, duration, cuts: [detik], samples: [(t, [(cx, w, skor)])]}.
    Posisi & lebar wajah dalam piksel sumber; waktu relatif terhadap awal klip."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError("Video sumber tidak bisa dibaca OpenCV.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if not 5 <= fps <= 240:
        fps = 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    det = _Faces()
    every = max(1, int(round(fps / DETECT_FPS)))
    dur = max(end - start, 0.1)
    scale = 640 / src_w if src_w > 640 else 1.0
    prev_hsv, recent, cuts, samples = None, [], [], []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 - start
        if t < -0.02:
            continue  # seek mendarat sedikit sebelum start
        if t >= dur:
            break
        tiny = cv2.resize(frame, (160, max(1, int(160 * src_h / src_w))), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(tiny, cv2.COLOR_BGR2HSV).astype(np.int16)
        if prev_hsv is not None:
            val = float(np.abs(hsv - prev_hsv).mean(axis=(0, 1)).mean())
            avg = sum(recent) / len(recent) if recent else 0.0
            if val >= CUT_MIN and val >= CUT_RATIO * (avg + 1.0) and (not cuts or t - cuts[-1] >= MIN_SHOT) and t >= MIN_SHOT:
                cuts.append(round(t, 3))
                recent = []
            else:
                recent = (recent + [val])[-8:]
        prev_hsv = hsv
        if i % every == 0:
            small = cv2.resize(frame, (int(src_w * scale), int(src_h * scale))) if scale != 1.0 else frame
            faces = [((x + fw / 2) / scale, fw / scale, sc) for x, _, fw, _, sc in det(small)]
            samples.append((round(max(t, 0.0), 3), faces))
            if len(samples) % 30 == 0:
                progress(f"Auto-reframe: menganalisis {min(100, t / dur * 100):.0f}% ({det.engine})…")
        i += 1
    cap.release()
    return {"fps": fps, "width": src_w, "height": src_h, "duration": dur, "cuts": cuts,
            "samples": samples, "engine": det.engine}


def _target(faces: list, cam: float | None, cw: float) -> float | None:
    """Titik bidik untuk satu sampel: tengah kelompok bila semua muat, kalau tidak wajah utama."""
    faces = [f for f in faces if f[2] >= 0.6]
    if not faces:
        return None
    big = max(f[1] for f in faces)
    faces = [f for f in faces if f[1] >= 0.45 * big]  # buang wajah kecil di latar belakang
    lo = min(f[0] - f[1] / 2 for f in faces)
    hi = max(f[0] + f[1] / 2 for f in faces)
    if hi - lo <= cw * 0.85:
        return (lo + hi) / 2
    main = max(faces, key=lambda f: f[1])
    if cam is not None:  # pertahankan subjek yang sedang dibidik bila ukurannya sebanding
        near = min(faces, key=lambda f: abs(f[0] - cam))
        if near[1] >= 0.7 * main[1]:
            return near[0]
    return main[0]


def plan(info: dict, W: int, H: int, out_fps: float = 30.0) -> dict | None:
    """Rencana kamera: {cw, xs: [x kiri crop per frame keluaran], shots, face_ratio}. None bila tak ada wajah."""
    src_w, src_h, dur = info["width"], info["height"], info["duration"]
    cw = min(src_w, int(round(src_h * W / H / 2)) * 2)
    samples = info["samples"]
    with_face = sum(1 for _, f in samples if f)
    if not samples or with_face == 0 or cw >= src_w:
        return None
    bounds = [0.0] + [c for c in info["cuts"] if 0 < c < dur] + [dur]
    shots = list(zip(bounds[:-1], bounds[1:]))
    # target per sampel (mentah), per shot
    per_shot = []
    for t0, t1 in shots:
        per_shot.append([(t, f) for t, f in samples if t0 <= t < t1])

    xs, cam, n_frames = [], None, int(round(dur * out_fps))
    fallback = src_w / 2
    shot_meta = []
    for (t0, t1), ss in zip(shots, per_shot):
        # titik awal shot: median beberapa target pertama (kamera langsung "potong" ke subjek)
        firsts = [x for x in (_target(f, None, cw) for _, f in ss[:5]) if x is not None]
        start_cam = sorted(firsts)[len(firsts) // 2] if firsts else (cam if cam is not None else fallback)
        cam = goal = start_cam
        pending, lost_since, k = 0, None, 0
        f0, f1 = int(round(t0 * out_fps)), int(round(t1 * out_fps))
        for fi in range(f0, min(f1, n_frames)):
            t = fi / out_fps
            while k < len(ss) and ss[k][0] <= t:
                tgt = _target(ss[k][1], cam, cw)
                if tgt is None:
                    lost_since = lost_since if lost_since is not None else ss[k][0]
                    pending = 0
                else:
                    lost_since = None
                    if abs(tgt - goal) > DEAD_ZONE * cw / 2:
                        pending += 1
                        if pending >= CONFIRM:
                            goal, pending = tgt, 0
                    else:
                        pending = 0
                k += 1
            dist = goal - cam
            speed = (FAST if abs(dist) > 0.5 * cw else SLOW) * cw / out_fps
            cam += max(-speed, min(speed, dist))
            xs.append(int(max(0, min(src_w - cw, cam - cw / 2))) // 2 * 2)
        shot_meta.append({"t0": round(t0, 3), "t1": round(t1, 3), "faces": sum(1 for _, f in ss if f)})
    while len(xs) < n_frames:
        xs.append(xs[-1] if xs else int((src_w - cw) / 2))
    return {"cw": cw, "xs": xs, "fps": out_fps, "shots": shot_meta,
            "face_ratio": round(with_face / len(samples), 2), "engine": info.get("engine")}


def write_cmds(pl: dict, path: Path) -> None:
    """Perintah sendcmd: ubah x crop hanya saat nilainya berubah."""
    lines, last = [], None
    for i, x in enumerate(pl["xs"]):
        if x != last:
            lines.append(f"{i / pl['fps']:.3f} crop@rf x {x};")
            last = x
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def crop_filter(pl: dict, cmd_rel: str, src_h: int, W: int, H: int) -> str:
    """Filter FFmpeg: crop bergerak mengikuti rencana kamera (cmd_rel relatif terhadap cwd ffmpeg)."""
    x0 = pl["xs"][0] if pl["xs"] else 0
    return f"sendcmd=f={cmd_rel},crop@rf=w={pl['cw']}:h={src_h}:x={x0}:y=0,scale={W}:{H},setsar=1"


def track(src: Path, start: float, end: float, W: int, H: int, progress=lambda m: None) -> tuple[dict | None, dict]:
    """Analisis + rencana kamera sekaligus. (rencana atau None, info ringkas)."""
    info = analyze(src, start, end, progress)
    pl = plan(info, W, H)
    summary = {"engine": info["engine"], "cuts": info["cuts"], "shots": len(info["cuts"]) + 1, "src_h": info["height"],
               "face_ratio": pl["face_ratio"] if pl else 0.0}
    return pl, summary
