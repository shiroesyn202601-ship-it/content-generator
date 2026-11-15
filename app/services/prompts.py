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


# ---------------------------------------------------------------- Ide & narasi (meniru tab "Generate Narasi"
# di repo test-google-ai-studio). Template ide bisa diedit kreator di UI; format JSON ditambahkan kode.
IDEA_TEMPLATE = """Buatkan saya {numberOfIdeas} ide judul dan tema untuk video berdurasi {videoDuration} detik dengan topik {viralTopic}.
Hasilkan dalam format JSON dengan struktur berupa array of objects, di mana setiap object memiliki properti berikut:

"nomor" → nomor urut ide (number)

"judul" → judul video yang singkat, padat, jelas, dan clickbait (maksimal sekitar 8–12 kata)

"deskripsi" → penjelasan singkat yang relevan dengan topik video, menggambarkan isi atau pesan utama secara ringkas dan mengundang rasa penasaran penonton

Pastikan:

Judul harus menarik secara emosional, bisa memicu rasa penasaran, kaget, atau ingin tahu lebih lanjut.

Hindari kalimat yang terlalu panjang, ambigu, atau bertele-tele.

Gunakan gaya bahasa natural dan engaging, cocok untuk judul dan deskripsi YouTube Shorts atau video pendek.

Semua hasil dalam bahasa Indonesia."""

TEMPLATE_LABEL = {"fakta": "Fakta Menarik", "tragedi": "Tragedi", "kisah-hidup": "Kisah Hidup", "custom": "Custom"}


def ideas_from_template(template: str, topic: str, count: int, duration: int, existing: list[str],
                        yt_context: list[dict], suggestions: list[str]) -> tuple[str, str]:
    body = ((template or IDEA_TEMPLATE).replace("{numberOfIdeas}", str(count))
            .replace("{videoDuration}", str(duration)).replace("{viralTopic}", topic))
    if existing:
        body += ("\n\nPENTING: Buat ide BARU. Jangan membuat ulang ide dengan judul yang mirip dengan yang ada di "
                 "daftar ini:\n- " + "\n- ".join(existing))
    trend = bool(yt_context or suggestions)
    if trend:
        def line(v):
            s = f"- {v['title']} ({v.get('views') or '?'} views"
            if v.get("views_per_day") is not None:
                s += f", umur {v['age_days']} hari, ±{v['views_per_day']:,.0f} views/hari"
            return s + ")"
        body += "\n\nKonteks riset (video populer di YouTube untuk topik ini — cari angle/celah yang BERBEDA):\n"
        body += "\n".join(line(v) for v in yt_context[:15]) or "(tidak ada)"
        if suggestions:
            body += "\n\nYang sedang dicari orang di YouTube (autocomplete):\n" + "\n".join(f"- {s}" for s in suggestions)
    fmt = ('{"insight": "ringkasan pola konten populer & celah yang bisa diisi", ' if trend else '{') + \
        '"ideas": [{"nomor": 1, "judul": "...", "deskripsi": "...", ' \
        '"hook": "teks hook thumbnail 7-10 kata, clickbait, bikin penasaran, tanpa typo"}]}'
    system = ("Kamu kreator & periset konten YouTube Indonesia yang jago membuat ide viral. "
              f"Balas HANYA JSON object dengan bentuk: {fmt}. Jumlah ide tepat {count}.")
    return system, body


def narration_prompt(template: str, title: str, desc: str, duration: int, wmin: int, wmax: int) -> str:
    """Prompt narasi persis gaya repo referensi; jumlah kata dihitung dari kecepatan suara proyek."""
    topik = f"{title} - {desc}".strip(" -")
    if template == "tragedi":
        return f"""Tulis sebuah narasi tragedi dengan gaya dokumenter jurnalistik.
Gunakan bahasa Indonesia yang padat, lugas, dan berjarak emosional.
Tulisan harus terasa seperti laporan naratif yang bisa dibacakan oleh narator dokumenter,
bukan seperti cerita atau opini pribadi.

🎯 TUJUAN:
Membuat narasi tragedi berdasarkan fakta-fakta yang banyak diberitakan oleh media berita nasional atau internasional.
Narasi harus membangun tensi dan rasa berat melalui data, kronologi, dan fakta nyata — bukan melalui bahasa emosional.

TOPIK:
{topik}


📘 GAYA PENULISAN:
Gunakan nada objektif, tenang, dan dokumenter.
Hindari kalimat puitis, reflektif, atau moralistik.
Gunakan kalimat yang jelas
Gunakan ritme: kalimat panjang (penjelasan) → kalimat pendek (penegasan).
Pilih kata konkret: “terbakar”, “menghantam”, “meledak”, “lenyap”, “tergelincir”.
Hindari kata abstrak seperti “kerapuhan”, “air mata”, atau “harapan”.
Gunakan penanda waktu alami seperti: “pagi itu”, “beberapa menit kemudian”, “tak lama setelah itu”.
Hindari elipsis (...), tanda seru, dan pertanyaan retoris.
Bangun ketegangan melalui urutan fakta, bukan gaya bahasa.

🏗️ STRUKTUR PENULISAN:
Paragraf 1 – Fakta Utama
Kalimat pertama wajib dimulai dengan: “Inilah tragedi [nama tragedi].” dilanjut dengan penjelasan singkat.
Lanjutkan 2–3 kalimat fakta konkret: lokasi, jumlah korban, dan hal unik dari kejadian.
Hindari opini atau emosi.
bagian ini adalah hook jadi buat se-menarik mungkin

Paragraf 2–4 – Kronologi Kejadian
sebutkan waktu kejadian,
Jelaskan peristiwa secara linier dari awal hingga puncak tragedi.
Gunakan waktu spesifik dan alur bergerak maju.
Gunakan kalimat faktual yang menggambarkan urutan dengan jelas.

Paragraf 5 – Dampak dan Korban
Cantumkan jumlah korban tewas, hilang, dan selamat.
Tambahkan fakta unik atau penting (misalnya korban pejabat, warga sipil di lokasi, atau anak-anak).
Hindari kalimat emosional.

Paragraf 6–7 – Penyelidikan atau Kejanggalan
Jelaskan hasil penyelidikan resmi, teori penyebab, atau fakta teknis.
Jika ada versi berbeda, tulis dengan netral: “Laporan lain menyebut...” atau “Penyebab pastinya masih belum jelas.”
Hindari opini atau spekulasi moral.

Paragraf 8 – Penutup
Gunakan satu kalimat pendek dan datar untuk menutup.
Contoh pola: “Hingga kini, tragedi ini masih dikenang sebagai salah satu yang paling kelam dalam sejarah.”
Jangan menambahkan ajakan emosional atau kesimpulan moral.


🧩 ATURAN TAMBAHAN:
Gunakan fakta-fakta yang umum diberitakan di media utama.
Hindari menambah detail yang tidak bisa diverifikasi.
Gunakan tanda titik untuk mengakhiri hampir semua kalimat.
Setiap paragraf maksimal 3–6 kalimat.
Hindari repetisi dan kalimat pengisi.
Pastikan narasi utama berada dalam rentang 1.400–1.700 karakter termasuk spasi, tidak boleh kurang atau lebih.

🎯 FOKUS AKHIR:
Teks harus terasa seperti naskah voice-over dokumenter tragedi.
Nada harus datar, faktual, dan menggugah lewat data, bukan emosi.
Bangun ketegangan perlahan melalui kronologi, dan akhiri dengan kalimat sunyi yang menandakan peringatan."""
    if template == "kisah-hidup":
        return f"""Buatkan saya cerita pendek dari tema: "{topik}". Cerita harus maksimal {wmin}–{wmax} kata, intens, bikin nagih, dan penuh emosi seperti karya manusia yang cocok untuk video pendek, konten storytelling, atau narasi dramatis berdurasi maksimal {duration} detik dengan struktur format:

HOOK (Kalimat pertama – maksimal 2 kalimat) Buat pembuka yang nge-hook, bisa berupa pertanyaan mencurigakan, pernyataan aneh, atau situasi ekstrem.

KONFLIK (isi tengah) Bangun ketegangan secara bertahap. Fokus pada emosi, tindakan dan dilema. Buat pembaca merasa seperti didalam cerita. Gunakan kalimat pendek dan ritme yang cepat jika ingin membangun intensitas.

TWIST Ubah arah cerita dengan cara tak terduga. Hindari ending klise. Lebih baik jika pembaca harus membacanya dua kali untuk benar-benar paham.

CTA (Call to Action & Pertanyaan Interaktif - 1 kalimat di akhir) Ajak penonton untuk berinteraksi. Ini penting untuk meningkatkan engagement. Buat pertanyaan singkat yang memancing diskusi atau komentar terkait cerita.
Contoh: "Gimana menurutmu, apakah Edith pantas jadi pahlawan?" atau "Kira-kira cerita apalagi yang bikin kamu penasaran? Coba komen di bawah!". SUARA PENULIS/NUANSA (optional) Tambahkan gaya bahasa unik, nyeleneh atau puitis untuk memberi “rasa manusia”.

Aturan tambahan: Gunakan bahasa Indonesia yang hidup dan santai. Hindari terlalu baku. Cerita harus punya ketegangan emosional tinggi (tekanan batin, rahasia besar, pertaruhan hidup mati, dll). Gunakan format 3 paragraf atau maksimal 6 baris."""
    return f"""Buatkan naskah video short dengan topik: "{topik}".
Naskah berdurasi {duration} detik dengan jumlah kata {wmin}–{wmax}.

Ketentuan naskah:
- Gaya storytelling singkat, padat, dan memikat sejak awal.
- Gunakan bahasa dramatis, informatif, dan misterius, seolah membuka “tabir rahasia”
- Sertakan kalimat retoris atau pertanyaan yang memancing rasa penasaran.
- Suasana harus intens, bikin nagih, dan penuh emosi seperti karya manusia yang cocok untuk video pendek
- output nya berupa naskah tanpa keterangan apapun

Struktur alur cerita:
1. HOOK: berupa pertanyaan, pernyataan ekstrem, fakta dari hal yang akan di bahas

2. KONFLIK & VALUE:
   Bangun ketegangan secara bertahap. buat pembaca merasa seperti didalam cerita. Gunakan kalimat pendek dan ritme yang cepat, buat supaya pendengar merasa masuk kedalam cerita ini

3. TWIST / RAHASIA LEBIH DALAM: Sajikan plot twist atau lapisan misteri kedua.

4. CALL TO ACTION & Pertanyaan Interaktif - 1 kalimat di akhir
   Ajak penonton untuk berinteraksi. Ini penting untuk meningkatkan engagement.
   HAL YANG WAJIB Hindari mengajak like atau subscribe channel ini
   Buat pertanyaan singkat yang memancing diskusi atau komentar terkait cerita.
   Contoh: "Gimana menurutmu, apakah Edith pantas jadi pahlawan?" atau
   "Kira-kira cerita apalagi yang bikin kamu penasaran? Coba komen di bawah!"""


NARRATION_SYSTEM = ("Kamu penulis naskah voice over untuk kreator YouTube Shiroe Syn. Ikuti instruksi pengguna "
                    "persis. Teks narasi adalah yang DIBACAKAN narator: tanpa judul, label bagian (HOOK/KONFLIK/…), "
                    "nomor, emoji, atau keterangan apa pun. Pisahkan paragraf dengan baris kosong. "
                    'Balas HANYA JSON: {"narasi": "teks narasi lengkap", "hook": "satu kalimat hook thumbnail '
                    'maks 7-10 kata, clickbait, bikin penasaran, tanpa typo, tanpa tanda kutip"}')


def narration_sources(sources_text: str) -> str:
    if not sources_text:
        return ""
    return f"""

SUMBER FAKTA (Wikipedia). Angka, tahun, nama, lokasi, dan superlatif HANYA boleh diambil dari sumber ini.
Kalau sumber tidak menyebutnya, jangan tulis (atau pakai bahasa umum yang pasti benar). Jangan menambah fakta.
---
{sources_text}
---"""


def split_narration(sentences: list[str], title: str, fmt: str, n: str | None = None) -> tuple[str, str]:
    """AI hanya memilih batas scene (nomor kalimat) + anotasi; teks disusun ulang oleh kode → narasi tidak berubah.
    n = rentang jumlah scene (mis. "3-5 scene" untuk satu bab video panjang)."""
    lines = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
    n = n or ("4-9 scene" if fmt == "short" else "10-24 scene")
    system = "Kamu sutradara video pendek. Kamu memecah narasi jadi scene visual & memberi arahan produksi."
    user = f"""Judul video: "{title}". Narasi per KALIMAT bernomor:
{lines}

Bagi narasi menjadi {n} berurutan. Tiap scene = 1-3 kalimat berurutan; "start" = nomor kalimat pertama scene
(scene pertama start 0, start harus naik). JANGAN menulis ulang narasinya.
Untuk tiap scene isi:
- "role": hook, setup, eskalasi, twist, payoff, atau loop.
- "emphasis": 1-2 kata (persis seperti di kalimat) yang paling penting untuk ditekankan di subtitle & intonasi.
- "emotion": salah satu {list(EMOTION_VOICE)}. "mood" musik: salah satu {MOODS}.
- "visual_query": 2-4 kata BAHASA INGGRIS untuk stock footage — apa yang TERLIHAT (benda/tempat/suasana),
  bukan nama tokoh/peristiwa; jaga budaya, lokasi & era topik.
- "sfx": whoosh, boom, ding, riser, click, heartbeat, atau "".
- "on_screen_text": hanya scene pertama, maks 5 kata kapital yang memperkuat hook; scene lain "".
- "sources": [].
Juga ringkas kerangka cerita di "framework" (hook, konflik, eskalasi = twist/rahasia lebih dalam, payoff, cta).
Balas JSON: {{"title": "{title}", "framework": {{"hook": "...", "konflik": "...", "eskalasi": "...", "payoff": "...",
"cta": "..."}}, "scenes": [{{"start": 0, "role": "hook", "emphasis": ["..."], "on_screen_text": "...",
"emotion": "...", "mood": "...", "visual_query": "...", "sfx": "", "sources": []}}]}}"""
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


def video_context(ctx: dict | None, t0: float | None = None, t1: float | None = None) -> str:
    """Blok konteks seluruh video (hasil proofread.py) untuk prompt Clipper. t0/t1 = rentang bagian yang sedang
    dinilai → ditambah "apa yang dibahas sebelumnya" supaya AI tahu posisi bagian ini dalam cerita besar."""
    if not ctx or not (ctx.get("summary") or ctx.get("topic")):
        return ""
    sp = "; ".join(f"{s['name']} ({s['role']})" if s.get("role") else s["name"]
                   for s in ctx.get("speakers") or [] if s.get("name"))
    out = ["KONTEKS SELURUH VIDEO (pahami dulu sebelum memilih):"]
    if ctx.get("topic"):
        out.append(f"- Topik: {ctx['topic']}" + (f" ({ctx['format']})" if ctx.get("format") else ""))
    if ctx.get("summary"):
        out.append(f"- Alur: {ctx['summary']}")
    if sp:
        out.append(f"- Pembicara: {sp}")
    ol = ctx.get("outline") or []
    if t0 is not None and ol:
        prev = [o for o in ol if o["end"] <= t0 + 2][-2:]
        here = [o for o in ol if o["end"] > t0 and o["start"] < (t1 if t1 is not None else t0)]
        if prev:
            out.append("- Sebelum bagian ini: " + " ".join(o["summary"] for o in prev))
        if here:
            out.append("- Bagian ini membahas: " + " ".join(o["summary"] for o in here))
    return "\n".join(out) + "\n"


def viral_clips(units: list[dict], fmt: str, lang: str, title: str, offset: int = 0, count: int = 5,
                direction: str = "", limits: tuple | None = None, context: dict | None = None) -> tuple[str, str]:
    """Rubrik penilaian klip (diadaptasi dari Anil-matcha & openshorts). Satuan = kalimat bernomor,
    jadi AI memilih nomor kalimat, bukan menebak detik."""
    lo, hi = limits or ((20, 59) if fmt == "short" else (90, 600))
    # ringkas (hemat token): nomor + menit:detik mulai; durasi klip dihitung ulang oleh kode
    lines = "\n".join(f"[{i + offset}] {int(s['start'] // 60)}:{int(s['start'] % 60):02d} {s['text']}{s.get('sig', '')}"
                      for i, s in enumerate(units))
    sig = [x for x in ("🔥", "🔊") if any(x in (u.get("sig") or "") for u in units)]
    tanda = ("\nTANDA dari data nyata (bukan tebakan): "
             + ("🔥 = bagian yang paling sering DIPUTAR ULANG penonton YouTube; " if "🔥" in sig else "")
             + ("🔊 = suara tiba-tiba keras/heboh (tawa, teriak, debat); " if "🔊" in sig else "")
             + "utamakan momen di sekitar tanda ini bila isinya memang kuat.\n") if sig else ""
    system = ("Kamu editor klip viral profesional untuk YouTube Shorts/TikTok. Kamu jujur menilai: "
              "kebanyakan bagian video BIASA saja, hanya sedikit yang benar-benar layak diklip.")
    arah = f"\nARAHAN KREATOR (utamakan ini): {direction.strip()}\n" if direction.strip() else ""
    ctx = video_context(context, units[0]["start"], units[-1]["end"]) if units else ""
    user = f"""Video sumber: "{title}".
{ctx}
Transkrip (sudah dikoreksi) per KALIMAT bernomor ([nomor] menit:detik mulai):
{lines}
{arah}{tanda}
Pilih sampai {count} momen terbaik. Setiap klip:
- mulai di kalimat "start_unit" dan berakhir di kalimat "end_unit" (nomor di kurung siku), durasi {lo}-{hi} detik (hitung dari waktu mulai kalimat);
- kalimat PERTAMA harus bisa dipahami tanpa konteks sebelumnya (jangan mulai dengan "jadi", "nah itu", "makanya",
  "dia" yang merujuk hal sebelumnya) dan langsung memancing rasa penasaran;
- kalimat TERAKHIR terasa tuntas (punchline, kesimpulan, jawaban) — jangan berhenti di tengah cerita;
- klip harus masuk akal bagi penonton yang TIDAK menonton video aslinya. Pahami siapa bicara & apa yang sedang
  dibahas: bila momen kuat merujuk hal sebelumnya ("itu", "dia", "tadi"), mulai lebih awal agar rujukannya ikut,
  atau lewati momen itu. Pertanyaan + jawabannya harus ada dalam satu klip.

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


def rerank_clips(cands: list[dict], units: list[dict], title: str, lang: str, direction: str = "",
                 context: dict | None = None) -> tuple[str, str]:
    """Babak final: SEMUA kandidat dibandingkan berdampingan oleh satu model → skor setara antar-bagian video
    (tiap bagian tadinya dinilai model berbeda, jadi skor mentahnya tidak bisa dibandingkan langsung)."""
    def txt(c):
        body = " ".join(u["text"] for u in units if u["start"] >= c["start"] - 0.5 and u["end"] <= c["end"] + 0.5)
        return body if len(body) <= 700 else body[:450] + " … " + body[-220:]
    rows = "\n\n".join(f"#{i} ({c['end'] - c['start']:.0f} dtk) judul: {c['title']}\n{txt(c)}" for i, c in enumerate(cands))
    arah = f"\nARAHAN KREATOR: {direction.strip()}" if direction.strip() else ""
    system = "Kamu kurator Shorts/TikTok yang sangat selektif. Bandingkan kandidat secara berdampingan dan jujur."
    user = f"""Video sumber: "{title}".
{video_context(context)}
Berikut {len(cands)} kandidat klip (#nomor, durasi, judul, isi ucapan):

{rows}
{arah}
Beri tiap kandidat skor 0-100: seberapa besar peluangnya ditonton sampai habis & dibagikan, DIBANDINGKAN kandidat lain
(hook 3 detik pertama, cerita utuh & tuntas, emosi/humor/insight, bisa dipahami tanpa menonton video aslinya).
Sebar skornya (yang terbaik 85+, yang biasa < 50). Bahasa alasan: {LANG_NAME.get(lang, lang)}.
Balas JSON: {{"ranking": [{{"n": 0, "score": 80, "why": "alasan singkat"}}]}}"""
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
- JANGAN menulis timestamp / daftar chapter (ditambahkan otomatis dari timing video).
Balas JSON: {{"title": "...", "description": "...", "tags": ["..."], "pinned_comment": "..."}}"""
    return system, user
