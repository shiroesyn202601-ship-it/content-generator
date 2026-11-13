# Shiroe Syn Content Studio

Aplikasi web lokal untuk membuat konten YouTube dari ide sampai video terjadwal tayang, dengan 7 tahap.
Di setiap tahap Anda bisa menjalankan AI, mengedit manual, lalu **Setujui & Lanjut**.

- **Jalur 1 – Dari nol** (Short 9:16 / Long 16:9): ide → naskah → stock footage → voice over → subtitle → render → upload
- **Jalur 2 – Clipper**: link YouTube/file → transkrip Whisper → AI pilih momen viral → potong → subtitle → render → upload

Arsitektur, alur data, skema Sheets, dan referensi open-source ada di [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 1. Instalasi (Windows)

1. **Python 3.11+**: https://www.python.org/downloads/ (centang *Add python.exe to PATH*)
2. **FFmpeg**: `winget install Gyan.FFmpeg` lalu buka ulang terminal (cek: `ffmpeg -version`)
3. Di folder ini:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   copy .env.example .env
   ```
4. Isi `.env` minimal dengan **satu** API key LLM gratis:
   - Gemini: https://aistudio.google.com/apikey → `GEMINI_API_KEY`
   - atau Groq: https://console.groq.com/keys → `LLM_PROVIDER=groq`, `GROQ_API_KEY`
   - atau Ollama (offline): install Ollama, lalu `ollama pull qwen2.5:7b` dan set `LLM_PROVIDER=ollama`
5. Stock footage (Jalur 1): `PEXELS_API_KEY` (https://www.pexels.com/api/) dan/atau `PIXABAY_API_KEY` (https://pixabay.com/api/docs/)
6. Jalankan: `python run.py` → browser akan terbuka di http://127.0.0.1:8000

Aplikasi sudah bisa dipakai tanpa Google; file tersimpan di `data/projects/`.

## 2. Setup Google (Drive + Sheets + YouTube)

1. Buka https://console.cloud.google.com → buat project baru.
2. *APIs & Services → Library*: aktifkan **Google Drive API**, **Google Sheets API**, **YouTube Data API v3**.
3. *OAuth consent screen*: pilih External, isi nama aplikasi, lalu tambahkan email Anda sebagai **Test user**.
4. *Credentials → Create credentials → OAuth client ID → Desktop app* → unduh JSON-nya dan simpan sebagai
   `credentials/client_secret.json`.
5. Jalankan `python run.py --setup-google`, login di browser, lalu izinkan aksesnya.
   Perintah ini otomatis membuat folder Drive **Shiroe Syn Studio** dan spreadsheet **Shiroe Syn Studio DB**.
6. (Opsional) Library audio bebas royalti di Drive: buat folder `Music` berisi subfolder per mood
   (`tegang`, `ceria`, `dramatis`, `sedih`, `misteri`, `santai`, `epik`, `lucu`) dan folder `SFX`
   (nama file mengandung kata seperti `whoosh`, `boom`, `ding`). Salin ID folder dari URL ke
   `DRIVE_MUSIC_FOLDER_ID` / `DRIVE_SFX_FOLDER_ID`. Library juga bisa disimpan lokal di `data/library/music/<mood>/` dan `data/library/sfx/`.

> ⚠ Selama project masih berstatus "Testing", token kedaluwarsa setiap 7 hari, jadi jalankan ulang `--setup-google`.
> Video yang diunggah lewat API yang belum diaudit Google selalu *private*. Lihat ARCHITECTURE.md §7.

## 2b. Versi web di GitHub Pages

UI tersedia di **https://shiroesyn202601-ship-it.github.io/content-generator/** dan otomatis diperbarui
setiap ada commit ke `main` (workflow `.github/workflows/pages.yml`).

Pages hanya menampung tampilan. Proses berat (AI, TTS, Whisper, FFmpeg, upload) tetap berjalan di laptop Anda:
1. Jalankan `python run.py` di laptop.
2. Buka URL Pages di atas → kotak **Backend** → isi `http://127.0.0.1:8000` → **Hubungkan**.
3. Pakai Chrome/Edge. Safari memblokir halaman https yang memanggil `http://127.0.0.1`.

Setiap commit juga menjalankan workflow **CI** (`.github/workflows/ci.yml`), yang mengecek bahwa dependensi
bisa di-install, semua modul bisa di-import, dan API dasar berjalan.

## 3. Alur kerja

| Tahap | Yang dilakukan AI | Yang bisa Anda ubah |
|---|---|---|
| 1. Ide & Riset | Membaca judul populer di YouTube lalu memberi 6 ide dengan angle baru | Mengedit/menambah ide, memilih satu. Clipper: tempel link atau unggah file |
| 2. Naskah | Menulis kerangka + naskah per scene dengan emosi, mood, `*penekanan*`. Clipper: Whisper + kandidat klip | Teks, urutan scene, emosi; waktu klip (tersedia tombol "Snap ke kalimat") |
| 3. Aset | Mencari stock footage per scene. Clipper: potong + crop 9:16 (blur/crop/letterbox) | Mengganti visual, cari ulang, unggah sendiri |
| 4. Voice & SFX | Edge-TTS per kalimat; musik dipilih sesuai mood; SFX per scene | Preset suara, kecepatan 0.8–1.5x, jeda, kamus pelafalan, track musik/SFX |
| 5. Subtitle | Subtitle per kata, gaya karaoke | Teks per baris, `*kata kunci*`, timing, gaya, dengan pratinjau langsung |
| 6. Render | FFmpeg menggabungkan semuanya, musik otomatis mengecil saat narasi | Render ulang kapan saja |
| 7. Upload | Membuat judul/deskripsi/tag | Mengedit metadata, privasi, jadwal tayang |

Tips: centang **Edit Manual (JSON mentah)** untuk mengubah data tahap apa pun secara langsung.

## 4. Struktur direktori

```
shiroe-content-studio/
├─ run.py                     # start server / --setup-google / --restore
├─ requirements.txt, .env.example
├─ app/
│  ├─ main.py                 # FastAPI: endpoint proyek, gerbang HITL, aksi, media
│  ├─ config.py               # baca .env
│  ├─ store.py                # state proyek, status tahap, format kanonik, preset suara
│  ├─ jobs.py                 # job background (download, whisper, tts, render, upload)
│  └─ services/
│     ├─ llm.py, prompts.py   # Gemini / Groq / Ollama (output JSON)
│     ├─ research.py          # riset yt-dlp + ide, download sumber
│     ├─ scriptgen.py         # naskah + penanda emosi
│     ├─ clipper.py           # faster-whisper, pemilihan klip viral, snap, potong
│     ├─ assets.py            # Pexels / Pixabay
│     ├─ tts.py               # Edge-TTS, kamus fonetik, jeda, emosi, alignment kata
│     ├─ audio_lib.py         # library musik/SFX per mood (Drive / lokal)
│     ├─ subtitles.py         # lines editor, ASS karaoke + penekanan, SRT
│     ├─ render.py            # FFmpeg assembly + ducking + burn-in
│     ├─ google_auth.py, gdrive.py, gsheets.py, sync.py
│     ├─ youtube.py           # upload + publishAt
│     └─ ffmpeg_utils.py
├─ web/ index.html, app.js, style.css
├─ docs/ARCHITECTURE.md
└─ data/ (otomatis) projects/<id>/{project.json, source/, assets/, audio/, render/, subs.ass}
```

## 5. Masalah umum
- **`ffmpeg tidak ditemukan`**: install FFmpeg lalu buka ulang terminal, atau isi `FFMPEG_BIN` dengan path lengkap.
- **Whisper lambat**: pakai `WHISPER_MODEL=base` (lebih cepat, sedikit kurang akurat) atau GPU (`WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE=float16`).
- **Font subtitle tidak muncul**: taruh file `.ttf` di `data/fonts/` dan isi nama font yang sama di gaya subtitle.
- **LLM error 429**: kena limit free tier. Tunggu sebentar atau ganti `LLM_PROVIDER`.
