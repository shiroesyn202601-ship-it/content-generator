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
- YouTube API (aturan per Nov 2026, hasil riset): upload punya bucket kuota sendiri (±100 upload/hari) dan
  sejak 2 Nov 2026 video dari project yang belum diaudit **tidak lagi dikunci private**. `privacyStatus` selalu
  diisi karena default-nya kini public. Belum diverifikasi dengan upload nyata.
- Free tier LLM punya rate limit. Gemini sering 429 (kuota habis), jadi Groq dijadikan provider utama
  (`openai/gpt-oss-120b`, fallback qwen/gpt-oss-20b), Gemini sebagai cadangan.
- Pexels sedang menghentikan pemberian API key baru; user hanya punya Pixabay.
- yt-dlp untuk YouTube butuh **Deno** (sudah di-install lewat winget, dicari otomatis oleh `config.find_deno`).
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
6. **13 Nov 2026 — instalasi & uji pertama:** Python 3.12, FFmpeg 9.0.2, Deno terpasang (winget). Jalur 1
   sudah dicoba user sampai tahap aset/VO. Perbaikan: koneksi Pages↔backend (allow_private_network), Groq +
   fallback model/provider, auto-reload (`.env` dibaca dengan override), bar status per langkah, deteksi
   ffmpeg/deno otomatis, AI pemilih footage (kata kunci + tag terlarang + penilaian AI).
7. **Riset 5 agent + Tahap 1** (bug & keamanan): path traversal upload, aksi ganda/upload duplikat, race
   simpan/setujui, lock sync Sheets & token Google, Clipper (skor string, durasi, jendela transkrip, VAD),
   unduhan yt-dlp, render (frame kumulatif, gambar sekali baca, apad musik, scene tanpa aset, font OFL
   otomatis di `data/fonts`), zona aman subtitle Shorts, anggaran kata naskah dari kecepatan suara + aturan
   anti-karangan fakta.

8. **Tahap 2** (kualitas): struktur retensi Shorts (role scene, hook ≤12 kata, `on_screen_text` → teks hook
   2.5 dtk di layar), rentang kata naskah + kembangkan/padatkan otomatis, subtitle "pop" + preset
   (`store.SUB_PRESETS`) + baris tidak melintasi kalimat, loudnorm -14 LUFS + fade musik, encoder GPU otomatis
   (`ff.video_encoder`, laptop user: h264_qsv Intel Iris Xe) + fallback libx264, mode bingkai blur per scene,
   cache Pixabay 24 jam, musik acak per mood, `containsSyntheticMedia`, prompt metadata SEO Shorts,
   halaman 🩺 Cek Kesiapan (`/api/health`).

9. **Tahap 3** (14 Nov 2026): `factsource.py` (Wikipedia id/en → sumber [S1..S4] di `research.sources`,
   naskah wajib berdasar sumber) + cek fakta (`script.claims`: didukung/tanpa_sumber/bertentangan + usulan);
   potongan visual dinamis per kalimat VO maks 4 dtk + punch-in (`render._plan_shots`, `render.cut_style`);
   Wikimedia Commons (`assets.search_commons`, lisensi PD/CC0/CC BY/BY-SA, `assets.credits` → deskripsi);
   juri visual (`vision.py`, lembar kontak → `qwen/qwen3.8-27b`, hanya scene yang belum "cocok", maks 3);
   structured output strict Groq (`llm.strict`, fallback JSON biasa); 429 Groq ditunggu ≤60 dtk (baca
   `x-ratelimit-reset-*`); job persisten `data/jobs.json` (restart → interrupted) + tombol Batalkan
   (membunuh proses ffmpeg). Catatan: batas per menit Groq gratis ketat, terutama model vision.

10. **Tahap 4** (14 Nov 2026): mode tablet ber-PIN (`APP_PIN`, middleware `lan_pin_guard`, loopback bebas PIN,
    PIN acak bila HOST=0.0.0.0); riset tren (`research.enrich_trends` views/hari via yt-dlp+Deno,
    `research.autocomplete`); produksi massal (`batch.py`, berurutan, tunggu 65 dtk bila 429); thumbnail
    (`thumbs.py`: 3 varian dari video final + teks Anton, `thumbnails.set` setelah processing succeeded);
    auto-reframe wajah Clipper (`reframe.py`, OpenCV **4.x** Haar alt2 — OpenCV 5 tak punya CascadeClassifier;
    crop x per shot); Gemini TTS (`tts_gemini.py`, 1 request per naskah, timing kata via STT + difflib,
    fallback Edge); gambar AI Pollinations (`assets.search_ai`, crop watermark 6% bawah, fallback scene kosong).
    **Temuan penting:** Smart App Control di laptop user **memblokir DLL ctranslate2** → faster-whisper lokal
    tidak bisa jalan. Dibuat `stt.py`: lokal → otomatis **Whisper Groq** (`whisper-large-v3-turbo`, gratis,
    timestamp kata). Jangan sarankan mematikan Smart App Control (tidak bisa dinyalakan lagi).

11. **Mode "Cerita AI"** (permintaan user: hasil seperti CapCut, gambar AI dengan karakter konsisten):
    `story.py` — buku karakter (LLM, strict schema) → gambar referensi per karakter → gambar tiap scene via
    Pollinations `/v1/images/edits` dengan referensi karakter (base64) + seed tetap, model
    `black-forest-labs/flux.2-klein-4b` (butuh `POLLINATIONS_KEY` gratis dari enter.pollinations.ai). Tanpa key:
    teks→gambar FLUX klasik (konsistensi lemah, ±1 gambar/40 dtk, 402/503 sering). Gemini image TIDAK ada di
    free tier key user. Render: transisi antar-scene xfade (`render._join_with_transitions`, `render.transition`).

11. **14 Nov 2026 — ruang kerja terpisah:** sidebar punya tab **🎬 Dari Nol** / **✂ Clipper** (aksen ungu/oranye),
    masing-masing dengan form proyek baru sendiri (Clipper: tempel link → langsung unduh, atau unggah file),
    daftar proyek terfilter, nama tahap sendiri (`store.STAGE_LABELS_CLIPPER`), penjelasan tahap
    (`store.STAGE_HELP`), dan layar sambutan. Link langsung `/#clipper`. Tahap internal tetap sama (7 kunci).
    Tab ketiga **📅 Upload** dipakai bersama kedua jalur: wizard Dari Nol/Clipper hanya 6 tahap (s.d. Render);
    setelah Render disetujui proyek pindah ke tab Upload (grup Siap diunggah / Terjadwal / Sudah terunggah,
    ringkasan jadwal di halaman awal tab). Tahap internal `publish` tetap ada (indeks 6). Link `/#publish/<id>`.
    Juga ikut: mode visual **Cerita AI** (`story.py`, karakter konsisten via Pollinations) + transisi antar-scene.

12. **14 Nov 2026 — Clipper versi riset GitHub** (openshorts MIT, Anil-matcha MIT, hotclip AGPL = ide saja):
    - `reframe.py` ditulis ulang: wajah **YuNet** (`cv2.FaceDetectorYN`, model ONNX diunduh ke `data/models`,
      cadangan Haar), potongan scene ala ContentDetector (selisih HSV adaptif, tanpa paket tambahan),
      "kamera" per shot (zona mati 25%, konfirmasi 3 deteksi, kecepatan dibatasi, kunci bila semua wajah muat,
      lompat di potongan) → jalur x per frame lewat `sendcmd` + `crop@rf`. Default crop Clipper Short = `face`.
    - `clipper.sentences()`: satuan kalimat dari kata Whisper (tanda baca disalin dari teks segmen via difflib,
      putus di . ? ! / jeda ≥0.8 dtk); AI memilih nomor kalimat; jeda napas masuk 0.35 / keluar 0.45 dtk.
    - Rubrik skor 0-100 (hook/alur/nilai/bagikan, bobot `clipper.WEIGHTS`) dengan schema strict, jendela
      bertumpuk, jumlah klip `max(3, min(10, durasi//90+2))`, buang tumpang-tindih ≥25%, kolom **Arahan AI**.
    - **PySceneDetect sengaja tidak dipakai:** `pip install scenedetect` menarik opencv-python 5 → cv2 diblokir
      Smart App Control. Cek Kesiapan kini punya baris "Auto-reframe" untuk mendeteksi hal ini.
    - Cari momen viral video panjang: jendela dibagi PARALEL ke 3 model Groq (kuota 8rb token/menit PER model),
      tanpa schema strict (gpt-oss sering json_validate_failed & membakar kuota), reasoning "low", baris transkrip
      ringkas, tunggu 429 s.d. 4x, tanpa fallback Gemini per jendela; hasil per jendela di `script.clip_cache`
      (klik ulang hanya jendela yang gagal) + `script.clip_note`. Video 50 mnt: ±45 dtk (dulu 4+ mnt / gagal).
    - Belum: deteksi pembicara aktif + layout split, sinyal audio lokal, subtitle YouTube sebagai transkrip.

13. **15 Nov 2026 — Ide & Naskah meniru tab "Generate Narasi"** (repo `test-google-ai-studio`, `NarrationGenerator.tsx`):
    - Tahap 1 (Dari Nol): topik utama, jumlah ide (default 20), target durasi (detik), template prompt ide yang bisa
      diedit (`prompts.IDEA_TEMPLATE`, variabel `{numberOfIdeas}` `{videoDuration}` `{viralTopic}`), riset tren YouTube
      opsional sebagai konteks. Ide = `{title, description, hook, released}`; "Tambah ide baru" (judul lama dikecualikan),
      impor/unduh JSON (format sama dengan repo referensi), tampilan Excel + salin TSV, filter sudah/belum release,
      hapus terpilih, produksi massal memakai template narasi pilihan (`research.narr_template`).
    - Tahap 2: aksi `script.narrate` — template Fakta Menarik / Tragedi / Kisah Hidup / Custom (`prompts.narration_prompt`,
      teks prompt disalin dari referensi) → `script.narration` + `script.hook_text`, lalu otomatis `script.split`:
      AI hanya memilih nomor kalimat awal tiap scene + anotasi (emosi/mood/visual/SFX/penekanan), teks scene disusun
      kode dari kalimat narasi → narasi tidak diubah AI. Modal "Lihat prompt" (`POST /api/projects/{id}/narration-prompt`).
    - Beda dengan referensi: jumlah kata dihitung dari kecepatan suara proyek (`scriptgen.word_range`), bukan 3.5–4.2
      kata/dtk (Edge-TTS Indonesia hanya ±2–2.5 kata/dtk → 50 dtk ≈ 92–109 kata). Fakta/Tragedi tetap bisa memakai
      sumber Wikipedia + cek fakta.

13. **15 Nov 2026 — produksi massal Clipper, subtitle ala CapCut, tahap Thumbnail:**
    - `STAGES` kini 8: ... render → **thumbnail** → publish (indeks publish 7; `store._migrate` menyesuaikan proyek
      lama saat dibaca). UI membaca indeks dari `/api/meta` (`PUB`).
    - Clipper: tahap script + assets tampil sebagai satu tahap **"Momen & Potong"** (`store.CLIPPER_MERGED`; approve
      script ikut approve assets; `assets.cut_clip` terdaftar di tahap script). Centang klip → `script.produce_clips`
      (`clipper.produce_clips`): fork (sumber di-**hardlink**) → potong/reframe → subtitle (template) → (musik) →
      render → 3 thumbnail → semua tahap disetujui → masuk tab Upload. Satu klip gagal tidak menghentikan yang lain.
    - Subtitle: `subtitles.normalize_style` + `to_ass` baru (posisi bebas `\pos` %, rotasi, animasi masuk
      pop/bounce/fade/slide/ketik, kata aktif pop/bounce, sorot warna/pill/kotak (`\p1` dari lebar kata Pillow)/karaoke
      `\kf`, glow, bayangan xy, kotak per baris BorderStyle 3, 1-2 baris). 14 template `store.SUB_TEMPLATES` +
      template buatan sendiri (`data/sub_templates.json`). Pratinjau browser bisa digeser + "Pratinjau akurat"
      (`/subtitles/preview`, satu frame libass). Font OFL tambahan di `fonts.CATALOG`, disajikan di `/fonts/{nama}`.
    - Thumbnail (`thumbs.py`): kandidat frame dari video TANPA subtitle (skor ketajaman + wajah YuNet), 7 template
      layout, teks auto-fit dengan *sorot*, latar (gelap/gradasi/blur/vignette), potong subjek grabCut + outline,
      stiker panah/lingkaran; pratinjau live `/thumbnail/preview`; variasi untuk A/B; dipasang via `publish.thumbnail`.
    - Catatan: render otomatis ikut mengunggah ke Google Drive bila Google terhubung (juga saat produksi massal).

15. **15 Nov 2026 — Clipper: 1 video sumber = 1 proyek, banyak video hasil** (permintaan user). Data tetap: tiap video
    hasil = proyek anak (`parent` = proyek sumber, dibuat `clipper.fork_clip`), jadi pipeline per tahap tidak berubah.
    Yang berubah tampilan & aturan: proyek sumber (`isContainer` di app.js: clipper, tanpa parent, current_stage ≤ 1)
    hanya punya tahap Video Sumber & Momen (tanpa Setujui di Momen); tiap klip "🎬 Jadikan video" / "🎞 Buka video";
    kartu "Video hasil". Sidebar: video hasil bersarang di bawah proyek sumbernya (`li.child`), hitungan tab hanya
    proyek sumber. Video hasil: link ← ke sumber, hanya klipnya sendiri yang tampil. `list_projects` memuat
    `parent` & `clip_id`. Hapus sumber: `?with_children=true` ikut hapus, kalau tidak anak dilepas jadi mandiri.
    Proyek lama yang sumbernya sudah jadi video sendiri (current_stage > 1) tetap berperilaku lama + anak bersarang.
    **Penyimpanan (15 Nov 2026):** folder video hasil ada DI DALAM proyek sumber: `data/projects/<sumber>/clips/<id>/`,
    tanpa salinan/hardlink video sumber — `store.source_file(p)` membaca `source/` milik induk, `/media/<anak>/source/...`
    disajikan dari induk. `store.project_dir` mencari `*/clips/<id>` otomatis; `store.nest_children()` (dipanggil saat
    start) memindahkan anak gaya lama. Hapus sumber tanpa anak → `store.release_child` memindah anak keluar + hardlink sumber.

14. **15 Nov 2026 — Clipper satu tombol (ala 2short.ai):** tab Clipper = halaman awal "tempel link → ✨ Buat Klip"
    (pilihan: durasi klip, jumlah, gaya subtitle visual; opsi lain dilipat; disimpan di localStorage `clipOpts`).
    Aksi `research.auto_clip` (`clipper.auto_clip`): unduh → transkrip → cari momen (`lo/hi` sesuai durasi) →
    `produce_clips` (top N), progres `[n/4]` untuk tampilan 4 langkah. Proyek sumber tampil sebagai **galeri klip**
    (kartu 9:16, skor, ⬇ Unduh / 📅 Jadwalkan / ✏ Edit / 🗑) + "momen lain" sekali klik; wizard = "⚙ Mode lanjutan"
    (`S.adv`). Dibangun di atas wadah sumber/anak dari commit 221f62f (sesi lain). Upload Drive saat produksi
    massal berjalan di latar belakang (`render.run` param `drive_background`). Uji nyata: video 59 dtk → 2 klip, 76 dtk.

16. **15 Nov 2026 — Clipper bernilai tambah** (riset: kebijakan YouTube reused/inauthentic content + pembaruan
    jangkauan Shorts 1 Okt 2026 — potong+subtitle/edit teknis/template massal TIDAK dihitung nilai tambah; yang
    dihitung komentar, konteks, framing edukatif). Modul `enhance.py`, dijalankan setelah potong (di
    `produce_clips` & aksi `assets.enhance`): 1 panggilan LLM → rencana (intro ≤16 kata, outro ≤30 kata opini +
    pertanyaan, people, claims, broll, emoji) → konteks narasumber dari Wikipedia (`_wiki_role`), cek klaim ke
    Wikipedia (hanya didukung/bertentangan yang tampil) → `assets/clip_enh.mp4` = intro TTS (frame blur) + klip
    dirapatkan (jeda >0.45 dtk & "eh/anu" dibuang) dengan punch-in bergantian per kalimat + outro TTS, B-roll layar
    penuh (Commons/Pixabay foto), emoji Noto (fonts.gstatic.com, cadangan Twemoji), SFX (library user atau sintetis
    FFmpeg). Kartu konteks/fakta/label "KOMENTAR · <nama>" = event ASS (`enhance.ass_parts` → `to_ass(extra=)`).
    `p.enhance.words` = timeline baru untuk subtitle; `enhance.valid()` cek mtime clip.mp4 (potong ulang → basi).
    Variasi gaya per klip `enhance.vary_style`. Rencana bisa diedit di Mode lanjutan lalu diterapkan tanpa AI.
    Juga: logo/overlay global (`brand.py`, data/brand.json) + aksi `research.rebrand`.

17. **15 Nov 2026 — tab Upload per proyek + upload lintas proyek:** sidebar Upload = folder proyek (video hasil
    Clipper ikut proyek sumbernya, `pubRoot`/`pubFolders` di app.js) → klik proyek = galeri video (filter
    Siap/Terjadwal/Terunggah) → klik video = editor judul/jadwal (breadcrumb + ‹ › antar video). Centang ☑ video dari
    proyek mana pun (`S.pubSel`, localStorage `pubSel`) → bar "Pilihan" → modal jadwal bergiliran (mulai + jarak,
    urutan bisa diubah, judul AI untuk yang kosong) → `POST /api/publish/bulk` (`youtube.bulk`, job berurutan;
    `upload:false` = simpan jadwal saja). Jadwal masa lalu ditolak di awal sebelum ada yang terunggah.

16. **15 Nov 2026 — AI tanpa ketergantungan satu layanan + Clipper versi riset 2:**
    - `keys.py`: kolam API key per layanan (`X_API_KEY=a,b` atau `X_API_KEY_2..20`). Key dipilih bergiliran
      (LRU), 429 → key didinginkan PER MODEL (Gemini harian ±1 jam, per menit dari retryDelay), 401/403 → mati
      sampai restart. Status (tanpa isi key) di Cek Kesiapan. `config.*_API_KEY` = key pertama (hanya cek "terisi").
    - `llm.py` ditulis ulang: rotasi key Groq (`groq_call`) & Gemini (`gemini_post`, dipakai juga Gemini TTS),
      provider baru kompatibel OpenAI **Cerebras / OpenRouter / Mistral** (`_compat`), `llm.chain()` = utama →
      LLM_FALLBACK_PROVIDERS → layanan cloud lain yang key-nya terisi. Vision: Groq → cadangan Gemini.
      `llm.last_used()` = provider+model yang menjawab (per thread). `only_model` untuk jalur paralel.
    - Whisper Groq & Pollinations ikut rotasi key; Pollinations tanpa key tersisa → endpoint gratis klasik.
    - Clipper: jalur paralel = model Groq × jumlah key (maks 9); bagian yang gagal di Groq dinilai ulang lewat
      layanan lain; batas percobaan per bagian (dulu bisa loop selamanya saat kuota harian habis).
      Sinyal nyata di prompt: 🔥 heatmap "paling sering diputar ulang" YouTube (yt-dlp `heatmap`, disimpan di
      `research.source.heatmap` + `chapters` + `description`), 🔊 lonjakan energi suara (RMS 0.5 dtk, cache
      `source/.stt/loud_*.json`). **Babak final rerank** (`prompts.rerank_clips`): semua kandidat dibandingkan
      satu model, skor = 60% rerank + 40% skor bagian + bonus heatmap. ID klip stabil (hash rentang; klip lama
      dengan rentang sama mempertahankan ID). auto_clip tidak memproduksi ulang klip yang sudah jadi video.
      `_fit_range` membuang kalimat pembuka kata sambung dulu sebelum memotong akhir.
    - STT: kata masuk tepat satu segmen (`stt._assign_words`; transkrip lama dibersihkan `clipper.dedupe_segments`),
      segmen halusinasi Whisper dibuang (no_speech_prob/avg_logprob/compression_ratio), potongan 30 mnt disimpan
      di `source/.stt/` (ulang = lanjut), judul+channel sebagai `prompt` Whisper. Anak hanya membawa transkrip
      ±90 dtk di sekitar klipnya. yt-dlp: retries/fragment_retries/concurrent_fragment_downloads.
    - Uji nyata: video 72 mnt → 10 momen dalam 47 dtk (4 bagian paralel + rerank 3 dtk); key Groq palsu
      otomatis dinonaktifkan; Gemini gagal → Groq.
    - Belum (hasil riset, prioritas berikut): deteksi pembicara aktif dari landmark mulut YuNet + layout split
      2 pembicara, render satu kali encode (potong+reframe langsung di graf render), cut_clip pakai encoder GPU,
      teks auto-caption YouTube sebagai cadangan transkrip, "cold open" teaser, produksi klip paralel.

18. **15 Nov 2026 — redesign UI mobile/tablet:** gaya "meja produksi" (grafit hangat, IBM Plex Sans/Mono, sudut tegas,
    aksen per ruang kerja: mustard Dari Nol / oranye Clipper / hijau Upload, token di `:root` style.css). **Tanpa emoji di UI**
    (ikon garis SVG `<symbol>` di index.html) — sengaja, supaya tidak terlihat "buatan AI"; jangan tambahkan emoji baru.
    < 1024px: top bar + sidebar jadi laci (`setNav`, `body.nav-open`) + tab bawah `#bottomnav` (selector `.tabs button`);
    `(pointer: coarse)` → target sentuh ≥44px & input 16px; < 640px dialog jadi lembar bawah, tabel bisa digeser.
    Branding Clipper (background/icon/logo) kini **dialog** (`brandButton`/`openBrandDialog`), bukan lipatan.

19. **15 Nov 2026 — Dari Nol: pendekatan beda untuk Short vs Long** (permintaan user, acuan: script-to-video CapCut).
    Halaman awal Dari Nol (`scratchHome`) = dua pilihan:
    - **Shorts massal** (`shorts.py`): proyek WADAH `p.batch = {opts, status, last}` (dibuat `POST /api/projects` dengan
      `batch: true`) + proyek ANAK per video (`parent`, folder `data/projects/<wadah>/clips/<id>/`, sama seperti Clipper).
      Topik → `research.ideas` → centang ide → aksi `research.shorts_batch` (`shorts.produce`): tiap ide → anak →
      `shorts.run_pipeline` (narasi → scene → visual stok `assets.auto_all` / gambar AI `story.*` → VO → musik → subtitle
      template → render dinamis → `thumbs.auto` → `youtube.gen_metadata`) → semua tahap disetujui → tab Upload.
      Pipeline melewati tahap yang sudah disetujui → "Lanjutkan produksi" (`children`) = coba lagi / lanjut setelah
      opsi "Cek naskah dulu" (`opts.review`). `ideas[i].video` = id anak. UI `renderShortsBatch` (ide · gaya video · galeri
      `batchCard`); proses dipantau di latar (`trackBatch`, baki `#bgtray`, TIDAK memakai `S.busy`). `isBatch(p)`.
    - **Video panjang** (`longform.py`): tetap 1 proyek wizard. Aksi `script.longform`: outline (cold open, 3-7 bab,
      penutup; schema strict) → tiap bagian ditulis terpisah (anggaran kata per bab, 2 kalimat akhir bagian sebelumnya,
      jembatan ke bab berikut) → narasi dengan baris **`## Judul`** → `scriptgen.split` kini paham bab (`longform.parse_parts`,
      per bab `_split_part`) → `scene.chapter` + `script.chapters [{title, n, scene_id}]` (n=0 untuk Pembuka/Penutup).
      Edit narasi + "Pecah jadi scene" mempertahankan bab. Kartu judul bab di video (`longform.ass_chapters`, digabung di
      `subtitles.export`, matikan via `render.chapter_cards`), chapter YouTube "00:00 Judul" otomatis di deskripsi
      (`longform.chapters_text`, aturan: mulai 00:00, ≥3 bab, ≥10 dtk). `assets.add_extras`: scene panjang di video long
      diberi 1-2 footage variasi (`slot.extras`, ikut kredit) yang dipakai bergantian per shot di render; potongan dinamis
      kini default juga untuk long (maks 6 dtk/shot).
    - Tombol **"Lanjutkan otomatis"** di gerbang wizard Dari Nol (`research.autopilot` = `shorts.autopilot`): kerjakan &
      setujui tahap sisa sampai thumbnail (long tanpa naskah → ditulis per bab dulu).
    - Dialog konfirmasi sendiri `askDialog(title, text, okLabel, danger)` (pengganti `confirm()`).

20. **15 Nov 2026 — Clipper memahami konteks: sunting transkrip sebelum cari momen** (keluhan user: potongan tidak
    paham konteks). Dulu transkrip Whisper mentah langsung dinilai per bagian, tiap bagian tanpa tahu isi video.
    Alur kini: unduh → Whisper → **`proofread.py`** → cari momen. Dipanggil otomatis di akhir `clipper.transcribe`
    (`proofread_safe`: gagal kuota tidak menghentikan Clipper) & di langkah [2/4] `auto_clip` bila belum/gagal sebagian.
    - Konteks (1 panggilan): judul/channel/deskripsi/chapter + cuplikan merata transkrip → `script.context`
      {topic, format, summary, speakers, glossary}; + `outline` per bagian (ringkasan tiap potongan koreksi).
    - Koreksi paralel per ±6rb karakter (lane Groq `clipper._lanes`): AI hanya mengembalikan `{n, from, to, why}`, kode
      mengganti kata ber-timestamp (`_replace_at`, waktu dibagi per panjang huruf) + teks segmen. Effort "medium" lalu
      otomatis "low" bila 400 json_validate_failed ("medium" saja = 17 mnt untuk video 62 mnt; kini ±1-2 mnt).
    - Pengaman (`_ok_fix`): alasan ragu dibuang; HAPUS ucapan hanya untuk halusinasi jelas (`HALU`/huruf non-Latin);
      tolak yang cuma menambah/membuang kata; bagian yang berubah harus mirip bunyi (`MIN_SIMILAR` 0.55 — menolak
      "Gunaderma"→"Gadjah Mada"). Transkrip asli di `script.transcript_raw`, sunting selalu mulai dari situ;
      cache per bagian `proof_cache`. Kamus kreator `transcript_dict` (ganti di seluruh transkrip, tanpa AI) dan
      batal per koreksi `proof_reject` (`reject`/`unreject`). UI: `proofPanel` di Mode lanjutan. Anak tidak membawa
      transcript_raw/cache.
    - `prompts.video_context(ctx, t0, t1)` masuk ke `viral_clips` (+ "sebelum bagian ini" / "bagian ini membahas"),
      `rerank_clips`, dan rencana `enhance`. Aturan baru: klip harus masuk akal tanpa menonton video asli.
    - `auto_clip` dijalankan ulang (`_retry` dari tombol "Jalankan ulang" sesi lain) = lanjutkan `autoclip.picked`
      (tidak cari momen lagi); anak tanpa `output` dihapus & dibuat ulang.

## 5. Status & langkah berikutnya
- Roadmap riset 13 Nov 2026 (Tahap 1–4) **selesai**. Mode Cerita AI menunggu user membuat POLLINATIONS_KEY
  untuk menguji jalur referensi karakter (jalur tanpa key sudah diuji).
- [ ] `python run.py --setup-google`, isi library musik/SFX per mood di Drive, lalu uji satu upload
      (+ pasang thumbnail; channel perlu verifikasi nomor HP untuk thumbnail kustom).
- Ide lanjutan: diarization pembicara Clipper, beat-sync potongan ke musik, importer musik Freesound,
  akun Pollinations (tanpa watermark) / Cloudflare Workers AI.

**Sudah diuji nyata:** render FFmpeg (sintetis), LLM Groq, Pixabay, Wikipedia/Commons, Edge-TTS & Gemini TTS
end-to-end, Whisper Groq, auto-reframe (video sintetis), thumbnail, riset tren, produksi massal, PIN.
**Belum diuji nyata:** yt-dlp unduh video YouTube penuh, Clipper end-to-end, integrasi Google, upload YouTube.

## 6. Catatan untuk sesi berikutnya
- Bahasa komunikasi dengan user: **Bahasa Indonesia**, santai tapi jelas.
- Jangan commit `.env`, `credentials/`, `data/` (sudah di `.gitignore`). Repo bersifat **publik**.
- Jangan menulis email pribadi user di file repo.
- Kalau error muncul saat uji pertama, cek dulu modul di `app/services/` yang sesuai tahapnya; peta
  spesifikasi → file ada di `docs/ARCHITECTURE.md` §6.

## 7. Aturan UX — acuan WAJIB untuk setiap fitur baru (permintaan user, 15 Nov 2026)
Bagian ini adalah standar pengembangan aplikasi. Fitur/layar baru harus memenuhinya; kode lama yang
melanggar diperbaiki saat disentuh.

1. **Proses lama tidak boleh mengunci layar.** User harus tetap bisa mengerjakan hal lain selama menunggu.
   - Setiap proses yang bisa lebih dari ±2 detik (hapus, render, upload, unduh, AI, sinkron Drive, dll.)
     wajib bisa **dikecilkan (minimize)**. Dialog yang menjalankannya punya tombol **"Kecilkan"**;
     menekan Esc / menutup dialog = dikecilkan, BUKAN membatalkan.
   - Selama berjalan wajib ada **indikator loading**: spinner + nama proses + nama item + lama berjalan,
     di baki `#bgtray` (pojok kanan bawah; di HP di atas tab bawah). Job backend tetap memakai bar status.
   - Item yang sedang diproses **ditandai & dikunci** (redup + spinner + label proses di daftar; tidak bisa
     dibuka/diedit/dihapus lagi) sampai prosesnya selesai. Bila item itu sedang terbuka saat dikecilkan,
     layar pindah ke tempat aman (induknya / halaman awal), jangan menampilkan data yang sedang diubah.
   - Selesai/gagal → toast singkat (bukan dialog yang menghalangi), kunci dilepas otomatis.
   - Implementasi: `bgTask(ids, label, judul, fn)` di `web/app.js` (state `S.bg`, `bgTray()`, kelas
     `li.bg-busy`, `openProject` menolak item terkunci). Contoh lengkap: `deleteProject()`.
2. **Jangan pakai `confirm()` / `alert()` / `prompt()` bawaan browser untuk aksi penting.** Pakai dialog sendiri
   (`#modal`) yang menjelaskan apa yang terjadi + pilihan yang relevan (contoh: dialog hapus → nasib video
   hasil & backup Google Drive). Aksi merusak: tombol merah `button.danger-solid`, pilihan berisiko default mati.
3. **Mobile & tablet setara desktop.** Uji di 390px (HP), 820px (tablet), ≥1024px (desktop). Target sentuh ≥44px,
   tidak ada scroll horizontal halaman, panel lebar → dialog/lembar bawah, bukan lipatan panjang.
4. **Tidak terlihat "buatan AI":** tanpa emoji di UI, tanpa gradien ungu/biru, pakai token warna di `:root`
   (`style.css`), ikon garis SVG (`<symbol>` di `index.html`), label/angka huruf mono.
5. **Hapus data harus tuntas & jujur.** Di Windows file yang sedang diputar/di-stream terkunci: pakai
   `store.remove_tree()` (retry di latar + penanda `.deleted` + `store.purge_deleted()` saat start), lepaskan dulu
   `<video>/<audio>` item itu di halaman, dan laporkan bila ada sisa (`locked`). Jangan `rmtree(ignore_errors=True)` diam-diam.
