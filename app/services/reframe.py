"""Auto-reframe Clipper: bingkai 9:16 seperti editor podcast multi-kamera — sorot ORANG YANG SEDANG BICARA.

Riset (Nov 2026): OpusClip/podcli/opensource-clipping memakai diarization (pyannote/torch) atau model ASD
(TalkNet/Light-ASD, torch). Torch/onnxruntime diblokir Smart App Control di laptop user, jadi di sini memakai
sinyal klasik audio-visual yang cukup dengan OpenCV 4.x + numpy:

1. Potongan scene: selisih HSV antar-frame (ContentDetector PySceneDetect), adaptif.
2. Wajah + 5 titik landmark: YuNet (cv2.FaceDetectorYN) ±10x per detik, dilacak per shot (track).
3. Gerak bibir: patch mulut (dari titik sudut bibir YuNet) dibandingkan dengan sampel sebelumnya pada
   wajah yang sama, dikurangi gerak patch mata (gerak kepala/kamera) → "aktivitas bicara" per wajah.
4. Kapan ada yang bicara: timestamp kata Whisper. Kata dikelompokkan jadi "giliran" (putus di jeda/tanda baca);
   tiap giliran → wajah dengan gerak bibir tertinggi (dibandingkan baseline wajah itu sendiri saat diam).
   Ragu → tahan pembicara sebelumnya. Giliran < MIN_HOLD dilebur (anti kedip).
5. Kamera = POTONG KERAS di jeda antar-kata (seperti switcher multi-kamera), BUKAN geser pelan. Dalam satu
   giliran bingkai DIAM. Zoom ditentukan ukuran wajah (shot lebar → sedikit diperbesar, maks MAX_ZOOM),
   konstan per giliran, mata di ±38% tinggi bingkai.
6. Layout per shot: semua wajah muat → bingkai kelompok; 2 orang saling sahut cepat (atau pembicara tak
   jelas) → layar dibagi atas/bawah; 3+ orang tak jelas / tanpa wajah → video utuh + latar blur.

Hasil = daftar segmen {t0, t1, layout, rects} → satu graf filter FFmpeg (trim per segmen + concat).
"""
from pathlib import Path

from .. import config

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
DETECT_FPS = 15.0       # deteksi wajah + gerak bibir per detik
ANALYZE_W = 960         # lebar frame analisis (wajah kecil di shot lebar 3 orang tetap ±40 px)
CUT_MIN = 18.0          # selisih HSV minimum untuk dianggap potongan scene
CUT_RATIO = 3.0         # ... dan harus ≥ 3x rata-rata selisih frame-frame sebelumnya
MIN_SHOT = 0.5          # detik; potongan lebih rapat dari ini diabaikan (kilat/flash)
MIN_HOLD = 1.3          # detik; giliran bicara lebih pendek dari ini tidak memicu potong kamera
SPLIT_TURN = 3.0        # 2 orang, median giliran < ini (dan ≥3 giliran) → layar dibagi
FACE_H = 0.17           # tinggi wajah ideal relatif tinggi bingkai 9:16 (medium close-up)
SPLIT_FACE_H = 0.30     # ... untuk tiap panel layar dibagi
MAX_ZOOM = 1.5          # batas perbesaran dari tinggi penuh sumber (kualitas)
EYE_Y = 0.38            # posisi mata dari atas bingkai
BIN, WIN = 0.5, 1.0     # keputusan pembicara per 0.5 dtk; korelasi bibir-suara dalam jendela ±1 dtk
SWITCH = 0.9            # penalti pindah pembicara (jumlah selisih korelasi yang dibutuhkan ≈ 2 dtk bukti)
SURE_OWN, SURE_MARGIN = 0.12, 0.06  # di bawah ini pembicara dianggap belum jelas


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

    def full(self, img) -> list[tuple]:
        """[(x, y, w, h, skor, landmark[10] | None)] — landmark: mata ka/ki, hidung, sudut bibir ka/ki."""
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, faces = self.yunet.detect(img)
            return [tuple(float(v) for v in f[:4]) + (float(f[14]), [float(v) for v in f[4:14]])
                    for f in (faces if faces is not None else [])]
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        found = self.haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        if not len(found):
            found = self.haar.detectMultiScale(self.cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        return [(float(x), float(y), float(fw), float(fh), 0.7, None) for x, y, fw, fh in found]

    def __call__(self, img) -> list[tuple]:
        return [f[:5] for f in self.full(img)]


# ---------------------------------------------------------------- analisis video

def _patch(gray, cx: float, cy: float, pw: float, ph: float, clip_x=None, size=(32, 20)):
    """Patch abu-abu (rata-rata dikurangi). clip_x = (x0, x1) membatasi ke dalam kotak wajah (wajah menyamping:
    jangan ikut menangkap latar belakang di sebelah pipi)."""
    import cv2
    H, W = gray.shape[:2]
    x0, x1 = cx - pw / 2, cx + pw / 2
    if clip_x:
        x0, x1 = max(x0, clip_x[0]), min(x1, clip_x[1])
    x0, x1 = int(max(0, x0)), int(min(W, x1))
    y0, y1 = int(max(0, cy - ph / 2)), int(min(H, cy + ph / 2))
    if x1 - x0 < 6 or y1 - y0 < 4:
        return None
    import numpy as np
    p = cv2.resize(gray[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA).astype(np.float32)
    return p - p.mean()


def _patches(gray, face):
    """(patch mulut, patch mata) untuk satu wajah (koordinat piksel sumber)."""
    x, y, w, h, _, lm = face
    if lm:
        rex, rey, lex, ley, _, _, rmx, rmy, lmx, lmy = lm
        mw = max(abs(lmx - rmx) * 1.5, 0.45 * w)
        mouth = _patch(gray, (rmx + lmx) / 2, (rmy + lmy) / 2 + 0.04 * h, mw, mw * 0.65, (x, x + w))
        ew = max(abs(lex - rex) * 1.6, 0.5 * w)
        eyes = _patch(gray, (rex + lex) / 2, (rey + ley) / 2, ew, ew * 0.45, (x, x + w))
    else:  # Haar: perkiraan dari kotak wajah
        mouth = _patch(gray, x + w / 2, y + 0.78 * h, 0.55 * w, 0.32 * h)
        eyes = _patch(gray, x + w / 2, y + 0.38 * h, 0.75 * w, 0.25 * h)
    return mouth, eyes


def _diff(p, q) -> float:
    """Selisih rata-rata dua patch, toleran geser ±1 piksel (goyang deteksi bukan gerak bibir)."""
    import numpy as np
    best = 1e9
    b = q[1:-1, 1:-1]
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            best = min(best, float(np.abs(p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx] - b).mean()))
    return best


def _audio_env(src: Path, start: float, dur: float, rate: float):
    """Log-RMS audio per 1/rate detik — dipakai untuk mencocokkan gerak bibir dengan suara."""
    import subprocess

    import numpy as np
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, timeout=300)
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768
    hop = int(16000 / rate)
    n = len(x) // hop
    if n == 0:
        return np.zeros(1, np.float32)
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)
    return np.log(rms + 1e-4)


def _frames(src: Path, start: float, dur: float, w: int, h: int, fps: int):
    """Frame BGR berukuran w×h berurutan lewat pipa FFmpeg. Pengecilan dilakukan FFmpeg (multi-thread) — dulu frame
    1080p penuh dialirkan (±6 MB/frame, ±14 GB untuk klip 80 dtk) padahal deteksi hanya memakai 640 px."""
    import subprocess

    import numpy as np
    proc = subprocess.Popen([config.FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                             "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=area", "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=w * h * 3)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _probe(src: Path) -> tuple[int, int]:
    import cv2
    cap = cv2.VideoCapture(str(src))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not w or not h:
        raise RuntimeError("Video sumber tidak bisa dibaca.")
    return w, h


def analyze(src: Path, start: float, end: float, progress=lambda m: None) -> dict:
    """Baca rentang klip → {width, height, duration, cuts, tracks, env, rate, engine}.
    tracks: [{id, shot, samples: [(t, cx, cy, w, h, m)]}] — piksel sumber, waktu relatif awal klip;
    m = gerak bibir (selisih patch mulut dikurangi gerak patch mata) sejak sampel sebelumnya.
    env = log-RMS audio per sampel (indeks round(t*rate))."""
    import cv2
    import numpy as np

    src_w, src_h = _probe(src)
    dur = max(end - start, 0.1)
    fps = int(round(config.FPS))
    every = max(1, int(round(fps / DETECT_FPS)))
    rate = fps / every
    det = _Faces()
    # frame dianalisis di ANALYZE_W; deteksi wajah di 640 px; koordinat disimpan dalam piksel sumber
    aw = min(src_w, ANALYZE_W) // 2 * 2
    ah = max(2, int(round(src_h * aw / src_w / 2)) * 2)
    k_src = aw / src_w                      # frame analisis → sumber: bagi dengan k_src
    scale = 640 / aw if aw > 640 else 1.0   # frame analisis → frame deteksi
    prev_hsv, recent, cuts = None, [], []
    tracks, live = [], []
    total = int(dur * fps)
    for i, frame in enumerate(_frames(src, start, dur, aw, ah, fps)):
        t = i / fps
        tiny = cv2.resize(frame, (160, max(1, int(160 * ah / aw))), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(tiny, cv2.COLOR_BGR2HSV).astype(np.int16)
        if prev_hsv is not None:
            val = float(np.abs(hsv - prev_hsv).mean(axis=(0, 1)).mean())
            avg = sum(recent) / len(recent) if recent else 0.0
            if val >= CUT_MIN and val >= CUT_RATIO * (avg + 1.0) and (not cuts or t - cuts[-1] >= MIN_SHOT) and t >= MIN_SHOT:
                cuts.append(round(t, 3))
                recent, live = [], []  # shot baru → semua track ditutup
            else:
                recent = (recent + [val])[-8:]
        prev_hsv = hsv
        if i % every:
            continue
        small = cv2.resize(frame, (int(aw * scale), int(ah * scale)), interpolation=cv2.INTER_AREA)             if scale != 1.0 else frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = []  # koordinat frame analisis (patch bibir diambil dari frame ini)
        for x, y, w, h, sc, lm in det.full(small):
            if sc >= 0.6:
                faces.append((x / scale, y / scale, w / scale, h / scale, sc, [v / scale for v in lm] if lm else None))
        used = set()
        for f in sorted(faces, key=lambda f: -f[2]):
            cx, cy, w = (f[0] + f[2] / 2) / k_src, (f[1] + f[3] / 2) / k_src, f[2] / k_src
            best, bd = None, None
            for tr in live:
                if id(tr) in used or t - tr["last"][0] > 0.6:
                    continue
                lt = tr["last"]
                d_ = abs(cx - lt[1]) + abs(cy - lt[2])
                if d_ < 0.6 * max(w, lt[3]) and 0.6 < w / lt[3] < 1.6 and (bd is None or d_ < bd):
                    best, bd = tr, d_
            mouth, eyes = _patches(gray, f)
            m = None
            if best is not None and t - best["last"][0] <= 2.5 / rate and mouth is not None and best["mp"] is not None:
                e = _diff(eyes, best["ep"]) if eyes is not None and best["ep"] is not None else 0.0
                m = round(_diff(mouth, best["mp"]) - 0.8 * e, 3)
            if best is None:
                best = {"id": len(tracks), "shot": len(cuts), "samples": []}
                tracks.append(best)
                live.append(best)
            used.add(id(best))
            best["last"] = (t, cx, cy, w)
            best["mp"], best["ep"] = mouth, eyes
            best["samples"].append((round(t, 3), round(cx, 1), round(cy, 1), round(w, 1), round(f[3] / k_src, 1), m))
        if (i // every) % 45 == 0:
            progress(f"Auto-reframe: mencari siapa yang bicara {min(100, i / max(1, total) * 100):.0f}%…")
    for tr in tracks:
        for k in ("mp", "ep", "last"):
            tr.pop(k, None)
    env = _audio_env(src, start, dur, rate)
    return {"width": src_w, "height": src_h, "duration": dur, "cuts": cuts, "tracks": tracks,
            "env": [round(float(v), 3) for v in env], "rate": rate, "engine": det.engine}


# ---------------------------------------------------------------- siapa yang bicara

def _main_tracks(tracks: list[dict], t0: float, t1: float) -> list[dict]:
    """Wajah utama dalam shot: cukup sering terlihat & tidak kecil di latar belakang."""
    n_samp = max(1.0, (t1 - t0) * DETECT_FPS)
    cand = [tr for tr in tracks if len(tr["samples"]) >= max(2, 0.35 * n_samp)]
    if not cand:
        return []
    big = max(_med([s[4] for s in tr["samples"]]) for tr in cand)
    return [tr for tr in cand if _med([s[4] for s in tr["samples"]]) >= 0.45 * big]


def _med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def _corr(a, b) -> float | None:
    import numpy as np
    if len(a) < 8:
        return None
    a, b = np.asarray(a, np.float32), np.asarray(b, np.float32)
    if a.std() < 1e-6 or b.std() < 1e-6:
        return None
    return float(((a - a.mean()) * (b - b.mean())).mean() / (a.std() * b.std()))


def _bin_scores(mains, edges, env, rate, speech) -> list[dict]:
    """Per bin: {track_id: korelasi gerak bibir vs naik-turun suara dalam jendela ±WIN} (hanya saat ada suara)."""
    series = {tr["id"]: [(s[0], s[5]) for s in tr["samples"] if s[5] is not None] for tr in mains}
    out = []
    for k in range(len(edges) - 1):
        c = (edges[k] + edges[k + 1]) / 2
        row = {}
        if speech(c):
            for tid, ser in series.items():
                pts = [(t, m) for t, m in ser if c - WIN <= t <= c + WIN]
                best = None
                for lag in (-1, 0, 1):   # bibir bisa sedikit mendahului/terlambat dari suara
                    xs, ys = [], []
                    for t, m in pts:
                        j = int(round(t * rate)) + lag
                        if 0 <= j < len(env):
                            xs.append(m)
                            ys.append(env[j])
                    r = _corr(xs, ys)
                    if r is not None and (best is None or r > best):
                        best = r
                row[tid] = best
        out.append(row)
    return out


def _speaker_runs(mains: list[dict], t0: float, t1: float, env: list[float], rate: float, speech) -> list:
    """Viterbi per bin BIN dtk; pindah pembicara dikenai penalti SWITCH → [[a, b, track_id, yakin]]."""
    n_bins = max(1, int(round((t1 - t0) / BIN)))
    edges = [t0 + (t1 - t0) * k / n_bins for k in range(n_bins + 1)]
    score = _bin_scores(mains, edges, env, rate, speech)
    ids = [tr["id"] for tr in mains]
    acc = {tid: 0.0 for tid in ids}
    back = []
    for row in score:
        nxt, bp = {}, {}
        for tid in ids:
            other = max((o for o in ids if o != tid), key=lambda o: acc[o], default=None)
            if other is not None and acc[other] - SWITCH > acc[tid]:
                nxt[tid], bp[tid] = acc[other] - SWITCH, other
            else:
                nxt[tid], bp[tid] = acc[tid], tid
            nxt[tid] += row.get(tid) or 0.0
        acc = nxt
        back.append(bp)
    cur = max(ids, key=lambda tid: acc[tid])
    path = []
    for bp in reversed(back):
        path.append(cur)
        cur = bp[cur]
    path.reverse()
    runs = []
    for k, tid in enumerate(path):
        if runs and runs[-1][2] == tid:
            runs[-1][1] = edges[k + 1]
        else:
            runs.append([edges[k], edges[k + 1], tid])
    # anti kedip: giliran pendek dilebur ke tetangga
    while len(runs) > 1:
        k = min(range(len(runs)), key=lambda k: runs[k][1] - runs[k][0])
        if runs[k][1] - runs[k][0] >= MIN_HOLD:
            break
        j = k - 1 if k > 0 else k + 1
        runs[j][0], runs[j][1] = min(runs[j][0], runs[k][0]), max(runs[j][1], runs[k][1])
        runs.pop(k)
        merged = [runs[0]]
        for r in runs[1:]:
            if r[2] == merged[-1][2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    # keyakinan per giliran: skor sendiri & selisih dengan pesaing terkuat
    for r in runs:
        margins, own = [], []
        for k, row in enumerate(score):
            c = (edges[k] + edges[k + 1]) / 2
            if r[0] <= c < r[1] and row.get(r[2]) is not None:
                others = [v for o, v in row.items() if o != r[2] and v is not None]
                own.append(row[r[2]])
                margins.append(row[r[2]] - (max(others) if others else 0.0))
        sure = bool(own) and sum(own) / len(own) >= SURE_OWN and sum(margins) / len(margins) >= SURE_MARGIN
        r.append(sure)
    return runs


def _snap_cut(t: float, lo: float, hi: float, env: list[float], rate: float) -> float:
    """Geser titik potong ke saat paling sunyi dalam ±0.4 dtk (potong di jeda, bukan di tengah kata)."""
    a, b = max(lo, t - 0.4), min(hi, t + 0.4)
    ks = [k for k in range(int(a * rate), int(b * rate) + 1) if 0 <= k < len(env)]
    return min(ks, key=lambda k: env[k]) / rate if ks else t


# ---------------------------------------------------------------- bingkai

def _even(v: float) -> int:
    return int(v) // 2 * 2


def _rect(tr_list: list[dict], src_w: int, src_h: int, aspect: float, face_h: float) -> tuple:
    """Kotak crop statis (x, y, w, h) untuk satu/sekelompok wajah. aspect = lebar/tinggi keluaran."""
    cx = [_med([s[1] for s in tr["samples"]]) for tr in tr_list]
    cy = [_med([s[2] for s in tr["samples"]]) for tr in tr_list]
    fw = [_med([s[3] for s in tr["samples"]]) for tr in tr_list]
    fh = max(_med([s[4] for s in tr["samples"]]) for tr in tr_list)
    ch = min(src_h, max(src_h / MAX_ZOOM, fh / face_h))
    lo = min(c - w * 0.9 for c, w in zip(cx, fw))
    hi = max(c + w * 0.9 for c, w in zip(cx, fw))
    ch = min(src_h, max(ch, (hi - lo) / aspect))          # kelompok harus muat
    cw = min(src_w, ch * aspect)
    ch = cw / aspect
    x = min(max(0.0, (lo + hi) / 2 - cw / 2), src_w - cw)
    eye = min(cy) - 0.12 * fh
    y = min(max(0.0, eye - EYE_Y * ch), src_h - ch)
    return (_even(x), _even(y), _even(cw), _even(ch))


def _speech_fn(words: list[dict], env: list[float], rate: float):
    """Ada yang bicara di detik t? Kata Whisper ATAU energi suara jelas di atas dasar kebisingan."""
    floor = sorted(env)[len(env) // 7] if env else 0.0

    def speech(t: float) -> bool:
        if any(w["start"] - 0.15 <= t <= w["end"] + 0.15 for w in words):
            return True
        ks = [k for k in range(int((t - 0.25) * rate), int((t + 0.25) * rate) + 1) if 0 <= k < len(env)]
        return bool(ks) and sum(env[k] for k in ks) / len(ks) > floor + 0.9
    return speech


def plan(info: dict, words: list[dict], W: int, H: int, out_fps: float = 30.0) -> dict | None:
    """Rencana kamera: {segments: [{t0, t1, layout, rects, speaker, face, sure}], stats}. None bila tak perlu."""
    src_w, src_h, dur = info["width"], info["height"], info["duration"]
    aspect = W / H
    tracks, env, rate = info["tracks"], info.get("env") or [], info.get("rate") or DETECT_FPS
    if not tracks or src_w * H <= src_h * W * 1.05:   # tanpa wajah / sumber sudah vertikal
        return None
    bounds = [0.0] + [c for c in info["cuts"] if 0 < c < dur] + [dur]
    speech = _speech_fn(words, env, rate)
    segs = []
    stats = {"switches": 0, "single": 0.0, "group": 0.0, "split": 0.0, "fit": 0.0, "unsure": 0.0}

    def add(t0, t1, layout, rects=(), spk=None, sure=True):
        if t1 - t0 <= 1e-3:
            return
        stats[layout] = stats.get(layout, 0.0) + (t1 - t0)
        layout_out = "single" if layout == "group" else layout
        plain = [list(r[:4]) for r in rects]
        if segs and segs[-1]["layout"] == layout_out and segs[-1]["rects"] == plain and abs(segs[-1]["t1"] - t0) < 1e-3:
            segs[-1]["t1"] = t1
            return
        face = list(rects[0][4]) if layout == "single" and rects and len(rects[0]) > 4 else None
        segs.append({"t0": t0, "t1": t1, "layout": layout_out, "rects": plain, "speaker": spk, "face": face,
                     "sure": sure})

    def single(tr):
        r = _rect([tr], src_w, src_h, aspect, FACE_H)
        fx = (_med([s[1] for s in tr["samples"]]) - r[0]) / r[2]
        fy = (_med([s[2] for s in tr["samples"]]) - r[1]) / r[3]
        return r + ((round(min(max(fx, 0.15), 0.85), 3), round(min(max(fy, 0.15), 0.85), 3)),)

    def nobody(t0, t1, mains):
        """Pembicara tidak jelas → tampilkan semua: 2 orang = layar dibagi, 3+ = video utuh."""
        if len(mains) == 2:
            add(t0, t1, "split", _split_rects(mains, src_w, src_h, W, H), sure=False)
        else:
            add(t0, t1, "fit", [_wide_rect(mains, src_w, src_h)], sure=False)

    for si, (t0, t1) in enumerate(zip(bounds[:-1], bounds[1:])):
        mains = _main_tracks([tr for tr in tracks if tr["shot"] == si], t0, t1)
        if not mains:
            add(t0, t1, "fit")
            continue
        if len(mains) == 1:
            add(t0, t1, "single", [single(mains[0])], mains[0]["id"])
            continue
        grp = _rect(mains, src_w, src_h, aspect, FACE_H)
        lo = min(_med([s[1] for s in tr["samples"]]) - 0.9 * _med([s[3] for s in tr["samples"]]) for tr in mains)
        hi = max(_med([s[1] for s in tr["samples"]]) + 0.9 * _med([s[3] for s in tr["samples"]]) for tr in mains)
        if hi - lo <= grp[2]:                      # semua wajah muat satu bingkai → bingkai kelompok, diam
            add(t0, t1, "group", [grp])
            continue
        if t1 - t0 < 2 * MIN_HOLD:                 # shot terlalu pendek untuk menilai siapa yang bicara
            nobody(t0, t1, mains)
            continue
        runs = _speaker_runs(mains, t0, t1, env, rate, speech)
        sure_runs = [r for r in runs if r[3]]
        if not sure_runs:
            nobody(t0, t1, mains)
            stats["unsure"] += t1 - t0
            continue
        lens = sorted(r[1] - r[0] for r in runs)
        if len(mains) == 2 and len(runs) >= 3 and lens[len(lens) // 2] < SPLIT_TURN:
            add(t0, t1, "split", _split_rects(mains, src_w, src_h, W, H))   # saling sahut cepat → keduanya tampil
            continue
        by_id = {tr["id"]: tr for tr in mains}
        cut = [t0] + [_snap_cut(runs[k][0], runs[k - 1][0] + 0.5, runs[k][1] - 0.5, env, rate)
                      for k in range(1, len(runs))] + [t1]
        for k, r in enumerate(runs):
            if r[3]:
                add(cut[k], cut[k + 1], "single", [single(by_id[r[2]])], r[2])
            else:
                nobody(cut[k], cut[k + 1], mains)
                stats["unsure"] += cut[k + 1] - cut[k]
        stats["switches"] += len(runs) - 1
    # rapikan ke grid frame keluaran supaya concat tidak bergeser dari audio
    for s in segs:
        s["t0"], s["t1"] = round(round(s["t0"] * out_fps) / out_fps, 4), round(round(s["t1"] * out_fps) / out_fps, 4)
    segs = [s for s in segs if s["t1"] > s["t0"]]
    if not segs:
        return None
    segs[0]["t0"], segs[-1]["t1"] = 0.0, round(round(dur * out_fps) / out_fps, 4)
    for a, b in zip(segs, segs[1:]):
        b["t0"] = a["t1"]
    stats = {k: round(v, 1) if isinstance(v, float) else v for k, v in stats.items()}
    return {"segments": segs, "stats": stats}


def _wide_rect(mains: list[dict], src_w: int, src_h: int) -> tuple:
    """Shot lebar 3+ orang tanpa pembicara jelas: potong hanya area tempat mereka duduk (lebar kelompok + ruang),
    lalu ditampilkan utuh di tengah dengan latar blur — orang tampil ±1.5x lebih besar daripada seluruh frame."""
    lo = min(_med([s[1] for s in tr["samples"]]) - 1.2 * _med([s[3] for s in tr["samples"]]) for tr in mains)
    hi = max(_med([s[1] for s in tr["samples"]]) + 1.2 * _med([s[3] for s in tr["samples"]]) for tr in mains)
    w = min(src_w, max(hi - lo, src_h * 0.9))
    x = min(max(0.0, (lo + hi) / 2 - w / 2), src_w - w)
    return (_even(x), 0, _even(w), _even(src_h))


def _split_rects(mains: list[dict], src_w, src_h, W, H) -> list:
    """Dua panel atas/bawah: orang kiri di atas, kanan di bawah. Panel tidak saling memotong wajah."""
    two = sorted(sorted(mains, key=lambda tr: -_med([s[4] for s in tr["samples"]]))[:2],
                 key=lambda tr: _med([s[1] for s in tr["samples"]]))
    aspect = W / (H / 2)
    return [_rect([tr], src_w, src_h, aspect, SPLIT_FACE_H) for tr in two]


# ---------------------------------------------------------------- FFmpeg

def segment_inputs(pl: dict, src: Path, start: float, fps: int) -> list[str]:
    """Argumen input FFmpeg: satu input per segmen, masing-masing di-seek ke awal segmennya (input ke-k = segmen k).
    Dulu satu input di-split ke semua segmen lalu di-trim: tiap frame disalin n kali dan frame shot berikutnya
    MENUMPUK di memori sampai concat sampai ke gilirannya (klip 60 dtk / 14 shot: ±200 dtk & memori besar)."""
    out = []
    for s in pl["segments"]:
        f0 = int(round(s["t0"] * fps))
        out += ["-ss", f"{start + f0 / fps:.4f}", "-t", f"{s['t1'] - s['t0'] + 2 / fps:.4f}", "-i", str(src)]
    return out


def filter_graph(pl: dict, W: int, H: int, fps: int) -> str:
    """Graf filter [k:v] (input per segmen, lihat segment_inputs) → [v]: tiap segmen dipangkas ke jumlah frame
    persisnya (grid frame), dibingkai sesuai layout, lalu disambung."""
    from . import ffmpeg_utils as ff
    segs = pl["segments"]
    n = len(segs)
    g = []
    hh = H // 2 // 2 * 2
    for k, s in enumerate(segs):
        f0, f1 = int(round(s["t0"] * fps)), int(round(s["t1"] * fps))
        head = f"[{k}:v]fps={fps},trim=end_frame={f1 - f0},setpts=PTS-STARTPTS"
        if s["layout"] == "single":
            x, y, w, h = s["rects"][0]
            g.append(f"{head},crop={w}:{h}:{x}:{y},scale={W}:{H},setsar=1[v{k}]")
        elif s["layout"] == "split":
            (x1, y1, w1, h1), (x2, y2, w2, h2) = s["rects"]
            g.append(f"{head},split[a{k}][b{k}];[a{k}]crop={w1}:{h1}:{x1}:{y1},scale={W}:{hh}[ta{k}];"
                     f"[b{k}]crop={w2}:{h2}:{x2}:{y2},scale={W}:{H - hh}[tb{k}];"
                     f"[ta{k}][tb{k}]vstack,drawbox=x=0:y={hh - 2}:w={W}:h=4:color=black@0.9:t=fill,setsar=1[v{k}]")
        else:
            if s["rects"]:  # hanya area kelompok orang
                x, y, w, h = s["rects"][0]
                head += f",crop={w}:{h}:{x}:{y}"
            fit = ff.fit_filter(W, H, "blur").replace("[a]", f"[fa{k}]").replace("[b]", f"[fb{k}]") \
                .replace("[bg]", f"[bg{k}]").replace("[fg]", f"[fg{k}]")
            g.append(f"{head},{fit}[v{k}]")
    g.append("".join(f"[v{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]")
    return ";".join(g)


def face_in(info: dict | None, a: float, b: float) -> tuple | None:
    """Untuk punch-in: (fx, fy, ruang) wajah pembicara bila potongan [a, b] klip berada utuh di SATU segmen
    berbingkai satu orang. fx/fy 0-1 relatif bingkai keluaran; ruang = tinggi crop / tinggi sumber (1 = belum
    diperbesar). None bila melintasi potong kamera, layar dibagi, atau video utuh — punch-in tidak cocok."""
    for s in (info or {}).get("segments") or []:
        if s["t0"] - 0.05 <= a and b <= s["t1"] + 0.05:
            if s["layout"] == "single" and s.get("face") and s["rects"]:
                return (s["face"][0], s["face"][1], s["rects"][0][3] / max(1, (info or {}).get("src_h") or s["rects"][0][3]))
            return None
    return None


def track(src: Path, start: float, end: float, W: int, H: int, words: list[dict] | None = None,
          progress=lambda m: None) -> tuple[dict | None, dict]:
    """Analisis + rencana kamera. words = kata Whisper (detik ABSOLUT sumber). (rencana atau None, ringkasan)."""
    info = analyze(src, start, end, progress)
    rel = [{**w, "start": w["start"] - start, "end": w["end"] - start} for w in (words or [])
           if w["end"] > start and w["start"] < end]
    pl = plan(info, rel, W, H, config.FPS)
    seen = {s[0] for tr in info["tracks"] for s in tr["samples"]}
    summary = {"engine": info["engine"], "cuts": info["cuts"], "shots": len(info["cuts"]) + 1, "src_h": info["height"],
               "face_ratio": round(min(1.0, len(seen) / max(1.0, info["duration"] * info["rate"])), 2),
               "segments": pl["segments"] if pl else [], "stats": pl["stats"] if pl else {}}
    return pl, summary
