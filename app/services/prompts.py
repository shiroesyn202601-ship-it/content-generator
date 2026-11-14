"""Template prompt. Semua prompt meminta JSON dengan skema tetap agar kompatibel
dengan format kanonik di store.py."""
import json

from ..store import EMOTION_VOICE, MOODS

PERSONA = ("Kamu adalah penulis konten YouTube untuk kreator bernama Shiroe Syn. "
           "Gaya: hook kuat di 3 detik pertama, bahasa lisan yang natural, tidak bertele-tele.")

LANG_NAME = {"id": "Bahasa Indonesia", "en": "English"}


def ideas(keyword: str, fmt: str, lang: str, yt_context: list[dict], suggestions: list[str] | None = None) -> tuple[str, str]:
    def line(v):
        s = f"- {v['title']} ({v.get('views') or '?'} views"
        if v.get("views_per_day") is not None:
            s += f", umur {v['age_days']} hari, ±{v['views_per_day']:,.0f} views/hari"
        return s + ")"
    ctx = "\n".join(line(v) for v in yt_context[:15])
    if suggestions:
        ctx += "\n\nYang sedang dicari orang di YouTube (autocomplete):\n" + "\n".join(f"- {s}" for s in suggestions)
        ctx += ("\n\nCatatan: views/hari tinggi + umur muda = topik sedang NAIK (prioritaskan angle di sekitarnya). "
                "Saran pencarian = kata kunci yang dicari penonton.")
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


FACT_RULES = """ATURAN AKURASI (WAJIB):
- Hanya tulis fakta yang kamu yakini benar & umum diketahui. JANGAN mengarang angka, tahun, nama penemu,
  lokasi temuan, atau superlatif ("terbesar", "pertama", "satu-satunya") yang tidak kamu yakini.
- Kalau ragu, pakai bahasa ragu yang jujur ("diperkirakan", "menurut catatan sejarah", "konon") atau
  ganti dengan pernyataan umum yang pasti benar. Lebih baik kurang dramatis daripada salah fakta."""


def sources_block(sources_text: str) -> str:
    if not sources_text:
        return ""
    return f"""SUMBER FAKTA (Wikipedia). Semua angka, tahun, nama, lokasi, dan superlatif HANYA boleh diambil dari sumber
ini. Kalau sumber tidak menyebutnya, JANGAN tulis (atau pakai bahasa umum yang pasti benar). Boleh menyederhanakan
bahasa, tapi jangan menambah fakta. Isi field "sources" tiap scene dengan id sumber yang dipakai (mis. ["S1"]).
---
{sources_text}
---"""


def factcheck(scenes: list[dict], sources_text: str) -> tuple[str, str]:
    lines = "\n".join(f'[{s["id"]}] {s["text"].replace("*", "")}' for s in scenes)
    system = "Kamu pemeriksa fakta yang teliti dan skeptis untuk konten edukasi."
    user = f"""Periksa setiap KLAIM FAKTUAL di naskah (angka, tahun, nama, lokasi, sebab-akibat, superlatif seperti
"terbesar/pertama/satu-satunya/lebih maju dari"). Abaikan kalimat opini/ajakan.
Bandingkan dengan SUMBER. Untuk tiap klaim tentukan "verdict":
- "didukung"  : sumber menyatakan hal yang sama (kutip singkat di "quote").
- "tanpa_sumber": sumber tidak menyebutnya (belum tentu salah, tapi tidak bisa diverifikasi).
- "bertentangan": sumber menyatakan hal berbeda.
Superlatif & perbandingan ("lebih maju dari Roma") tanpa kutipan sumber = "tanpa_sumber".
Beri "fix": usulan kalimat pengganti yang aman (bahasa Indonesia), kosongkan bila "didukung".

NASKAH:
{lines}

SUMBER:
{sources_text or "(tidak ada sumber)"}

Balas JSON: {{"claims": [{{"scene_id": "s1", "claim": "...", "verdict": "didukung", "source_id": "S1",
"quote": "...", "fix": ""}}]}}"""
    return system, user


def script(idea: dict, fmt: str, lang: str, preset: str, max_words: int | None = None,
           sources_text: str = "") -> tuple[str, str]:
    if fmt == "short":
        mw = max_words or 110
        n = (f"5-8 scene, TOTAL {int(mw * 0.8)}-{mw} kata (±45-55 detik). Lebih dari {mw} kata melewati batas "
             f"60 detik Shorts; kurang dari {int(mw * 0.8)} kata terlalu pendek & tipis isinya")
    else:
        mw = max_words or 1100
        n = f"12-20 scene, total sekitar {int(mw * 0.8)}-{mw} kata"
    system = PERSONA + " Kamu ahli struktur cerita (hook - konflik - eskalasi - payoff - CTA)."
    user = f"""Ide terpilih:
{json.dumps(idea, ensure_ascii=False, indent=2)}

Tulis naskah voice over lengkap, {n}, bahasa {LANG_NAME.get(lang, lang)}, karakter suara "{preset}".
{FACT_RULES}
{sources_block(sources_text)}
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
{SHORTS_RETENTION if fmt == "short" else LONG_RETENTION}
- "role" tiap scene, pilih dari: hook, setup, eskalasi, twist, payoff, loop.
Balas JSON:
{{"title": "...", "framework": {{"hook": "...", "konflik": "...", "eskalasi": "...", "payoff": "...", "cta": "..."}},
  "scenes": [{{"role": "hook", "text": "...", "on_screen_text": "MAKS 5 KATA (hanya scene 1, lainnya \\"\\")",
              "emotion": "...", "mood": "...", "visual_query": "...", "sfx": "...", "sources": ["S1"]}}]}}"""
    return system, user


SHORTS_RETENTION = """Struktur retensi Shorts (penonton memutuskan swipe dalam 1-3 detik pertama):
- Scene 1 = HOOK: maks 12 kata, LANGSUNG ke klaim/pertanyaan paling mengejutkan. DILARANG salam/perkenalan
  ("halo guys", "di video ini", "tahukah kamu" yang hambar). Isi "on_screen_text" scene 1 dengan maks 5 kata
  yang memperkuat hook (bukan menyalin kalimatnya).
- Tiap ±2 scene beri pola interupsi: pertanyaan retoris, angka konkret, kontras ("tapi…", "ternyata…").
- Kalimat pendek-pendek (≤ 15 kata), ritme cepat, satu ide per kalimat.
- JANGAN akhiri dengan ajakan subscribe panjang. Scene terakhir = payoff singkat yang menyambung ke kalimat
  pertama supaya video terasa looping saat diputar ulang (role "loop"). Ajakan subscribe, bila ada, cukup
  2-4 kata di scene payoff."""

LONG_RETENTION = """Struktur retensi video panjang:
- Scene 1 = hook kuat tanpa salam panjang; janjikan apa yang akan didapat penonton. Isi "on_screen_text" scene 1.
- Buka "loop rasa penasaran" di awal yang baru dijawab menjelang akhir; beri pola interupsi berkala.
- CTA singkat menjelang akhir."""


def shorten_script(res: dict, max_words: int) -> tuple[str, str]:
    system = "Kamu editor naskah video pendek. Kamu memadatkan naskah tanpa mengubah maknanya."
    user = f"""Naskah berikut terlalu panjang. Padatkan menjadi TOTAL MAKSIMAL {max_words} kata
(hitung semua field "text"). Pertahankan hook pembuka, urutan cerita, penanda *bintang*, dan semua field lain
(role, on_screen_text, emotion, mood, visual_query, sfx). Boleh menggabung atau membuang scene yang kurang penting.
Jangan menambah fakta baru.

{json.dumps({"scenes": res.get("scenes", [])}, ensure_ascii=False)}

Balas JSON: {{"scenes": [{{"role": "...", "text": "...", "on_screen_text": "...", "emotion": "...", "mood": "...",
"visual_query": "...", "sfx": "..."}}]}}"""
    return system, user


def expand_script(res: dict, min_words: int, max_words: int, sources_text: str = "") -> tuple[str, str]:
    system = "Kamu editor naskah video pendek. Kamu mengembangkan naskah supaya lebih berisi tanpa mengarang."
    user = f"""Naskah berikut terlalu pendek. Kembangkan menjadi TOTAL {min_words}-{max_words} kata (hitung semua
field "text"): tambah penjelasan, contoh, atau konteks yang membuat penonton paham & penasaran. Boleh menambah
1-3 scene. Pertahankan hook, struktur (role), penanda *bintang*, dan semua field lain.
{FACT_RULES}
{sources_block(sources_text)}

{json.dumps({"scenes": res.get("scenes", [])}, ensure_ascii=False)}

Balas JSON: {{"scenes": [{{"role": "...", "text": "...", "on_screen_text": "...", "emotion": "...", "mood": "...",
"visual_query": "...", "sfx": "..."}}]}}"""
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
Untuk scene yang menyebut OBJEK/TEMPAT/TOKOH NYATA yang mungkin ada fotonya (artefak, situs, candi, peta kuno,
lukisan), isi "commons_query": 2-4 kata bahasa Inggris untuk mencari foto asli di Wikimedia Commons — di sini
nama asli BOLEH (mis. "Majapahit cannon", "Trowulan ruins", "Borobudur relief"). Selain itu isi "".
Balas JSON: {{"avoid": ["..."], "scenes": [{{"id": "s1", "queries": ["...", "...", "..."], "commons_query": ""}}]}}"""
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


def viral_clips(units: list[dict], fmt: str, lang: str, title: str, offset: int = 0, count: int = 5,
                direction: str = "") -> tuple[str, str]:
    """Rubrik penilaian klip (diadaptasi dari Anil-matcha & openshorts). Satuan = kalimat bernomor,
    jadi AI memilih nomor kalimat, bukan menebak detik."""
    lo, hi = (20, 59) if fmt == "short" else (90, 600)
    lines = "\n".join(f"[{i + offset}] {s['start']:.1f}-{s['end']:.1f}: {s['text']}" for i, s in enumerate(units))
    system = ("Kamu editor klip viral profesional untuk YouTube Shorts/TikTok. Kamu jujur menilai: "
              "kebanyakan bagian video BIASA saja, hanya sedikit yang benar-benar layak diklip.")
    arah = f"\nARAHAN KREATOR (utamakan ini): {direction.strip()}\n" if direction.strip() else ""
    user = f"""Video sumber: "{title}". Transkrip per KALIMAT bernomor (detik):
{lines}
{arah}
Pilih sampai {count} momen terbaik. Setiap klip:
- mulai di kalimat "start_unit" dan berakhir di kalimat "end_unit" (nomor di kurung siku), durasi {lo}-{hi} detik;
- kalimat PERTAMA harus bisa dipahami tanpa konteks sebelumnya (jangan mulai dengan "jadi", "nah itu", "makanya",
  "dia" yang merujuk hal sebelumnya) dan langsung memancing rasa penasaran;
- kalimat TERAKHIR terasa tuntas (punchline, kesimpulan, jawaban) — jangan berhenti di tengah cerita.

Nilai tiap klip 0-100 (jujur; 80+ hanya untuk yang benar-benar kuat):
- "hook": seberapa kuat 3 detik pertama menahan penonton (pertanyaan, klaim mengejutkan, konflik, emosi);
- "alur": cerita utuh — ada awal, puncak, dan akhir yang tuntas;
- "nilai": puncak emosi, opini tajam, pengungkapan, humor, kalimat yang layak dikutip, atau tips praktis;
- "bagikan": seberapa besar orang ingin mengomentari / membagikan / relevan dengan tren.
"title": judul klip menarik (maks 60 karakter). "hook": teks pendek di layar untuk 2 detik pertama (maks 8 kata).
"reason": 1 kalimat kenapa klip ini kuat. "keywords": 2-5 kata yang diucapkan di klip untuk ditekankan di subtitle.
Bahasa judul/hook/alasan: {LANG_NAME.get(lang, lang)}.
Balas JSON: {{"clips": [{{"start_unit": 0, "end_unit": 5, "title": "...", "hook": "...", "reason": "...",
  "scores": {{"hook": 70, "alur": 60, "nilai": 65, "bagikan": 55}}, "keywords": ["..."]}}]}}"""
    return system, user


def metadata(p: dict) -> tuple[str, str]:
    if p["mode"] == "scratch":
        body = " ".join(s["text"].replace("*", "") for s in p["script"]["scenes"])
    else:
        clip = p["script"]["clips"][p["script"]["selected_clip"]]
        body = clip.get("title", "") + " — " + " ".join(
            w["text"] for w in p["subtitles"].get("words", [])[:400])
    short = p["format"] == "short"
    system = "Kamu spesialis YouTube SEO."
    user = f"""Buat metadata YouTube untuk video {'Shorts' if short else 'panjang'} berikut.
Judul kerja: {p['title']}
Isi: {body[:6000]}
Bahasa: {LANG_NAME.get(p['language'], p['language'])}.
Aturan:
- Judul {'maks 60 karakter' if short else 'maks 70 karakter'}, kata kunci utama di DEPAN, bikin penasaran tapi
  JUJUR sesuai isi (bukan clickbait menyesatkan). Jangan pakai karakter < atau >.
- Baris pertama deskripsi = hook + kata kunci utama (yang tampil sebelum "lainnya").
- Deskripsi {'2-3 paragraf pendek' if short else '3-5 paragraf'}; akhiri dengan 3-5 hashtag relevan
  (3 pertama tampil di atas judul). JANGAN lebih dari 15 hashtag.
- "tags": 8-15 tag, campuran kata kunci umum & spesifik, total < 450 karakter.
- "pinned_comment": 1 kalimat pertanyaan untuk memancing komentar.
Balas JSON: {{"title": "...", "description": "...", "tags": ["..."], "pinned_comment": "..."}}"""
    return system, user
