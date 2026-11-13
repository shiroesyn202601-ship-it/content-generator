# Konteks Percakapan — Shiroe Syn Content Studio

File ini merangkum percakapan pembuatan aplikasi supaya sesi berikutnya (Claude Code atau manusia)
langsung paham latar belakang, keputusan, dan status terakhir. Claude Code membaca `CLAUDE.md`
otomatis setiap sesi dibuka di folder ini.

Terakhir diperbarui: 2026-11-13

---

## 1. Permintaan awal (kata-kata user)

> saya mau jadi konten kreator, mau buat tools untuk generate konten dari 0 sampai bisa di upload
>
> ada 2 jenis konten yg dihasilkan
> 1. konten yg di bangun dr 0 tanpa input asset (bisa short video, bisa long)
> 2. konten klipper, jd inputnya berupa video/ link youtube
>
> toolsnya kyk ber-tahap & ada flow nya gitu. tiap tahapan sy bisa lakukan adjustment / campur tangan saya
> untuk bantu rapihkan/ benerin kl memang ada salah
>
> kriterianya:
> - web base
> - pakai AI yg gratis (claude ini hanya di pakai utk generate aplikasi nya aja)
> - penyimpanannya pake google drive, spreadsheet
> - 1 aplikasi bisa menyelesaikan pembuatan konten: cari ide, framework cerita, kumpulin asset,
>   voice over, sound effect, subtitle, edit video
> - kalau bisa bikin scheduler untuk upload youtube secara otomatis

Setelah itu user menempelkan spesifikasi lengkap (prompt "Principal Full-Stack Software Architect")
untuk kreator **Shiroe Syn**. Poin-poinnya:
- **Tone of Voice:** preset karakter suara (naratif, antusias, edukatif, sinematik) untuk Jalur 1 & 2.
- **Speaking Pace & Rhythm:** kecepatan TTS 0.8–1.5x, jeda antar-kalimat, clipper tidak boleh memutus kalimat
  (pakai transkrip Whisper), dan pratinjau ritme audio sebelum digabung dengan visual.
- **Emotional Register:** LLM menyisipkan penanda emosi di naskah, musik/SFX otomatis sesuai mood dari
  library bebas royalti di Google Drive, dan bisa diganti manual.
- **Script Structure:** wizard 7 tahap. Tiap tahap punya tombol "Edit Manual" dan "Setujui & Lanjut".
  Sheets dipakai sebagai database, Drive sebagai storage.
- **Emphasis:** highlight kata kunci di subtitle (warna/ukuran saat diucapkan) dan intonasi TTS.
  Semua komponen harus gratis dan antar-modul harus kompatibel (format JSON timestamp seragam).
- **Pronunciation:** kamus fonetik kustom di tahap Voice Over.
- **Output yang diminta:** arsitektur, referensi open-source, struktur direktori, dan kode inti.

## 2. Keputusan arsitektur (dan alasannya)

| Keputusan | Alasan |
|---|---|
| Python FastAPI + HTML/JS biasa (tanpa build step) | Whisper, edge-tts, yt-dlp, dan FFmpeg paling matang di Python. UI tidak butuh npm |
| Berjalan **lokal** (`python run.py`) | Whisper/FFmpeg terlalu berat untuk hosting gratis |
| LLM: Gemini / Groq / Ollama (dipilih lewat `.env`) | Semua gratis; Ollama untuk offline |
| TTS: Edge-TTS | Gratis, ada timestamp per kata (WordBoundary) |
| STT: faster-whisper | Lokal, timestamp per kata |
| Render: FFmpeg langsung (bukan MoviePy) | Lebih cepat dan stabil; ada ducking musik (`sidechaincompress`) dan subtitle ASS |
| Jadwal upload: `status.publishAt` YouTube | YouTube sendiri yang menayangkan; laptop tidak perlu menyala |
| State kerja di `data/projects/<id>/project.json`, sinkron ke Sheets/Drive saat "Setujui" | Cepat dan bisa offline; batas sel Sheets 50rb karakter terlalu kecil untuk transkrip |
| OAuth akun pribadi (bukan service account) | Service account tidak punya kuota Drive dan tidak bisa upload ke channel |
| Clipper: 1 proyek = 1 klip, ada tombol "Jadikan proyek baru" (fork) untuk klip lain | Menjaga alur tahap 4–7 tetap sederhana |
| Mengedit tahap yang sudah lewat → tahap sesudahnya `stale` dan `current_stage` mundur | Mencegah render dari data yang sudah tidak cocok |

**Format data kanonik** (wajib dipakai semua modul; detailnya ada di `docs/ARCHITECTURE.md` §3):
- `Word = {text, start, end, emphasis}`, waktu dalam detik, relatif terhadap awal media tahap itu.
- `*kata*` adalah penanda penekanan yang sama untuk naskah, subtitle, dan TTS.
- Path file di JSON selalu relatif terhadap folder proyek.

## 3. Batasan yang sudah disampaikan ke user
- Edge-TTS tidak lagi menerima SSML kustom, jadi emosi dan penekanan diterapkan **per kalimat** (rate/pitch/volume), bukan per kata.
- Project YouTube API yang **belum diaudit** Google hanya bisa upload sebagai **private** (termasuk yang terjadwal).
- Kuota YouTube 10.000 unit/hari, sedangkan satu upload ±1.600 unit, jadi sekitar 6 upload per hari.
- Free tier LLM punya rate limit.
- Jalur clipper hanya untuk konten yang punya izin.
- Selama OAuth app berstatus "Testing", token Google kedaluwarsa tiap 7 hari.

## 4. Kronologi pekerjaan
1. **Kode aplikasi lengkap ditulis.** Saat itu Python dan FFmpeg **belum terpasang** di laptop, jadi kode
   belum bisa dijalankan. JS dicek dengan `node --check` (Node lokal v10, `??` harus diganti dulu sebelum dicek).
2. **Upload ke GitHub:** https://github.com/shiroesyn202601-ship-it/content-generator (publik, branch `main`).
   - Identitas git **lokal repo**: `shiroesyn202601-ship-it` / alamat noreply GitHub (supaya email pribadi tidak terpublikasi).
   - GitHub CLI (`gh`) tidak terpasang; push memakai git + Git Credential Manager.
3. **GitHub Pages:** karena Pages hanya statis, UI di Pages terhubung ke backend lokal.
   - `web/app.js`: `API_BASE` + kotak "Backend" (default `http://127.0.0.1:8000`, disimpan di localStorage).
   - `app/main.py`: CORS (`CORS_ORIGINS`, default `https://shiroesyn202601-ship-it.github.io`) + header Private Network Access.
   - `web/index.html`: path aset relatif `static/...` (berlaku untuk backend maupun Pages).
   - Workflow `.github/workflows/pages.yml` auto-deploy saat push ke `main` (perubahan di `web/`, `docs/`).
   - Workflow `.github/workflows/ci.yml`: install deps, import semua modul, smoke test API, `node --check`.
4. **Hasil Actions pertama:** CI **lulus** ✅. Deploy Pages **gagal** di `configure-pages` karena
   Pages belum diaktifkan. User perlu: Settings → Pages → Source = **GitHub Actions**, lalu re-run.
5. **Folder dipindah** dari `C:\Users\msiso\shiroe-content-studio` ke **`D:\Berdikari\shiroe-content-studio`**
   (pakai robocopy; remote git tetap utuh, tidak perlu clone ulang).

## 5. Status & langkah berikutnya
- [ ] User mengaktifkan GitHub Pages (Source = GitHub Actions) lalu re-run "Deploy Pages".
- [ ] Install Python 3.11+ dan FFmpeg (`winget install Gyan.FFmpeg`), `pip install -r requirements.txt`.
- [ ] Isi `.env` (minimal `GEMINI_API_KEY`; `PEXELS_API_KEY`/`PIXABAY_API_KEY` untuk Jalur 1).
- [ ] Uji end-to-end pertama: Jalur 1, format short, **tanpa Google** dulu.
- [ ] `python run.py --setup-google`, isi library musik/SFX per mood di Drive.
- [ ] Ajukan audit YouTube API bila ingin upload publik/terjadwal.

**Belum pernah diuji nyata:** TTS Edge, Whisper, yt-dlp download, render FFmpeg, integrasi Google, upload YouTube.
Yang sudah terverifikasi baru import modul dan smoke test API (lewat CI).

## 6. Catatan untuk sesi berikutnya
- Bahasa komunikasi dengan user: **Bahasa Indonesia**, santai tapi jelas.
- Jangan commit `.env`, `credentials/`, `data/` (sudah di `.gitignore`). Repo bersifat **publik**.
- Jangan menulis email pribadi user di file repo.
- Kalau error muncul saat uji pertama, cek dulu modul di `app/services/` yang sesuai tahapnya; peta
  spesifikasi → file ada di `docs/ARCHITECTURE.md` §6.
