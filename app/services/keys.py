"""Kolam API key: satu layanan AI boleh punya BANYAK key (akun gratis berbeda = kuota terpisah).

Cara isi di .env (boleh dicampur):
    GROQ_API_KEY=gsk_aaa,gsk_bbb          ← dipisah koma
    GROQ_API_KEY_2=gsk_ccc                ← atau bernomor _2, _3, … (sampai _20)

Aturan pakai:
  - Key dipilih bergiliran (yang paling lama tidak dipakai lebih dulu) → beban tersebar rata.
  - Kena batas (429) → key itu "didinginkan" untuk model itu saja sampai pulih; key lain langsung dipakai,
    tanpa menunggu. Kuota harian habis → didinginkan lebih lama.
  - Key ditolak (401/403 / key tidak valid) → dinonaktifkan sampai aplikasi di-restart.
Status disimpan di memori saja (tidak pernah ditulis ke disk / log, supaya key tidak bocor).
"""
import os
import re
import threading
import time

from .. import config  # noqa: F401 — memastikan .env sudah dimuat ke os.environ

_lock = threading.Lock()
_cool: dict[tuple[str, str, str], float] = {}   # (layanan, key, model) → pulih pada (epoch)
_dead: dict[tuple[str, str], str] = {}          # (layanan, key) → alasan
_used: dict[tuple[str, str], float] = {}        # (layanan, key) → terakhir dipakai

ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY",
       "cerebras": "CEREBRAS_API_KEY", "mistral": "MISTRAL_API_KEY", "pollinations": "POLLINATIONS_KEY"}


def pool(service: str) -> list[str]:
    """Semua key untuk satu layanan, urutan sesuai .env, tanpa duplikat."""
    base = ENV.get(service, service.upper() + "_API_KEY")
    raw = [os.getenv(base, "")] + [os.getenv(f"{base}_{i}", "") for i in range(2, 21)]
    out = []
    for chunk in raw:
        for k in re.split(r"[,\s;]+", chunk or ""):
            k = k.strip().strip('"').strip("'")
            if k and k not in out:
                out.append(k)
    return out


def has(service: str) -> bool:
    return any(k for k in pool(service) if (service, k) not in _dead)


def mask(key: str) -> str:
    return f"…{key[-4:]}" if len(key) > 8 else "…"


def label(service: str, key: str) -> str:
    """'key 2/3' — dipakai di pesan progres (tanpa membuka isi key)."""
    keys = pool(service)
    return f"key {keys.index(key) + 1}/{len(keys)}" if key in keys and len(keys) > 1 else ""


def candidates(service: str, model: str = "") -> list[str]:
    """Key yang siap dipakai sekarang untuk model ini, paling lama tidak dipakai di depan."""
    now = time.time()
    with _lock:
        ks = [k for k in pool(service) if (service, k) not in _dead and _cool.get((service, k, model), 0) <= now]
        return sorted(ks, key=lambda k: _used.get((service, k), 0))


def take(service: str, model: str = "") -> str | None:
    """Ambil satu key siap pakai & tandai dipakai (thread lain akan mendapat key berbeda)."""
    with _lock:
        now = time.time()
        ks = [k for k in pool(service) if (service, k) not in _dead and _cool.get((service, k, model), 0) <= now]
        if not ks:
            return None
        k = min(ks, key=lambda x: _used.get((service, x), 0))
        _used[(service, k)] = now
        return k


def wait_time(service: str, model: str = "") -> float:
    """Detik sampai ada key yang pulih untuk model ini (0 = ada yang siap; inf = semua key mati/tidak ada)."""
    now = time.time()
    alive = [k for k in pool(service) if (service, k) not in _dead]
    if not alive:
        return float("inf")
    return max(0.0, min(_cool.get((service, k, model), 0) for k in alive) - now)


def cool(service: str, key: str, model: str = "", secs: float = 60.0) -> None:
    with _lock:
        _cool[(service, key, model)] = max(_cool.get((service, key, model), 0), time.time() + max(1.0, secs))


def kill(service: str, key: str, why: str) -> None:
    with _lock:
        _dead[(service, key)] = why[:120]
    print(f"[keys] {service} {mask(key)} dinonaktifkan: {why[:120]}")


def status() -> dict:
    """Ringkasan untuk halaman Cek Kesiapan (tanpa isi key)."""
    now = time.time()
    out = {}
    for svc in ENV:
        keys = pool(svc)
        if not keys:
            continue
        rows = []
        for i, k in enumerate(keys):
            cooling = {m: round(t - now) for (s, kk, m), t in _cool.items() if s == svc and kk == k and t > now}
            rows.append({"n": i + 1, "tail": mask(k), "dead": _dead.get((svc, k)), "cooling": cooling})
        out[svc] = rows
    return out


def gemini_429_wait(text: str) -> float:
    """Lama pendinginan dari body 429 Gemini: kuota harian → ±1 jam (dicek ulang), per menit → retryDelay."""
    if re.search(r"PerDay|per day|daily", text or "", re.I):
        return 3600.0
    m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', text or "")
    return float(m.group(1)) + 1 if m else 60.0
