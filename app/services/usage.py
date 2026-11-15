"""Laporan produksi: AI apa saja yang dipakai & berapa lama tiap langkah.

Satu laporan dibuat per job (main.action membungkus semua job). Isinya:
  phases : langkah besar, dideteksi dari pesan progres "[n/4] Label: …" (unduh, transkrip, cari momen,
           membuat klip) dan "Video n/N: …" (jendela waktu tiap klip di produksi massal)
  steps  : langkah yang dicatat langsung oleh modul (with usage.step("Render", pid)) — per video hasil
  calls  : pemanggilan AI (usage.ai / usage.timed) — LLM, suara, transkrip, gambar, dll.
Setelah job selesai, laporan disimpan ke proyek (p["reports"][jenis job]) dan potongan per klip
ke tiap video hasil (p["report"]).

Pencatatan memakai thread-local; modul AI cukup memanggil usage.ai(...) — tanpa laporan aktif,
panggilan diabaikan. Thread pembantu ikut mencatat lewat usage.bind(rep, fn).
"""
import re
import threading
import time
from contextlib import contextmanager

_tls = threading.local()
_active: list = []          # laporan yang sedang berjalan (untuk thread pembantu tanpa laporan sendiri)
_active_lock = threading.Lock()


def _now() -> float:
    return time.time()


class Report:
    def __init__(self, title: str = ""):
        self.title = title
        self.t0 = _now()
        self.t1 = None
        self.phases: list[dict] = []   # {name, t0, t1}
        self.windows: list[dict] = []  # {n, of, t0, t1, pid}
        self.steps: list[dict] = []    # {name, pid, t0, t1, ok}
        self.calls: list[dict] = []    # {kind, service, model, seconds, purpose, pid, t}
        self._lock = threading.Lock()

    # --- deteksi fase dari pesan progres
    _PHASE = re.compile(r"^\[(\d+)/(\d+)\]\s*([^:…]+)")
    _VIDEO = re.compile(r"(?:^|\]\s*[^:]*:\s*)Video (\d+)/(\d+):")

    def feed(self, msg: str) -> None:
        t = _now()
        m = self._PHASE.match(msg or "")
        if m:
            name = m.group(3).strip()
            if not self.phases or self.phases[-1]["name"] != name:
                if self.phases and self.phases[-1]["t1"] is None:
                    self.phases[-1]["t1"] = t
                self.phases.append({"name": name, "t0": t, "t1": None})
        v = self._VIDEO.search(msg or "")
        if v:
            n, of = int(v.group(1)), int(v.group(2))
            if not self.windows or self.windows[-1]["n"] != n:
                if self.windows and self.windows[-1]["t1"] is None:
                    self.windows[-1]["t1"] = t
                self.windows.append({"n": n, "of": of, "t0": t, "t1": None, "pid": None})

    def _pid_now(self) -> str | None:
        st = getattr(_tls, "pid", None)
        if st:
            return st[-1]
        return None

    def close(self) -> "Report":
        t = self.t1 = self.t1 or _now()
        for x in self.phases + self.windows:
            if x["t1"] is None:
                x["t1"] = t
        # jendela klip ↔ pid: pid pertama yang tercatat di dalam jendela waktunya
        for w in self.windows:
            hit = next((s["pid"] for s in self.steps if s["pid"] and w["t0"] <= s["t0"] <= w["t1"]), None)
            w["pid"] = hit
        return self

    # --- keluaran
    @staticmethod
    def _ai_groups(calls: list[dict]) -> list[dict]:
        groups: dict[tuple, dict] = {}
        for c in calls:
            k = (c["kind"], c["service"], c["model"], c["purpose"])
            g = groups.setdefault(k, {"kind": c["kind"], "service": c["service"], "model": c["model"],
                                      "purpose": c["purpose"], "count": 0, "seconds": 0.0})
            g["count"] += 1
            g["seconds"] = round(g["seconds"] + (c["seconds"] or 0), 1)
        return list(groups.values())

    def as_dict(self) -> dict:
        end = self.t1 or _now()
        return {
            "title": self.title,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.t0)),
            "total": round(end - self.t0, 1),
            "phases": [{"name": p["name"], "seconds": round((p["t1"] or end) - p["t0"], 1)} for p in self.phases],
            "clips": [{"n": w["n"], "of": w["of"], "pid": w["pid"], "seconds": round((w["t1"] or end) - w["t0"], 1)}
                      for w in self.windows],
            "steps": [{"name": s["name"], "pid": s["pid"], "seconds": round((s["t1"] or end) - s["t0"], 1),
                       "ok": s.get("ok", True)} for s in self.steps],
            "ai": self._ai_groups(self.calls),
            "ai_by_pid": {pid: self._ai_groups([c for c in self.calls if c["pid"] == pid])
                          for pid in {c["pid"] for c in self.calls if c["pid"]}},
        }

    def slice_for(self, pid: str) -> dict:
        """Laporan satu video hasil: jendela waktunya di produksi massal + langkah & AI miliknya."""
        d = self.as_dict()
        win = next((c for c in d["clips"] if c["pid"] == pid), None)
        steps = [s for s in d["steps"] if s["pid"] == pid]
        known = sum(s["seconds"] for s in steps)
        if win and win["seconds"] - known > 1:
            steps.insert(0, {"name": "Potong & auto-reframe", "pid": pid,
                             "seconds": round(win["seconds"] - known, 1), "ok": True})
        return {"title": self.title, "started": d["started"], "total": win["seconds"] if win else round(known, 1),
                "steps": steps, "ai": d["ai_by_pid"].get(pid, []),
                "shared": [p for p in d["phases"] if not p["name"].lower().startswith("membuat")],
                "shared_ai": self._ai_groups([c for c in self.calls if not c["pid"]])}


def begin(title: str = "") -> Report:
    rep = Report(title)
    _tls.rep = rep
    _tls.pid = []
    with _active_lock:
        _active.append(rep)
    return rep


def current() -> Report | None:
    rep = getattr(_tls, "rep", None)
    if rep is None:  # thread pembantu (mis. pencarian momen paralel): pakai satu-satunya laporan aktif
        with _active_lock:
            rep = _active[0] if len(_active) == 1 else None
    return rep


def end() -> None:
    rep = getattr(_tls, "rep", None)
    with _active_lock:
        if rep in _active:
            _active.remove(rep)
    _tls.rep = None
    _tls.pid = []


def bind(rep: Report | None, fn):
    """Bungkus fungsi untuk thread pembantu supaya panggilan AI di dalamnya ikut tercatat."""
    pid_stack = list(getattr(_tls, "pid", None) or [])

    def run(*a, **kw):
        prev, prev_pid = getattr(_tls, "rep", None), getattr(_tls, "pid", None)
        _tls.rep, _tls.pid = rep, list(pid_stack)
        try:
            return fn(*a, **kw)
        finally:
            _tls.rep, _tls.pid = prev, prev_pid
    return run


@contextmanager
def step(name: str, pid: str | None = None):
    """Langkah bernama (durasi dicatat). pid = video hasil yang sedang dikerjakan (untuk laporan per klip)."""
    rep = current()
    if rep is None:
        yield
        return
    s = {"name": name, "pid": pid, "t0": _now(), "t1": None}
    with rep._lock:
        rep.steps.append(s)
    stack = getattr(_tls, "pid", None)
    if stack is None:
        stack = _tls.pid = []
    stack.append(pid)
    names = getattr(_tls, "names", None)
    if names is None:
        names = _tls.names = []
    names.append(name)
    try:
        yield
        s["ok"] = True
    except Exception:
        s["ok"] = False
        raise
    finally:
        s["t1"] = _now()
        stack.pop()
        names.pop()


def stepped(name: str):
    """Decorator untuk fungsi aksi (pid, params, progress): seluruh fungsi dicatat sebagai satu langkah."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def run(pid, *a, **kw):
            with step(name, pid):
                return fn(pid, *a, **kw)
        return run
    return deco


def ai(kind: str, service: str, model: str = "", seconds: float | None = None, purpose: str = "") -> None:
    """kind: LLM | Suara | Transkrip | Gambar | Visi. Aman dipanggil di mana saja (tanpa laporan → diabaikan)."""
    rep = current()
    if rep is None:
        return
    if not purpose:  # "untuk apa": langkah yang sedang berjalan di thread ini, atau fase job saat ini
        names = getattr(_tls, "names", None)
        open_phase = next((p["name"] for p in reversed(rep.phases) if p["t1"] is None), "")
        purpose = names[-1] if names else open_phase
    with rep._lock:
        rep.calls.append({"kind": kind, "service": service, "model": model or "",
                          "seconds": round(seconds, 2) if seconds is not None else None,
                          "purpose": purpose or "", "pid": rep._pid_now(), "t": _now()})


@contextmanager
def timed(kind: str, service: str, model: str = "", purpose: str = ""):
    """with usage.timed("Suara", "Edge-TTS", voice, "komentar"): …  → dicatat beserta durasinya bila sukses."""
    t = _now()
    yield
    ai(kind, service, model, _now() - t, purpose)
