"""Auto-reframe Clipper: bingkai 9:16 mengikuti wajah pembicara (OpenCV, jalan di CPU, gratis).

Cara kerja: sampel ±3 frame/detik → deteksi wajah (Haar cascade bawaan OpenCV) → deteksi pergantian shot
(perbedaan histogram warna) → per shot ambil median posisi x wajah terbesar → filter crop FFmpeg dengan
posisi x berbeda per shot (patah di batas shot, tidak goyang)."""
from pathlib import Path

SAMPLE_FPS = 3.0
CUT_THRESHOLD = 0.45   # jarak histogram (Bhattacharyya) di atas ini = pergantian shot
MERGE_DX = 0.04        # shot berurutan dengan posisi wajah hampir sama digabung


def track(src: Path, start: float, end: float, progress=lambda m: None) -> list[dict]:
    """[{t0, t1, cx}] relatif terhadap awal klip; cx = posisi tengah wajah (0..1) atau None."""
    import cv2

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError("Video sumber tidak bisa dibaca OpenCV.")
    face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
    samples, prev_hist, shots = [], None, []
    t, step, total = start, 1.0 / SAMPLE_FPS, max(end - start, 0.1)
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (640, int(640 * h / w)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = face.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        if not len(faces):  # cahaya redup/kontras rendah → coba versi histogram diratakan
            faces = face.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        cx = None
        if len(faces):
            x, _, fw, _ = max(faces, key=lambda f: f[2] * f[3])
            cx = float((x + fw / 2) / small.shape[1])
        hist = cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_BGR2HSV)], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        cut = prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > CUT_THRESHOLD
        prev_hist = hist
        samples.append((t - start, cx, cut))
        if len(samples) % 15 == 0:
            progress(f"Mendeteksi wajah {min(100, (t - start) / total * 100):.0f}%…")
        t += step
    cap.release()
    if not samples:
        return []
    # kelompokkan per shot
    cur = {"t0": 0.0, "xs": []}
    for rel, cx, cut in samples:
        if cut and cur["xs"] is not None and rel > cur["t0"]:
            shots.append(cur)
            cur = {"t0": rel, "xs": []}
        if cx is not None:
            cur["xs"].append(cx)
    shots.append(cur)
    out = []
    for i, s in enumerate(shots):
        xs = sorted(s["xs"])
        cx = xs[len(xs) // 2] if len(xs) >= max(1, 0.3 * SAMPLE_FPS) else None
        t1 = shots[i + 1]["t0"] if i + 1 < len(shots) else total
        if out and (cx is None and out[-1]["cx"] is None
                    or cx is not None and out[-1]["cx"] is not None and abs(cx - out[-1]["cx"]) < MERGE_DX):
            out[-1]["t1"] = t1
            continue
        out.append({"t0": round(s["t0"], 3), "t1": round(t1, 3), "cx": cx})
    # shot tanpa wajah ikut posisi shot berwajah terdekat sebelumnya (atau tengah)
    last = 0.5
    for s in out:
        if s["cx"] is None:
            s["cx_used"] = last
        else:
            s["cx_used"] = last = s["cx"]
    return out


def crop_filter(shots: list[dict], src_w: int, src_h: int, W: int, H: int) -> str | None:
    """Filter FFmpeg crop dengan x berubah per shot. None bila tidak ada wajah sama sekali."""
    if not shots or all(s["cx"] is None for s in shots):
        return None
    cw = min(src_w, int(round(src_h * W / H / 2)) * 2)  # lebar crop setara 9:16 dari tinggi sumber
    def x_of(cx):
        return int(max(0, min(src_w - cw, cx * src_w - cw / 2)))
    expr = str(x_of(shots[-1]["cx_used"]))
    for s in reversed(shots[:-1]):
        expr = f"if(lt(t,{s['t1']:.3f}),{x_of(s['cx_used'])},{expr})"
    return f"crop={cw}:{src_h}:'{expr}':0,scale={W}:{H},setsar=1"
