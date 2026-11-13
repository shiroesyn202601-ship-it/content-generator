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
- "visual_query": 2-4 kata BAHASA INGGRIS untuk mencari stock footage (konkret & visual, mis. "city night rain").
- "sfx": nama efek suara singkat bahasa Inggris (whoosh, boom, ding, riser, click, heartbeat) atau "" jika tidak perlu.
- Tulis angka & singkatan sebagaimana diucapkan bila rawan salah baca.
Balas JSON:
{{"title": "...", "framework": {{"hook": "...", "konflik": "...", "eskalasi": "...", "payoff": "...", "cta": "..."}},
  "scenes": [{{"text": "...", "emotion": "...", "mood": "...", "visual_query": "...", "sfx": "..."}}]}}"""
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
