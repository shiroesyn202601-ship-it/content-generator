"""Produksi massal: beberapa ide → beberapa proyek sekaligus, naskah ditulis otomatis satu per satu.

Berurutan (bukan paralel) supaya tidak menabrak batas token per menit LLM gratis. Setiap proyek berhenti
di gerbang "Setujui naskah" — kreator tetap memeriksa hasil AI sebelum lanjut (Human-in-the-Loop)."""
import time

from .. import store
from . import scriptgen


def produce(pid: str, params: dict, progress) -> dict:
    src = store.load(pid)
    ideas = src["research"].get("ideas") or []
    idx = [int(i) for i in params.get("indexes") or [] if 0 <= int(i) < len(ideas)]
    if not idx:
        raise RuntimeError("Centang minimal satu ide.")
    created, failed = [], []
    for n, i in enumerate(idx, 1):
        idea = ideas[i]
        title = idea.get("title") or f"Ide {i + 1}"
        progress(f"Proyek {n}/{len(idx)}: membuat proyek '{title}'…")
        p = store.new_project("scratch", src["format"], title, src["research"].get("keyword", ""), src["language"])

        def setup(q, i=i):
            q["voice"] = src["voice"]
            q["research"].update({k: src["research"].get(k) for k in ("keyword", "competitors", "suggestions",
                                                                       "insight", "ideas")})
            q["research"]["selected"] = i
            store.approve(q, "research")  # ide sudah dipilih kreator → langsung ke tahap naskah
            store.log(q, f"Produksi massal dari proyek {pid} (ide {i + 1})")
        store.update(p["id"], setup)

        def sub(msg, n=n):
            progress(f"Proyek {n}/{len(idx)}: {msg}")
        for attempt in range(2):
            try:
                res = scriptgen.generate(p["id"], {}, sub)
                created.append({"id": p["id"], "title": title, **{k: res.get(k) for k in ("words", "factcheck")}})
                break
            except Exception as e:  # noqa: BLE001 — satu ide gagal tidak menghentikan yang lain
                if attempt == 0 and "429" in str(e):
                    # Kuota token per menit LLM gratis habis setelah proyek sebelumnya → tunggu pulih, ulangi.
                    for left in range(65, 0, -5):
                        progress(f"Proyek {n}/{len(idx)}: batas per menit AI tercapai — menunggu {left} dtk…")
                        time.sleep(5)
                    continue
                failed.append({"id": p["id"], "title": title, "error": str(e)[:200]})
                store.update(p["id"], lambda q, e=e: store.log(q, f"Naskah otomatis gagal: {e}"))
                break
    store.update(pid, lambda q: store.log(q, f"Produksi massal: {len(created)} proyek dibuat"
                                             + (f", {len(failed)} gagal" if failed else "")))
    return {"created": created, "failed": failed}
