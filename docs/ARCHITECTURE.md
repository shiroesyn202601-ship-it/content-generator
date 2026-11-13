# Arsitektur — Shiroe Syn Content Studio

## 1. Tech stack (semuanya gratis untuk dipakai harian)

| Lapisan | Pilihan | Alasan |
|---|---|---|
| Web UI | HTML + JavaScript biasa (tanpa build step), disajikan FastAPI | Tidak perlu Node/npm, cukup buka browser |
| Backend | Python 3.11 + FastAPI + Uvicorn | Semua tool AI/video terbaik ada di ekosistem Python |
| LLM | Gemini free tier / Groq free tier / Ollama lokal (bisa diganti per aksi) | Biaya nol; Ollama untuk 100% offline |
| TTS | **Edge-TTS** (suara neural Microsoft, termasuk `id-ID-ArdiNeural` & `id-ID-GadisNeural`) | Gratis, ada timestamp per kata (WordBoundary) |
| Speech-to-text | **faster-whisper** (Whisper versi CTranslate2) | Lokal, cepat di CPU, timestamp per kata |
| Download video | **yt-dlp** | Standar de-facto, juga dipakai untuk riset judul populer |
| Stock footage | Pexels API + Pixabay API | Gratis, lisensi bebas pakai |
| Editing/render | **FFmpeg** (filter graph + libass) | Lebih cepat & stabil daripada MoviePy untuk pipeline otomatis |
| Database | Google Sheets API | Gratis, bisa dibaca/diedit manual |
| Penyimpanan file | Google Drive API (15 GB gratis) | Backup proyek, VO, hasil render, library musik/SFX |
| Upload & jadwal | YouTube Data API v3 (`videos.insert` + `status.publishAt`) | Penjadwalan dikerjakan server YouTube |

**Kenapa dijalankan lokal (localhost)?** Whisper + FFmpeg butuh CPU dan disk besar; hosting gratis
(Render/Vercel/HF Spaces) membatasi RAM, durasi proses, dan penyimpanan. Aplikasi tetap berbasis web —
bisa dibuka dari HP di jaringan yang sama dengan `HOST=0.0.0.0`.

## 2. Alur data

```mermaid
flowchart TD
    subgraph J1[Jalur 1 · Dari nol]
      A1[1. Kata kunci] -->|yt-dlp ytsearch + LLM| B1[ideas[] → pilih 1]
      B1 -->|LLM + penanda emosi/mood/*penekanan*| C1[scenes[]]
      C1 -->|Pexels/Pixabay per visual_query| D1[assets/sX.mp4]
      C1 -->|Edge-TTS per kalimat + kamus fonetik| E1[audio/vo.wav + words[] + scene_timings[]]
    end
    subgraph J2[Jalur 2 · Clipper]
      A2[1. Link YouTube / unggah] -->|yt-dlp| B2[source/source.mp4]
      B2 -->|faster-whisper word_timestamps| C2[transcript.segments[].words[]]
      C2 -->|LLM pilih segmen + snap ke batas kalimat| D2[clips[] → pilih 1]
      D2 -->|FFmpeg potong + crop 9:16/blur| E2[assets/clip.mp4]
    end
    E1 --> F[5. words[] → lines[] (editor) → subs.ass karaoke]
    C2 --> F
    E1 --> G[Musik per mood + SFX dari Drive/lokal]
    E2 --> G
    F --> H[6. FFmpeg: visual + VO + musik ducking + SFX + burn-in → render/final.mp4]
    G --> H
    D1 --> H
    E2 --> H
    H --> I[7. LLM metadata → YouTube publishAt]
    H -.-> DR[(Google Drive)]
    I -.-> SH[(Google Sheets)]
```

### Gerbang Human-in-the-Loop
Setiap tahap punya status `pending → draft → approved`, plus `stale`.
- **Edit Manual**: lewat form khusus tahap ATAU editor JSON mentah (kontrol penuh).
- **Setujui & Lanjut**: validasi minimum (mis. semua scene punya visual), membuka tahap berikutnya,
  lalu menyinkronkan ke Sheets/Drive di background.
- Mengubah tahap yang sudah lewat → tahap sesudahnya ditandai **stale** (data tidak dihapus) dan harus disetujui ulang.
  Ini mencegah video dirender dari data yang sudah tidak cocok.

## 3. Format data kanonik (kunci kompatibilitas antar-modul)

Semua modul hanya berbicara lewat format ini — ini yang membuat logika dari berbagai proyek open-source bisa digabung.

```jsonc
// Word — dipakai oleh TTS (Edge WordBoundary), Whisper, editor subtitle, dan generator ASS
{"text": "rahasia", "start": 3.42, "end": 3.88, "emphasis": true}

// Scene — output LLM tahap 2, input aset/TTS/musik
{"id": "s3", "text": "Ini *rahasia* terbesar.", "emotion": "tegang", "mood": "misteri",
 "visual_query": "dark ancient temple", "sfx": "whoosh"}

// Clip — output LLM + snap Whisper
{"id": "c1", "start": 61.2, "end": 104.9, "title": "...", "hook": "...", "score": 9, "keywords": ["..."]}

// Line subtitle — i0/i1 menunjuk ke indeks words[]
{"start": 3.1, "end": 4.6, "text": "ini *rahasia* terbesar", "i0": 12, "i1": 14}
```
- Waktu selalu **detik (float) relatif terhadap awal media tahap itu** (VO untuk jalur 1, klip untuk jalur 2).
  Edge-TTS memberi satuan 100 ns → dikonversi di `tts.py`; Whisper memberi detik absolut sumber → digeser `-clip_start` di `subtitles.py`.
- Path file di JSON selalu **relatif terhadap folder proyek** (`data/projects/<id>/`), sehingga proyek bisa dibackup/pulihkan dari Drive.
- Penanda penekanan sama di naskah, subtitle, dan TTS: `*kata*`.

## 4. Skema Google Sheets ("Shiroe Syn Studio DB")

| Tab | Kolom | Isi |
|---|---|---|
| **Projects** | project_id, created_at, updated_at, mode, format, title, current_stage, stage_status, keyword_or_source, drive_folder_id, project_json_drive_id, final_video_drive_id, youtube_video_id, publish_at, privacy, notes | 1 baris per proyek (upsert) |
| **Scripts** | project_id, order, scene_id, emotion, mood, text, visual_query, sfx, start, end | 1 baris per scene (jalur 1) |
| **Assets** | project_id, scene_or_clip, type, provider, author, license, page_url, local_path, logged_at | Jejak lisensi setiap aset |
| **Schedule** | project_id, title, publish_at, privacy, status, youtube_video_id, url, uploaded_at, error | Kalender publikasi |
| **Pronunciation** | term, phonetic, note | Kamus fonetik global (bisa diedit langsung di Sheets) |

Data berat (transkrip, timestamp kata) tidak muat di sel Sheets (batas 50.000 karakter), jadi
disimpan di `project.json` yang di-backup ke Drive; Sheets menyimpan indeks + ID file Drive-nya.

Struktur Drive:
```
Shiroe Syn Studio/
  Projects/<id - judul>/project.json, audio_vo.wav, subs.srt, subs.ass, <id> - final.mp4
Library musik (DRIVE_MUSIC_FOLDER_ID)/tegang/*.mp3, ceria/*.mp3, dramatis/*.mp3 ...
Library SFX   (DRIVE_SFX_FOLDER_ID)/whoosh.mp3, boom.mp3, ...
```

## 5. Referensi open-source & cara integrasinya

> Saya tidak membuka repositori ini saat menulis kode, jadi detail dan status terbarunya tidak saya verifikasi.
> Nama proyek berikut berasal dari pengetahuan umum. Cek sendiri lisensi dan versi terbarunya sebelum menyalin kode.

| Proyek | Fitur yang relevan | Diadaptasi di sini sebagai |
|---|---|---|
| **MoneyPrinterTurbo** (harry0703) | Pipeline topik → naskah LLM → kata kunci stok Pexels → Edge-TTS → subtitle → MoviePy | Pola keseluruhan Jalur 1. Di sini memakai FFmpeg langsung (bukan MoviePy) dan ada gerbang HITL di tiap tahap |
| **ShortGPT** (RayVentura) | "Editing engine" berbasis JSON, otomasi Shorts | Ide "semua tahap bicara lewat JSON" → format kanonik di atas |
| **rany2/edge-tts** | TTS gratis + event WordBoundary | `services/tts.py` (sejak v7 wajib `boundary="WordBoundary"`; ada fallback untuk versi lama) |
| **SYSTRAN/faster-whisper** | Whisper cepat di CPU + `word_timestamps` | `services/clipper.py` |
| **WhisperX** | Alignment kata yang lebih presisi (wav2vec2) | Opsi upgrade bila timestamp Whisper kurang akurat. Cukup ganti `transcribe_file`, formatnya sama |
| **yt-dlp** | Download + metadata pencarian | Riset (`ytsearch`) dan sumber clipper |
| **FFmpeg / libass** | Filter graph, `sidechaincompress`, subtitle ASS | `services/render.py`, `services/subtitles.py` |
| **Coqui TTS / Piper** | TTS lokal offline (bisa SSML/kloning suara) | Alternatif bila Edge-TTS diblokir. Implementasikan fungsi `synth()` yang mengembalikan `bounds[]` dengan format yang sama |
| **auto-editor**, **PySceneDetect** | Potong jeda/deteksi pergantian adegan | Kandidat peningkatan Jalur 2 |

Aturan menambah/mengganti modul: input dan output **wajib** memakai format kanonik pada §3, dan path relatif terhadap folder proyek.

## 6. Pemetaan spesifikasi → implementasi

| Spesifikasi | Lokasi |
|---|---|
| Tone of voice: preset naratif/antusias/edukatif/sinematik | `store.VOICE_PRESETS`, UI tahap 4 |
| Kecepatan 0.8–1.5x, jeda antar-kalimat | `tts.sentence_params`, `tts.render_vo` (jeda ×1.6 di akhir scene) |
| Clipper tidak memutus kata | `clipper.snap` (batas segmen + padding yang tidak masuk ke kata tetangga) |
| Pratinjau ritme sebelum digabung visual | Timeline per kalimat + preview per scene/kalimat (`/api/tts/preview`) |
| Penanda emosi di naskah | `prompts.script` → `scene.emotion` → `store.EMOTION_VOICE` (rate/pitch) |
| Musik/SFX otomatis sesuai suasana + ganti manual | `audio_lib.auto_plan` + dropdown di UI |
| Highlight kata kunci di subtitle & intonasi | `*kata*` → `Word.emphasis` → ASS (warna + skala) & `emphasis_boost` TTS |
| Kamus fonetik | `tts.apply_pron_text` + tab Sheets *Pronunciation* + kamus per proyek |
| Penjadwal upload YouTube | `youtube.upload` dengan `publishAt` |

## 7. Batasan yang perlu diketahui
- **Edge-TTS tidak lagi menerima SSML kustom**, sehingga emosi dan penekanan diterapkan per kalimat
  (rate/pitch/volume), bukan per kata. Untuk intonasi per kata, gunakan TTS lokal yang mendukung SSML.
- **Audit YouTube API**: project Google Cloud yang belum diaudit hanya bisa mengunggah video sebagai *private*
  (termasuk video terjadwal). Ajukan audit lewat formulir "YouTube API Services – Audit and Quota Extension".
- **Kuota YouTube** 10.000 unit/hari, sedangkan satu upload ±1.600 unit, jadi sekitar 6 upload per hari.
- **Free tier LLM** punya batas request per menit/hari. Jika kena limit, ganti `LLM_PROVIDER` ke provider lain atau ke Ollama.
- **Hak cipta Clipper**: gunakan hanya konten yang Anda punya izinnya. Tab *Assets* mencatat sumber dan lisensi.
