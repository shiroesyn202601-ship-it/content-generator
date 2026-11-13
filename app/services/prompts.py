"""Template prompt. Semua prompt meminta JSON dengan skema tetap agar kompatibel
dengan format kanonik di store.py."""
import json

from ..store import EMOTION_VOICE, MOODS

PERSONA = ("Kamu adalah penulis konten YouTube untuk kreator bernama Shiroe Syn. "
           "Gaya: hook kuat di 3 detik pertama, bahasa lisan yang natural, tidak bertele-tele.")

LANG_NAME = {"id": "Bahasa Indonesia", "en": "English"}


def ideas(keyword: str, fmt: str, lang: str, yt_context: list[dict]) -> tuple[str, str]:
    ctx = "\n".join(f"- {v['title']} ({v.get('views') or '?'} views)" for v in yt_context[:15])
    durasi = "video pendek 30-60 detik (Shorts)" if fmt == "short" else "video panjang 6-10 menit"
    system = PERSONA + " Kamu juga riset tren dan menganalisis celah konten."
    user = f"""Topik/kata kunci: "{keyword}"
Format: {durasi}. Bahasa: {LANG_NAME.get(lang, lang)}.
Judul video populer yang sudah ada untuk topik ini (konteks riset):
{ctx or "(tidak ada data)"}

Buat 6 ide konten yang BERBEDA dari yang sudah ada (cari angle/celah baru).
Balas JSON:
{{"insight": "ringkasan pola konten populer & celah yang bisa diisi",
  "ideas": [{{"title": "judul menarik", "angle": "sudut pandang unik", "hook": "kalimat pembuka 3 detik",
             "keywords": ["kata kunci SEO"], "target_audience": "siapa penontonnya", "potential": 1-10}}]}}"""
    return system, user


def script(idea: dict, fmt: str, lang: str, preset: str) -> tuple[str, str]:
    n = "5-8 scene, total 90-150 kata" if fmt == "short" else "12-20 scene, total 900-1400 kata"
    system = PERSONA + " Kamu ahli struktur cerita (hook - konflik - eskalasi - payoff - CTA)."
    user = f"""Ide terpilih:
{json.dumps(idea, ensure_ascii=False, indent=2)}

Tulis naskah voice over lengkap, {n}, bahasa {LANG_NAME.get(lang, lang)}, karakter suara "{preset}".
Aturan:
- Pecah per scene. Setiap scene = 1-3 kalimat yang dibacakan narator.
- Beri PENANDA EMOSI tiap scene di field "emotion", pilih dari: {list(EMOTION_VOICE)}.
- Beri suasana musik tiap scene di field "mood", pilih dari: {MOODS}.
- Tandai 1-2 kata kunci terpenting per scene dengan *bintang* (contoh: "ini *rahasia* terbesar") —
  kata ini akan ditekankan di subtitle & intonasi.
- "visual_query": 2-4 kata BAHASA INGGRIS untuk mencari stock footage di Pixabay/Pexels. Tulis APA YANG TERLIHAT
  di layar (benda/tempat/suasana umum), bukan nama tokoh/kerajaan/peristiwa — stok footage tidak punya itu.
  Jaga kesesuaian budaya & lokasi topik (mis. topik Jawa kuno → "javanese temple ruins", bukan "rome").
  Contoh baik: "ancient stone temple", "old bronze cannon", "rice field aerial". Contoh buruk: "majapahit capital".
- "sfx": nama efek suara singkat bahasa Inggris (whoosh, boom, ding, riser, click, heartbeat) atau "" jika tidak perlu.
- Tulis angka & singkatan sebagaimana diucapkan bila rawan salah baca.
Balas JSON:
{{"title": "...", "framework": {{"hook": "...", "konflik": "...", "eskalasi": "...", "payoff": "...", "cta": "..."}},
  "scenes": [{{"text": "...", "emotion": "...", "mood": "...", "visual_query": "...", "sfx": "..."}}]}}"""
    return system, user


FOOTAGE_RULES = """Aturan kata kunci stok footage (Pixabay/Pexels, BAHASA INGGRIS, 1-4 kata):
- Tulis apa yang TERLIHAT: benda, tempat, suasana, aksi. Stok footage tidak punya nama tokoh/kerajaan/peristiwa
  spesifik, jadi terjemahkan ke visual umumnya (mis. "kerajaan Majapahit" → "javanese temple ruins").
- Jaga budaya & lokasi topik. Topik Indonesia → pakai kata seperti indonesia, java, bali, javanese,
  southeast asia, tropical. JANGAN memancing visual budaya lain (rome, japan, kyoto, egypt) kecuali memang dibahas.
- Jaga ERA: topik sejarah/kuno → visual kuno (temple, stone relief, ruins, old artifact), jangan memancing
  visual modern (traffic, construction, sewer, office).
- Objek yang sangat spesifik sering tidak ada di stok. Kata kunci ke-3 harus b-roll SUASANA yang pasti ada dan
  tetap sesuai era & lokasi (mis. "javanese temple", "stone temple relief", "tropical jungle river", "rice terrace").
- Untuk ajakan subscribe/like → "subscribe button" atau visual penutup yang netral."""


def footage_queries(topic: str, scenes: list[dict]) -> tuple[str, str]:
    lines = "\n".join(f'[{s["id"]}] narasi: {s["text"].replace("*", "")} | kata kunci lama: {s.get("visual_query", "")}'
                      for s in scenes)
    system = "Kamu editor video yang ahli mencari stock footage yang cocok dengan narasi."
    user = f"""Topik video: "{topic}"
Scene:
{lines}

Untuk SETIAP scene, buat 3 kata kunci pencarian berurutan dari paling spesifik → paling umum (yang umum harus
pasti ada di situs stok, mis. "ancient temple", "river water", "fish pond").
{FOOTAGE_RULES}
Selain itu, buat daftar "avoid": 10-25 tag stok footage (1 kata, bahasa Inggris, huruf kecil) yang PASTI salah
untuk topik ini secara era/budaya/lokasi — mis. untuk sejarah Jawa kuno: traffic, urban, skyscraper, office,
construction, excavator, sewer, pollution, wastewater, car, japan, kyoto, rome, italy, egypt, paris.
Jangan masukkan kata yang mungkin dibutuhkan narasi.
Balas JSON: {{"avoid": ["..."], "scenes": [{{"id": "s1", "queries": ["...", "...", "..."]}}]}}"""
    return system, user


def pick_footage(topic: str, items: list[dict]) -> tuple[str, str]:
    """items: [{id, text, candidates: [{i, tags, orientation, duration}]}]"""
    blocks = []
    for it in items:
        cands = "\n".join(f'   ({c["i"]}) {c["orientation"]}, {c.get("duration") or "?"}s — tag: {c["tags"] or "(tanpa tag)"}'
                          for c in it["candidates"])
        blocks.append(f'[{it["id"]}] narasi: {it["text"]}\n{cands or "   (tidak ada kandidat)"}')
    system = "Kamu editor video yang teliti. Kamu memilih stok footage yang paling cocok dengan narasi."
    user = f"""Topik video: "{topic}"
Untuk setiap scene, pilih SATU kandidat yang isinya paling cocok dengan narasi, dinilai dari tag-nya.
Nilai dengan 3 syarat: (1) MAKNA sesuai narasi, (2) BUDAYA/LOKASI sesuai topik (topik Indonesia jangan diberi
visual Jepang/Eropa/Mesir kecuali narasinya menyebut itu), (3) ERA sesuai (topik sejarah/kuno jangan diberi visual
modern: lalu lintas, gedung kota, alat berat, limbah/polusi, kantor, orang berpakaian modern).
- "cocok"  = memenuhi ketiga syarat.
- "lumayan" = suasana/era/lokasi pas walau objeknya tidak persis (b-roll suasana yang aman).
- "lemah"  = melanggar salah satu syarat. Lebih baik pilih b-roll suasana yang pas era & lokasi
  daripada objek yang benar tapi modern/budaya lain.
- Bersikap KETAT; jangan memberi "cocok" hanya karena satu kata tag sama.
- "reason": alasan singkat bahasa Indonesia (maks 15 kata).

{chr(10).join(blocks)}

Balas JSON: {{"picks": [{{"id": "s1", "index": 0, "fit": "cocok", "reason": "..."}}]}}"""
    return system, user


def viral_clips(segments: list[dict], fmt: str, lang: str, title: str) -> tuple[str, str]:
    lo, hi = (20, 59) if fmt == "short" else (90, 600)
    lines = "\n".join(f"[{i}] {s['start']:.1f}-{s['end']:.1f}: {s['text']}" for i, s in enumerate(segments))
    system = "Kamu editor klip viral profesional. Kamu memilih momen paling menarik dari transkrip."
    user = f"""Video sumber: "{title}". Transkrip bernomor (detik):
{lines}

Pilih 3-6 momen paling berpotensi viral. Setiap klip HARUS:
- dimulai di awal segmen dan berakhir di akhir segmen (pakai nomor segmen, jangan memotong kalimat),
- berdurasi {lo}-{hi} detik, punya hook di awal dan terasa selesai di akhir.
Balas JSON:
{{"clips": [{{"start_seg": 0, "end_seg": 5, "title": "judul klip", "hook": "teks hook di layar",
             "reason": "kenapa menarik", "score": 1-10, "keywords": ["kata kunci untuk ditekankan"]}}]}}
Bahasa judul/hook: {LANG_NAME.get(lang, lang)}."""
    return system, user


def metadata(p: dict) -> tuple[str, str]:
    if p["mode"] == "scratch":
        body = " ".join(s["text"].replace("*", "") for s in p["script"]["scenes"])
    else:
        clip = p["script"]["clips"][p["script"]["selected_clip"]]
        body = clip.get("title", "") + " — " + " ".join(
            w["text"] for w in p["subtitles"].get("words", [])[:400])
    system = "Kamu spesialis YouTube SEO."
    user = f"""Buat metadata YouTube untuk video {'Shorts' if p['format'] == 'short' else 'panjang'} berikut.
Judul kerja: {p['title']}
Isi: {body[:6000]}
Bahasa: {LANG_NAME.get(p['language'], p['language'])}.
Balas JSON: {{"title": "maks 90 karakter, bikin penasaran", "description": "3-5 paragraf pendek + hashtag",
"tags": ["maks 15 tag"]}}"""
    return system, user
