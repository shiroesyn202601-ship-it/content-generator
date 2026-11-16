"""Mode "Cerita AI" (ala script-to-video CapCut): setiap scene memakai gambar AI dengan KARAKTER KONSISTEN.

Kunci konsistensi:
  1) "Buku karakter": AI menulis deskripsi visual tiap tokoh yang sangat spesifik (wajah, rambut, baju, warna)
     dan gaya gambar yang sama untuk seluruh video.
  2) Gambar referensi per karakter (bisa dibuat ulang / diunggah sendiri).
  3) Gambar tiap scene dibuat dari gambar referensi karakter (image edit / multi-reference, Pollinations
     flux.2-klein-4b) + seed tetap → tokoh yang sama tetap terlihat sama di semua scene.
Tanpa POLLINATIONS_KEY: fallback teks→gambar FLUX klasik (deskripsi karakter diulang + seed tetap) —
konsistensi lebih lemah dan dibatasi ±1 gambar per beberapa puluh detik."""
import base64
import re
import time
from urllib.parse import quote

import httpx

from .. import config, store
from . import beats, llm, keys

GEN = "https://gen.pollinations.ai"
CLASSIC = "https://image.pollinations.ai/prompt/"
STYLES = {
    "3d": "3D animated feature film still, Pixar-like stylized characters, soft cinematic lighting, vibrant colors",
    "anime": "anime illustration, Ghibli-inspired, detailed painted background, soft warm colors",
    "realistis": "cinematic photorealistic film still, natural lighting, shallow depth of field, 35mm",
    "komik": "comic book illustration, bold clean ink lines, flat vivid colors, dynamic composition",
    "lukisan": "digital painting, painterly brush strokes, dramatic lighting, rich textures",
}
STYLE_LABEL = {"3d": "Animasi 3D", "anime": "Anime", "realistis": "Realistis sinematik", "komik": "Komik",
               "lukisan": "Lukisan digital"}

SETUP_SCHEMA = llm.strict({
    "style_notes": llm.STR,
    "characters": llm.arr(llm.strict({"id": llm.STR, "name": llm.STR, "look": llm.STR})),
    "scenes": llm.arr(llm.strict({"id": llm.STR, "image_prompt": llm.STR, "characters": llm.arr(llm.STR)})),
})


def has_key() -> bool:
    return keys.has("pollinations")


def _size(p: dict) -> tuple[int, int]:
    return (720, 1280) if p["format"] == "short" else (1280, 720)


# ---------------------------------------------------------------- 1) buku karakter & prompt scene
SHOTS_PER_CALL = 24  # potongan per panggilan LLM (video panjang ±70 potongan → beberapa panggilan)


def _shot_lines(p: dict, scenes: list[dict], per_beat: bool) -> str:
    out = []
    for s in scenes:
        bs = beats.split(s["text"]) if per_beat else [{"text": s["text"]}]
        if len(bs) == 1:
            out.append(f'[{s["id"]}] {bs[0]["text"].replace("*", "")}')
        else:
            out.append(f'Scene {s["id"]} (satu adegan, {len(bs)} potongan):')
            out += [f'  [{s["id"]}.{k}] {b["text"].replace("*", "")}' for k, b in enumerate(bs)]
    return "\n".join(out)


def _ask(p: dict, style: str, scenes: list[dict], per_beat: bool, known: list[dict] | None) -> dict:
    vert = p["format"] == "short"
    if known is None:
        chars_rule = """1) "characters": tokoh yang muncul BERULANG (maks 3; boleh 0 bila tidak ada tokoh, mis. video fakta tempat).
   "look" = deskripsi visual BAHASA INGGRIS 40-70 kata yang sangat spesifik & tetap: jenis kelamin, usia, etnis,
   bentuk wajah, warna kulit, rambut (gaya & warna), kumis/jenggot, pakaian lengkap dengan WARNA, aksesoris khas.
   Sesuaikan era & budaya topik (mis. kerajaan Jawa kuno → kain batik, ikat kepala, perhiasan emas; bukan baju modern).
   "id" = c1, c2, …"""
    else:
        chars_rule = ("1) Tokoh SUDAH ditetapkan (pakai id ini, jangan buat tokoh baru): "
                      + ("; ".join(f'{c["id"]} = {c["name"]}' for c in known) or "(tidak ada tokoh)")
                      + '. Isi "characters": [] dan "style_notes": "".')
    beat_rule = """
   Scene yang dipecah jadi beberapa POTONGAN ([s3.0], [s3.1], …) = satu adegan yang sama, tapi TIAP potongan mendapat
   GAMBAR SENDIRI yang menggambarkan isi kata-kata potongan itu (benda/aksi/tempat yang disebut di potongan itu),
   bukan mengulang gambar potongan sebelumnya: ganti aksi, objek, ekspresi, atau sudut kamera (wide → close-up → detail).
   Lokasi & waktu tetap nyambung dalam satu scene. Buat "image_prompt" untuk SETIAP id potongan.""" if per_beat else ""
    return llm.chat_json(
        "Kamu sutradara & storyboard artist untuk video bergambar AI. Tugasmu menjaga KONSISTENSI karakter.",
        f"""Topik: "{p['script'].get('title') or p['title']}". Gaya gambar: {STYLES[style]}.
Naskah:
{_shot_lines(p, scenes, per_beat)}

{chars_rule}
2) "scenes": untuk SETIAP id dalam kurung siku, "image_prompt" BAHASA INGGRIS 25-60 kata: lokasi/latar, aksi tokoh,
   ekspresi, jenis shot (close-up / medium / wide), pencahayaan. Sebut tokoh dengan NAMA-nya (bukan deskripsi ulang).
   Komposisi {'vertikal 9:16, subjek di tengah' if vert else 'horizontal 16:9'}. Tanpa teks/tulisan
   di gambar. "characters" = id tokoh yang tampil di gambar itu ([] bila tidak ada).{beat_rule}
3) "style_notes": 10-25 kata bahasa Inggris tambahan agar seluruh gambar seragam (palet warna, era, suasana).
SETIA PADA NASKAH: siapa melakukan apa harus persis seperti di narasi (jangan menukar peran tokoh).
Balas JSON: {{"style_notes": "...", "characters": [{{"id": "c1", "name": "...", "look": "..."}}],
"scenes": [{{"id": "s1", "image_prompt": "...", "characters": ["c1"]}}]}}""",
        temperature=0.5, schema=SETUP_SCHEMA)


def setup(pid: str, params: dict, progress) -> dict:
    """Buku karakter + prompt gambar. Video panjang: tiap potongan kalimat (beats.split) dapat prompt sendiri.
    params.keep=True → karakter & prompt scene yang ada dipertahankan, hanya prompt potongan yang ditambahkan
    (proyek lama yang dulu 1 gambar per scene)."""
    p = store.load(pid)
    scenes = p["script"].get("scenes") or []
    if not scenes:
        raise RuntimeError("Naskah masih kosong.")
    old = p["assets"].get("story") or {}
    keep = bool(params.get("keep")) and old.get("scenes")
    style = params.get("style") or old.get("style") or "3d"
    per_beat = beats.enabled(p)
    chunks, cur, n = [], [], 0
    for s in scenes:
        k = len(beats.split(s["text"])) if per_beat else 1
        if cur and n + k > SHOTS_PER_CALL:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += k
    if cur:
        chunks.append(cur)
    chars = old.get("characters") or [] if keep else None
    notes = old.get("style_notes", "") if keep else ""
    prompts_by_id = {}
    for i, part in enumerate(chunks):
        progress("AI menyusun " + ("prompt gambar per kalimat" if keep else "buku karakter & prompt gambar")
                 + (f" (bagian {i + 1}/{len(chunks)})…" if len(chunks) > 1 else "…"))
        res = _ask(p, style, part, per_beat, chars)
        if chars is None:
            chars = [{"id": c["id"], "name": c["name"], "look": c["look"], "image": None}
                     for c in res.get("characters", [])[:3] if c.get("id")]
            notes = res.get("style_notes", "")
        prompts_by_id.update({x["id"]: x for x in res.get("scenes", []) if x.get("id")})
    chars = chars or []
    ids = {c["id"] for c in chars}

    def entry(x: dict | None, fallback: str) -> dict:
        x = x or {}
        return {"prompt": x.get("image_prompt") or fallback, "characters": [c for c in x.get("characters", []) if c in ids]}

    story_scenes = {}
    for s in scenes:
        bs = beats.split(s["text"]) if per_beat else [{"w": 0, "text": s["text"]}]
        if keep and s["id"] in old["scenes"]:
            sc = {k: v for k, v in old["scenes"][s["id"]].items() if k not in ("beats", "beats_of")}
        else:
            sc = entry(prompts_by_id.get(f"{s['id']}.0") or prompts_by_id.get(s["id"]),
                       s.get("visual_query") or s["text"])
        sc["beats"] = []
        for k, b in enumerate(bs[1:], start=1):
            x = prompts_by_id.get(f"{s['id']}.{k}")
            if x and x.get("image_prompt"):
                sc["beats"].append({"w": b["w"], "text": b["text"], **entry(x, "")})
        sc["beats_of"] = s["text"]
        story_scenes[s["id"]] = sc
    n_beats = sum(len(v["beats"]) for v in story_scenes.values())

    def apply(q):
        q["assets"]["visual_mode"] = "ai_story"
        q["assets"]["story"] = {"style": style, "style_notes": notes, "characters": chars,
                                "scenes": story_scenes, "seed": old.get("seed") or int(time.time()) % 100000}
        for sid, sl in q["assets"]["scenes"].items():  # gambar potongan lama tidak cocok dengan prompt baru
            if sid in story_scenes:
                sl.pop("beats", None)
                sl.pop("beats_of", None)
        store.touch_stage(q, "assets")
        store.log(q, f"Cerita AI: {len(chars)} karakter, {len(story_scenes)} scene + {n_beats} gambar per kalimat")
    store.update(pid, apply)
    return {"characters": len(chars), "scenes": len(story_scenes), "beats": n_beats}


def add_beats(pid: str, params: dict, progress) -> dict:
    """Satu klik untuk proyek yang sudah punya gambar per scene: karakter & gambar lama dipertahankan, AI menulis
    prompt per potongan kalimat, lalu hanya gambar yang belum ada yang dibuat."""
    r = setup(pid, {"keep": True}, progress)
    return {**r, **scenes(pid, {"missing": True}, progress)}


# ---------------------------------------------------------------- pembuat gambar
def _retry_wait(r: httpx.Response) -> float:
    m = re.search(r"[Rr]etry after (\d+)", r.text)
    return float(m.group(1)) if m else float(r.headers.get("retry-after") or 0)


def _pkey() -> str | None:
    """Key Pollinations berikutnya (rotasi semua POLLINATIONS_KEY*); None = tanpa key / semua sedang habis."""
    return keys.take("pollinations") if keys.pool("pollinations") else None


def _pcool(pk: str, status: int, wait: float) -> None:
    if status in (401, 403):
        keys.kill("pollinations", pk, str(status))
    else:  # 402 saldo pollen habis (pulih per jam/hari), 429 batas per menit
        keys.cool("pollinations", pk, "", 3600 if status == 402 else max(wait, 20))


def _crop_watermark(img: bytes) -> bytes:
    """Endpoint gratis tanpa key menaruh watermark kecil di kanan bawah → buang 6% bawah."""
    import io
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(img)).convert("RGB")
        buf = io.BytesIO()
        im.crop((0, 0, im.width, int(im.height * 0.94))).save(buf, "JPEG", quality=92)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return img


def _text2img(prompt: str, w: int, h: int, seed: int, progress) -> bytes:
    from . import imagegen
    if imagegen.available():  # utama: Cloudflare FLUX (gratis harian); gagal → Pollinations di bawah
        try:
            return imagegen.cf_image(prompt, w, h, seed, progress)
        except RuntimeError as e:
            progress(f"{str(e)[:120]} — memakai Pollinations…")
    for attempt in range(4):
        pk = _pkey()
        if pk:
            r = httpx.get(f"{GEN}/image/{quote(prompt)}", params={"model": config.STORY_IMAGE_MODEL, "width": w,
                          "height": h, "seed": seed}, headers={"Authorization": f"Bearer {pk}"},
                          timeout=180, follow_redirects=True)
        else:
            r = httpx.get(CLASSIC + quote(prompt), params={"width": w, "height": h, "seed": seed, "nologo": "true",
                          "model": "flux"}, timeout=180, follow_redirects=True)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content if pk else _crop_watermark(r.content)
        wait = _retry_wait(r) or (20 if r.status_code in (402, 429, 502, 503) else 0)
        if pk and r.status_code in (401, 402, 403, 429):
            _pcool(pk, r.status_code, wait)
            if keys.candidates("pollinations"):
                continue  # key lain masih punya saldo/kuota → langsung coba
        if not wait or attempt == 3:
            raise RuntimeError(f"Pollinations {r.status_code}: {r.text[:200]}")
        progress(f"Server gambar sibuk/batas gratis ({r.status_code}) — menunggu {wait:.0f} dtk…")
        time.sleep(min(wait, 60) + 1)
    raise RuntimeError("Gagal membuat gambar.")


def _edit_with_refs(prompt: str, refs: list[bytes], w: int, h: int, seed: int, progress) -> bytes:
    """Gambar baru dari prompt + gambar referensi karakter (konsistensi wajah & pakaian)."""
    images = [{"image_url": "data:image/jpeg;base64," + base64.b64encode(b).decode()} for b in refs]
    for attempt in range(4):
        pk = _pkey()
        if not pk:
            raise RuntimeError("Semua POLLINATIONS_KEY habis saldo/kuota untuk sementara.")
        r = httpx.post(f"{GEN}/v1/images/edits", headers={"Authorization": f"Bearer {pk}"},
                       json={"model": config.STORY_IMAGE_MODEL, "prompt": prompt, "image": images,
                             "size": f"{w}x{h}", "seed": seed, "response_format": "b64_json"}, timeout=240)
        if r.status_code == 200:
            d = (r.json().get("data") or [{}])[0]
            if d.get("b64_json"):
                return base64.b64decode(d["b64_json"])
            if d.get("url"):
                return httpx.get(d["url"], timeout=120, follow_redirects=True).content
            raise RuntimeError(f"Respons gambar tak terduga: {r.text[:200]}")
        wait = _retry_wait(r) or (20 if r.status_code in (429, 502, 503) else 0)
        if r.status_code in (401, 402, 403, 429):
            _pcool(pk, r.status_code, wait)
            if keys.candidates("pollinations"):
                continue
        if not wait or attempt == 3:
            raise RuntimeError(f"Pollinations edit {r.status_code}: {r.text[:300]}")
        progress(f"Server gambar sibuk ({r.status_code}) — menunggu {wait:.0f} dtk…")
        time.sleep(min(wait, 60) + 1)
    raise RuntimeError("Gagal membuat gambar.")


def _style(st: dict) -> str:
    return f"{STYLES.get(st.get('style'), STYLES['3d'])}, {st.get('style_notes', '')}, no text, no watermark"


# ---------------------------------------------------------------- 2) gambar referensi karakter
def characters(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    st = p["assets"].get("story") or {}
    chars = st.get("characters") or []
    only = params.get("char_id")
    d = store.project_dir(pid)
    (d / "assets").mkdir(exist_ok=True)
    done = 0
    for k, c in enumerate(chars):
        if only and c["id"] != only:
            continue
        progress(f"Gambar karakter {k + 1}/{len(chars)}: {c['name']}…")
        prompt = (f"Character reference portrait, full body, front view, neutral standing pose, plain light grey "
                  f"background, single person. {c['name']}: {c['look']}. {_style(st)}")
        seed = (st.get("seed") or 1) + 1000 * (k + 1) + int(params.get("bump", 0))
        img = _text2img(prompt, 768, 1024, seed, progress)
        rel = f"assets/char_{c['id']}.jpg"
        (d / rel).write_bytes(img)
        c["image"] = rel
        done += 1

    def apply(q):
        q["assets"]["story"]["characters"] = chars
        store.touch_stage(q, "assets")
    store.update(pid, apply)
    return {"characters": done}


def save_char_upload(pid: str, cid: str, fileobj) -> dict:
    p = store.load(pid)
    chars = (p["assets"].get("story") or {}).get("characters") or []
    if cid not in {c["id"] for c in chars}:
        raise ValueError("Karakter tidak dikenal")
    rel = f"assets/char_{cid}.jpg"
    from PIL import Image
    Image.open(fileobj).convert("RGB").save(store.project_dir(pid) / rel, "JPEG", quality=92)

    def apply(q):
        for c in q["assets"]["story"]["characters"]:
            if c["id"] == cid:
                c["image"] = rel
        store.touch_stage(q, "assets")
    return store.update(pid, apply)


# ---------------------------------------------------------------- 3) gambar tiap scene
def scenes(pid: str, params: dict, progress) -> dict:
    p = store.load(pid)
    st = p["assets"].get("story") or {}
    if not st.get("scenes"):
        raise RuntimeError("Klik '1. Siapkan karakter & prompt' dulu.")
    d = store.project_dir(pid)
    (d / "assets").mkdir(exist_ok=True)
    W, H = _size(p)
    chars = {c["id"]: c for c in st.get("characters") or []}
    only = params.get("scene_id")
    beat_only = params.get("beat")  # indeks potongan (1..) di scene_id → hanya gambar potongan itu
    missing = bool(params.get("missing"))  # hanya gambar yang belum ada (proyek lama: tambah gambar per kalimat)
    index = {s["id"]: i for i, s in enumerate(p["script"]["scenes"])}
    # (sid, k): k=0 gambar utama scene, k>=1 potongan kalimat ke-k
    jobs = []
    for s in p["script"]["scenes"]:
        sc = st["scenes"].get(s["id"])
        if not sc or (only and s["id"] != only):
            continue
        shots = [sc, *(sc.get("beats") or [])]
        # semua scene → gambar utama + semua potongan; satu scene → gambar utamanya saja / potongan tertentu
        ks = [int(beat_only)] if only and beat_only else [0] if only else range(len(shots))
        for k in ks:
            if not 0 <= k < len(shots):
                continue
            img = shots[k].get("image")
            if missing and img and (d / img).is_file():
                continue
            jobs.append((s["id"], k))
    made = 0
    for n, (sid, k) in enumerate(jobs):
        sc = st["scenes"][sid]
        shot = sc if k == 0 else sc["beats"][k - 1]
        cs = [chars[c] for c in shot.get("characters", []) if c in chars]
        who = "; ".join(f"{c['name']}: {c['look']}" for c in cs)
        prompt = f"{shot['prompt']}. " + (f"Characters (keep EXACTLY the same face, hair and outfit as the reference "
                                          f"images): {who}. " if cs else "") + _style(st)
        seed = (st.get("seed") or 1) + index[sid] + 1 + 211 * k + int(params.get("bump", 0))
        refs = [(d / c["image"]).read_bytes() for c in cs if c.get("image") and (d / c["image"]).is_file()]
        progress(f"Gambar {n + 1}/{len(jobs)} · scene {index[sid] + 1}" + (f" kalimat {k + 1}" if k else "")
                 + (f" (referensi: {', '.join(c['name'] for c in cs)})" if refs else "") + "…")
        if refs and has_key():
            img = _edit_with_refs(prompt, refs, W, H, seed, progress)
            how = "referensi karakter"
        else:
            img = _text2img(prompt, W, H, seed, progress)
            how = "teks" + (" (tanpa key: konsistensi terbatas)" if cs else "")
        rel = f"assets/{sid}_story_{seed}.jpg" if k == 0 else f"assets/{sid}_b{k}_story_{seed}.jpg"
        (d / rel).write_bytes(img)
        made += 1

        def apply(q, sid=sid, k=k, rel=rel, how=how, prompt=prompt):
            slot = q["assets"]["scenes"].setdefault(sid, {})
            sel = {"provider": "ai_story", "type": "image", "path": rel, "synthetic": True,
                   "license": "Gambar buatan AI", "author": "AI (Pollinations)", "tags": prompt[:200]}
            qsc = q["assets"]["story"]["scenes"][sid]
            if k == 0:
                slot["selected"] = sel
                slot["pick"] = {"fit": "cocok", "reason": f"🎨 Gambar cerita AI ({how})"}
                slot["fit"] = "center"
                qsc["image"] = rel
            elif k <= len(qsc.get("beats") or []):
                qsc["beats"][k - 1]["image"] = rel
            # potongan bergambar → slot.beats (dibaca render), urut sesuai naskah
            slot["beats"] = [{"w": b["w"], "text": b["text"],
                              "selected": {**sel, "path": b["image"], "tags": b.get("prompt", "")[:200]}}
                             for b in qsc.get("beats") or [] if b.get("image")]
            slot["beats_of"] = qsc.get("beats_of")
            store.touch_stage(q, "assets")
        store.update(pid, apply)
    return {"images": made}
