/* Shiroe Syn Studio — UI wizard 7 tahap dengan gerbang Human-in-the-Loop.
   Pola: S.draft = salinan data tahap yang sedang dilihat. Form mengubah S.draft,
   "Simpan Edit Manual" mengirimnya ke server, "Setujui & Lanjut" membuka tahap berikutnya. */

const $ = (s, el = document) => el.querySelector(s);
const clone = (o) => JSON.parse(JSON.stringify(o ?? null));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtT = (s) => { s = +s || 0; const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, '0')}`; };

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (typeof v === 'boolean') el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

/* Backend bisa di origin yang sama (python run.py membuka UI sendiri) atau di laptop Anda
   saat UI dibuka dari GitHub Pages. Alamat backend disimpan di localStorage. */
const SERVED_BY_BACKEND = !location.hostname.endsWith('github.io') && location.protocol !== 'file:';
const lsGet = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };
const lsSet = (k, v) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (_) {} };
let API_BASE = SERVED_BY_BACKEND ? '' : (lsGet('apiBase') || 'http://127.0.0.1:8000');
const U = (path) => API_BASE + path;

/* Mode tablet/LAN: backend minta PIN untuk perangkat selain laptop. PIN disimpan di localStorage
   (header) dan cookie (supaya <video>/<img> dari /media ikut terotorisasi). */
function setPin(pin) {
  lsSet('appPin', pin || null);
  document.cookie = pin ? `app_pin=${encodeURIComponent(pin)}; path=/; max-age=31536000; SameSite=Lax` : 'app_pin=; path=/; max-age=0';
}

async function api(method, url, body, _retried = false) {
  const opt = { method, headers: {} };
  const pin = lsGet('appPin');
  if (pin) opt.headers['X-App-Pin'] = pin;
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(U(url), opt);
  if (r.status === 401 && !_retried) {
    let d = {}; try { d = await r.clone().json(); } catch (_) {}
    if (d.detail === 'PIN') {
      const p = prompt('Masukkan PIN aplikasi (tertera di jendela terminal laptop):');
      if (p) { setPin(p.trim()); return api(method, url, body, true); }
    }
  }
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) {}
    throw new Error(m);
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.blob();
}

function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = err ? 'err' : ''; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), err ? 9000 : 3000);
}
function status(msg) {
  const s = $('#statusbar'); s.hidden = !msg; s.innerHTML = '';
  if (!msg) return;
  if (msg instanceof Node) s.append(msg);
  else s.append(h('div', { class: 'sb-head' }, h('span', { class: 'spin' }), h('b', {}, msg)));
}

/* Nama aksi yang mudah dipahami untuk bar status & notifikasi. */
const ACTION_LABEL = {
  'research.ideas': '💡 Membuat ide konten', 'research.download': '⬇ Mengunduh video sumber',
  'script.generate': '✍ Menulis naskah', 'script.narrate': '✍ Menulis narasi', 'script.split': '✂ Memecah narasi jadi scene', 'script.factcheck': '🔍 Cek fakta', 'research.batch': '🏭 Produksi massal',
  'publish.thumbnail': '🖼 Memasang thumbnail', 'thumbnail.frames': '🎞 Mengambil kandidat frame',
  'thumbnail.render': '🖼 Menyimpan thumbnail', 'thumbnail.auto': '⚡ Membuat 3 thumbnail otomatis',
  'thumbnail.remove': '✕ Menghapus thumbnail', 'script.produce_clips': '🏭 Produksi banyak video',
  'research.auto_clip': '✨ Membuat klip',
  'assets.story_setup': '📖 Menyusun karakter & prompt', 'assets.story_chars': '👤 Membuat gambar karakter',
  'assets.story_scenes': '🎨 Membuat gambar scene', 'script.transcribe': '🎧 Transkrip audio (Whisper)',
  'script.find_clips': '🔥 Mencari momen viral', 'assets.search': '🔎 Mencari stock footage',
  'assets.select': '🖼 Memasang aset', 'assets.auto_all': '⚡ Mengisi aset semua scene',
  'assets.cut_clip': '✂ Memotong klip', 'audio.render_vo': '🎙 Membuat voice over',
  'audio.auto_plan': '🎵 Memilih musik & SFX', 'subtitles.build': '🔤 Membuat subtitle',
  'subtitles.apply': '🔤 Menyimpan subtitle', 'subtitles.regroup': '↻ Mengelompokkan subtitle',
  'render.run': '🎬 Render video final', 'publish.metadata': '✨ Membuat judul, deskripsi & tag',
  'publish.upload': '⬆ Upload ke YouTube',
};
const actionLabel = (name) => ACTION_LABEL[name] || name;
const fmtDur = (s) => { s = Math.max(0, Math.floor(s)); return s < 60 ? `${s} dtk` : `${Math.floor(s / 60)} mnt ${String(s % 60).padStart(2, '0')} dtk`; };

/* Bar status job: nama proses + total waktu, langkah yang sedang berjalan + lamanya, dan langkah sebelumnya. */
function jobStatusView(label, j, extra) {
  const steps = j.steps || [];
  const cur = steps[steps.length - 1];
  const done = steps.slice(0, -1).slice(-3);
  const cancelBtn = j.status === 'running' ? h('button', { class: 'small ghost sb-cancel', onclick: async (e) => {
    e.target.disabled = true; e.target.textContent = 'Membatalkan…';
    try { await api('POST', `/api/jobs/${j.id}/cancel`); } catch (err) { toast(err.message, true); }
  } }, '✕ Batalkan') : null;
  return h('div', {},
    h('div', { class: 'sb-head' }, h('span', { class: 'spin' }), h('b', {}, label),
      h('span', { class: 'sb-time' }, `· total ${fmtDur(j.elapsed + extra)}`), cancelBtn),
    h('div', { class: 'sb-now' }, '▶ ', cur ? cur.msg : 'Memulai…',
      cur ? h('span', { class: 'sb-time' }, ` (${fmtDur(j.step_elapsed + extra)})`) : null),
    done.length ? h('div', { class: 'sb-done' }, done.map((s) =>
      h('div', {}, `✓ ${s.msg}`, s.took != null ? h('span', { class: 'sb-time' }, ` ${fmtDur(s.took)}`) : null))) : null);
}

const S = {
  meta: null, p: null, view: 0, draft: null, dirty: false, raw: false, busy: false,
  voices: [], lib: null, globalPron: {}, voiceDraft: null, voiceDirty: false,
  batch: new Set(),  // indeks ide yang dicentang untuk produksi massal
  tab: ['clipper', 'publish'].includes(lsGet('tab')) ? lsGet('tab') : 'scratch',  // Dari Nol / Clipper / Upload
};
let PUB = 7;            // indeks tahap "publish" (diisi dari /api/meta): dikerjakan di tab Upload, bukan di wizard
const TABS = ['scratch', 'clipper', 'publish'];
const readyForUpload = (p) => p.current_stage >= PUB;
// Clipper: tahap "assets" (potong klip) digabung ke tahap "script" (Momen & Potong) → tidak tampil sendiri.
const hiddenStage = (mode, k) => mode === 'clipper' && ((S.meta && S.meta.clipper_merged) || {})[k];
const wizardStages = (mode) => S.meta.stages.slice(0, PUB).map((k, i) => [k, i]).filter(([k]) => !hiddenStage(mode, k));
function fixView() { const t = hiddenStage(S.p.mode, stageKey()); if (t) S.view = S.meta.stages.indexOf(t); }
const stageKey = () => S.meta.stages[S.view];
const labelsFor = (mode) => ((S.meta.labels_by_mode || {})[mode]) || S.meta.labels;
const helpFor = (mode, st) => (((S.meta.stage_help || {})[mode]) || {})[st] || '';
const media = (rel) => rel ? U(`/media/${S.p.id}/${rel}?v=${encodeURIComponent(S.p.updated_at)}`) : '';

function loadFonts() {
  const css = (S.meta.fonts || []).filter((f) => f !== 'Arial')
    .map((f) => `@font-face{font-family:"${f}";src:url("${U('/fonts/' + encodeURIComponent(f))}");font-display:swap}`).join('\n');
  document.head.append(h('style', {}, css));
}

// ---------------------------------------------------------------- data flow
async function loadProject(pid, keepView = false, tab = null) {
  S.p = await api('GET', `/api/projects/${pid}`);
  // Tab tujuan: yang diminta, atau tetap di Upload bila proyek sudah siap upload, selain itu tab jalurnya.
  const want = tab || (S.tab === 'publish' && readyForUpload(S.p) ? 'publish' : S.p.mode);
  if (S.tab !== want) { S.tab = want; lsSet('tab', want); renderSidebarTab(); }
  if (S.tab === 'publish') S.view = PUB;
  else if (!keepView || S.view >= PUB) S.view = Math.min(S.p.current_stage, PUB - 1);
  fixView();
  S.draft = clone(S.p[stageKey()]);
  S.voiceDraft = clone(S.p.voice);
  S.dirty = false; S.voiceDirty = false;
  lsSet('lastProject', pid); lsSet(`lastProject_${S.tab}`, pid);
  await refreshList().catch(() => {});  // daftar dulu: header & Momen butuh S.all (proyek sumber/video hasil)
  render();
  resumeJobs();
}

/* Proyek dibuka ulang (refresh halaman / pindah proyek) saat proses masih berjalan → lanjutkan bar status. */
async function resumeJobs() {
  // Proses yang terputus karena server restart: beri tahu sekali per proses.
  (S.p._interrupted || []).forEach((j) => {
    const key = `seen-int-${j.id}`;
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, '1');
    toast(`${actionLabel(j.kind)} terhenti karena server di-restart. Jalankan ulang aksinya.`, true);
  });
  const running = (S.p._jobs || []).filter((j) => !j.kind.startsWith('sync.'));
  if (!running.length || S.busy) return;
  const j = running[0], pid = S.p.id;
  S.busy = true;
  try {
    await pollJob(j.id, actionLabel(j.kind));
    if (S.p && S.p.id === pid) await loadProject(pid, true);
  } catch (e) { toast(`${actionLabel(j.kind)} gagal:\n${e.message}`, true); }
  finally { S.busy = false; status(''); }
}

function markDirty() {
  S.dirty = true;
  const b = $('#btn-save'); if (b) b.textContent = '💾 Simpan Edit Manual *';
}

async function saveDraft(quiet = false) {
  if (stageKey() === 'subtitles') {           // subtitle punya penyimpanan khusus (retiming)
    await runAction('subtitles.apply', { lines: S.draft.lines, style: S.draft.style }, true);
    return;
  }
  S.p = await api('PUT', `/api/projects/${S.p.id}/stage/${stageKey()}`, S.draft);
  S.draft = clone(S.p[stageKey()]); S.dirty = false;
  if (!quiet) toast('Tersimpan. Tahap berikutnya perlu disetujui ulang bila sebelumnya sudah disetujui.');
  render(); refreshList();
}

async function runAction(name, params = {}, skipSave = false) {
  if (S.busy) return toast('Masih ada proses berjalan…', true);
  try {
    if (S.dirty && !skipSave) await saveDraft(true);
    S.busy = true; status(`${actionLabel(name)}…`);
    const res = await api('POST', `/api/projects/${S.p.id}/action/${name}`, params);
    const jobResult = res.job ? await pollJob(res.job, actionLabel(name)) : undefined;
    status('Memuat hasil…');
    await loadProject(S.p.id, true);
    return res.job ? jobResult : res.result;
  } catch (e) {
    toast(`${actionLabel(name)} gagal:\n${e.message}`, true);
  } finally { S.busy = false; status(''); }
}

async function pollJob(jid, label) {
  // Poll server tiap detik; di antaranya hitung detik secara lokal supaya timer terlihat berjalan.
  let last = null, at = 0;
  const tick = setInterval(() => { if (last) status(jobStatusView(label, last, (Date.now() - at) / 1000)); }, 250);
  try {
    for (;;) {
      await sleep(1000);
      const j = await api('GET', `/api/jobs/${jid}`);
      last = j; at = Date.now();
      if (S.onJob) try { S.onJob(j); } catch (_) {}
      status(jobStatusView(label, j, 0));
      if (j.status === 'done') { toast(`${label} selesai ✓ (${fmtDur(j.elapsed)})`); return j.result; }
      if (j.status === 'cancelled') { toast(`${label} dibatalkan.`); return null; }
      if (j.status === 'error' || j.status === 'interrupted') throw new Error(j.error);
    }
  } finally { clearInterval(tick); }
}

async function approve() {
  try {
    if (S.dirty) await saveDraft(true);
    const res = await api('POST', `/api/projects/${S.p.id}/stage/${stageKey()}/approve`);
    toast('Disetujui ✓ — lanjut ke tahap berikutnya');
    S.p = res.project; S.dirty = false;
    let nv = S.view + 1;
    while (nv < PUB && hiddenStage(S.p.mode, S.meta.stages[nv])) nv++;
    if (S.tab !== 'publish' && nv >= PUB) {
      toast('Video siap ✓ — lanjut di tab 📅 Upload & Jadwal');
      await loadProject(S.p.id, false, 'publish');
    } else {
      S.view = Math.min(nv, S.tab === 'publish' ? PUB : PUB - 1);
      S.draft = clone(S.p[stageKey()]);
      render(); refreshList();
    }
    if (S.meta.google) pollJob(res.sync_job, 'Sinkron Google').catch((e) => toast(e.message, true)).finally(() => status(''));
  } catch (e) { toast(e.message, true); }
}

function setView(i) {
  if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
  S.view = i; S.draft = clone(S.p[stageKey()]); S.dirty = false; S.raw = false; render();
}

// ---------------------------------------------------------------- form helpers
function field(label, obj, key, o = {}) {
  let el;
  const set = (v) => { obj[key] = v; (o.dirty || markDirty)(); if (o.onchange) o.onchange(v); };
  if (o.type === 'textarea') {
    el = h('textarea', { rows: o.rows || 3, placeholder: o.placeholder || '', oninput: (e) => set(e.target.value) });
    el.value = obj[key] ?? '';
  } else if (o.type === 'select') {
    el = h('select', { onchange: (e) => set(o.num ? +e.target.value : e.target.value) },
      o.options.map((x) => { const [v, l] = Array.isArray(x) ? x : [x, x]; return h('option', { value: v }, l); }));
    el.value = obj[key] ?? '';
  } else if (o.type === 'checkbox') {
    el = h('input', { type: 'checkbox', onchange: (e) => set(e.target.checked) });
    el.checked = !!obj[key];
    return h('label', { class: 'row' }, el, label);
  } else if (o.type === 'csv') {
    el = h('input', { oninput: (e) => set(e.target.value.split(',').map((s) => s.trim()).filter(Boolean)) });
    el.value = (obj[key] || []).join(', ');
  } else if (o.type === 'range') {
    const out = h('b', {}, (o.fmt || String)(obj[key]));
    el = h('input', { type: 'range', min: o.min, max: o.max, step: o.step,
      oninput: (e) => { set(parseFloat(e.target.value)); out.textContent = (o.fmt || String)(obj[key]); } });
    el.value = obj[key];
    return h('label', { class: 'field' }, h('span', {}, label, ': ', out), el);
  } else {
    const num = o.type === 'number';
    el = h('input', { type: o.type || 'text', step: o.step, min: o.min, max: o.max, placeholder: o.placeholder || '',
      oninput: (e) => set(num ? parseFloat(e.target.value) : e.target.value) });
    el.value = obj[key] ?? '';
  }
  return label ? h('label', { class: 'field' }, h('span', {}, label), el) : el;
}
const btn = (label, fn, cls = '') => h('button', { class: cls, onclick: fn, disabled: S.busy }, label);
const hint = (txt, cls = '') => h('div', { class: `hint ${cls}` }, txt);

async function playTTS(text, emotion) {
  try {
    const blob = await api('POST', '/api/tts/preview', {
      text, emotion, voice: S.voiceDraft, pronunciation: (S.draft && S.draft.pronunciation) || S.p.audio.pronunciation,
    });
    new Audio(URL.createObjectURL(blob)).play();
  } catch (e) { toast(`Preview gagal: ${e.message}`, true); }
}

function playSegment(video, st, en) {
  video.currentTime = st; video.play();
  const stop = () => { if (video.currentTime >= en) { video.pause(); video.removeEventListener('timeupdate', stop); } };
  video.addEventListener('timeupdate', stop);
}

async function uploadFile(url, accept) {
  return new Promise((resolve) => {
    const inp = h('input', { type: 'file', accept });
    inp.onchange = async () => {
      if (!inp.files[0]) return;
      const fd = new FormData(); fd.append('file', inp.files[0]);
      status('Mengunggah file…');
      try { await api('POST', url, fd); toast('File terunggah ✓'); await loadProject(S.p.id, true); }
      catch (e) { toast(e.message, true); }
      finally { status(''); resolve(); }
    };
    inp.click();
  });
}

// ---------------------------------------------------------------- main render
function render() {
  if (S.tab === 'publish') return renderPublishView();
  S.onJob = null;
  if (S.tab === 'clipper' && S.p && !S.p.parent && !(S.adv || {})[S.p.id]) return renderClipSource();
  const p = S.p, main = $('#main');
  main.innerHTML = '';
  if (p.mode === 'clipper') main.append(h('div', { class: 'row', style: 'margin-bottom:6px' },
    p.parent ? btn('← Kembali ke galeri klip', () => loadProject(p.parent, false, 'clipper'), 'small ghost')
      : btn('← Tampilan sederhana', () => { S.adv[p.id] = false; render(); }, 'small ghost'),
    h('span', { class: 'muted small' }, p.parent ? 'Edit detail klip ini (subtitle, bingkai, thumbnail…). Setelah selesai, render ulang.' : 'Mode lanjutan: kerjakan per tahap.')));
  const st = stageKey(), locked = S.view > p.current_stage, L = labelsFor(p.mode);
  applyTheme(p.mode);

  const parent = p.parent ? (S.all || []).find((q) => q.id === p.parent) : null;
  const container = isContainer(p);
  if (parent) main.append(h('div', { class: 'row small' },
    h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProject(parent.id); } }, `← 📁 ${parent.title}`),
    h('span', { class: 'muted' }, ` · video hasil ke-${kidsOf(parent.id).findIndex((q) => q.id === p.id) + 1} dari ${kidsOf(parent.id).length}`)));
  main.append(h('div', { class: 'row between' },
    h('div', {},
      h('h1', {}, container ? `📁 ${p.title}` : p.title),
      h('div', { class: 'row' },
        h('span', { class: 'badge' }, p.mode === 'scratch' ? 'Jalur 1 · Dari nol' : parent ? '🎞 Video hasil Clipper' : 'Jalur 2 · Clipper · video sumber'),
        h('span', { class: 'badge' }, p.format === 'short' ? 'Short 9:16' : 'Long 16:9'),
        container ? h('span', { class: 'badge' }, `${kidsOf(p.id).length} video hasil`) : null,
        h('span', { class: 'badge' }, p.id))),
    h('button', { class: 'ghost danger small', onclick: deleteProject }, parent ? 'Hapus video ini' : 'Hapus proyek')));

  // Proyek sumber Clipper hanya punya tahap Video Sumber & Momen; tahap berikutnya dikerjakan per video hasil.
  main.append(h('div', { class: 'stepper' }, wizardStages(p.mode).filter(([k]) => !container || k === 'research' || k === 'script').map(([k, i]) => h('div', {
    class: `step s-${p.stage_status[k]} ${i === S.view ? 'current' : ''} ${i > p.current_stage ? 'locked' : ''}`,
    onclick: () => setView(i),
  }, L[k], h('span', { class: 'st' }, statusLabel(p.stage_status[k]))))));

  const panel = h('div', { class: 'panel' });
  panel.append(h('div', { class: 'gate' },
    h('b', { class: 'grow' }, L[st], ' ', h('span', { class: `badge ${p.stage_status[st]}` }, statusLabel(p.stage_status[st]))),
    h('label', { class: 'row small' }, h('input', { type: 'checkbox', checked: S.raw, onchange: (e) => { S.raw = e.target.checked; render(); } }), 'Edit Manual (JSON mentah)'),
    h('button', { id: 'btn-save', onclick: () => saveDraft().catch((e) => toast(e.message, true)), disabled: locked || S.busy },
      S.dirty ? '💾 Simpan Edit Manual *' : '💾 Simpan Edit Manual'),
    container && st === 'script' ? null : h('button', { class: 'success', onclick: approve, disabled: locked || S.busy }, '✓ Setujui & Lanjut')));

  if (container && st === 'script') panel.append(h('div', { class: 'muted small stage-help' }, 'Tiap klip pilihan dijadikan video sendiri di dalam proyek ini — otomatis sekaligus (🏭) atau satu per satu (🎬) untuk diedit manual per tahap.'));
  else if (helpFor(p.mode, st)) panel.append(h('div', { class: 'muted small stage-help' }, helpFor(p.mode, st)));
  if (st === 'render' && readyForUpload(p)) panel.append(h('div', { class: 'hint ok row' },
    h('span', { class: 'grow' }, '✅ Video sudah disetujui dan masuk antrean upload.'),
    h('button', { class: 'small primary', onclick: () => loadProject(p.id, false, 'publish') }, '📅 Buka di Upload & Jadwal')));
  if (locked) panel.append(hint('🔒 Tahap ini terkunci. Setujui tahap sebelumnya terlebih dahulu.', 'warn'));
  else if (p.stage_status[st] === 'stale') panel.append(hint('⚠ Tahap sebelumnya diubah. Cek ulang tahap ini (jalankan ulang bila perlu), lalu setujui lagi.', 'warn'));

  if (!locked) {
    if (S.raw) panel.append(rawEditor());
    else panel.append(RENDER[st]());
  }
  main.append(panel);
  main.append(h('details', {}, h('summary', {}, 'Log proyek'), h('pre', { class: 'log' }, (p.log || []).slice().reverse().join('\n'))));
}

const statusLabel = (s) => ({ pending: 'belum', draft: 'dikerjakan', approved: 'disetujui ✓', stale: 'perlu cek ulang' }[s] || s);

function rawEditor() {
  const err = h('div', { class: 'muted small' });
  const ta = h('textarea', { rows: 28, style: 'font-family:monospace;font-size:12px', oninput: (e) => {
    try { S.draft = JSON.parse(e.target.value); err.textContent = 'JSON valid'; markDirty(); }
    catch (x) { err.textContent = `JSON tidak valid: ${x.message}`; }
  } });
  ta.value = JSON.stringify(S.draft, null, 2);
  return h('div', {}, hint('Kontrol penuh: semua data tahap ini bisa diubah langsung. Format mengikuti skema kanonik (lihat docs/ARCHITECTURE.md).'), ta, err);
}

// ---- Clipper: 1 video sumber = 1 proyek (wadah); tiap video hasil = proyek anak (field parent).
const isContainer = (p) => !!p && p.mode === 'clipper' && !p.parent && (p.current_stage || 0) <= 1;
const kidsOf = (pid) => (S.all || []).filter((q) => q.parent === pid).sort((a, b) => a.id.localeCompare(b.id));
const kidStatus = (q) => (readyForUpload(q) ? (q.publish.video_id ? '✅ sudah diunggah' : '📅 siap upload')
  : `✎ ${labelsFor('clipper')[S.meta.stages[q.current_stage]] || ''}`);
function openProject(id, tab = null) {
  if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
  loadProject(id, false, tab);
}

async function deleteProject() {
  if (!confirm(`Hapus ${S.p.parent ? 'video' : 'proyek'} "${S.p.title}" beserta semua file lokalnya? (Backup di Google Drive tidak ikut terhapus)`)) return;
  const kids = kidsOf(S.p.id).length;
  const withKids = kids > 0 && confirm(`Proyek ini punya ${kids} video hasil.\nOK = hapus juga semua video hasil.\nBatal = video hasil tetap disimpan (jadi proyek mandiri).`);
  await api('DELETE', `/api/projects/${S.p.id}${withKids ? '?with_children=true' : ''}`);
  lsSet(`lastProject_${S.p.mode}`, null); lsSet('lastProject_publish', null); S.p = null; lsSet('lastProject', null);
  renderWelcome(S.tab);
  refreshList();
}

function copyText(text, msg = 'Disalin.') {
  if (!text) return;
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => toast(msg), () => toast('Gagal menyalin.', true));
}

function exportIdeas(d) {
  // Format sama dengan repo referensi (topic + ideas[nomor, judul, deskripsi, hookText, sudahRelease]) → bisa bolak-balik.
  const data = { topic: d.keyword || '', ideas: (d.ideas || []).map((it, i) => ({ nomor: i + 1, judul: it.title,
    deskripsi: it.description || it.angle || '', hookText: it.hook || '', sudahRelease: !!it.released })) };
  const now = new Date(), p2 = (n) => String(n).padStart(2, '0');
  const slug = (d.keyword || 'ide').toLowerCase().replace(/[\s,.]+/g, '-').replace(/[^a-z0-9-]/g, '').slice(0, 50);
  const a = h('a', { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })),
    download: `${p2(now.getDate())}-${p2(now.getMonth() + 1)}-${now.getFullYear()}-${p2(now.getHours())}${p2(now.getMinutes())}-${slug}.json` });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function importIdeas(d, ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  file.text().then((txt) => {
    const data = JSON.parse(txt);
    const list = Array.isArray(data) ? data : data.ideas;
    if (!Array.isArray(list)) throw new Error("Format tidak valid: harus punya 'ideas' (array).");
    const ideas = list.map((x) => ({ title: x.judul || x.title || '', description: x.deskripsi || x.description || x.angle || '',
      hook: x.hookText || x.hook || '', released: !!(x.sudahRelease || x.released) })).filter((x) => x.title);
    const replace = !(d.ideas || []).length || confirm(`Impor ${ideas.length} ide.\nOK = ganti ide yang ada, Batal = tambahkan di belakang.`);
    if (replace) { d.ideas = ideas; d.selected = null; S.batch.clear(); } else d.ideas = [...d.ideas, ...ideas];
    if (data.topic && (replace || !d.keyword)) d.keyword = data.topic;
    markDirty(); render(); toast(`${ideas.length} ide diimpor. Klik Simpan supaya tersimpan.`);
  }).catch((e) => toast(`Gagal impor: ${e.message}`, true)).finally(() => { ev.target.value = ''; });
}

// Perkiraan durasi VO dari kecepatan suara (kata/dtk diukur dari edge-tts) + jeda antar-kalimat.
function voSeconds(words) {
  const v = S.p.voice || {}, wps = ({ 'id-ID-ArdiNeural': 2.5, 'id-ID-GadisNeural': 2.0 }[v.voice] || 2.2) * (v.speed || 1);
  return Math.round(words / wps + (words / 9) * ((v.pause_ms || 350) / 1000));
}

async function showNarrPrompt(d) {
  const body = $('#modal-body'); body.innerHTML = '';
  const tpl = d.narr_template === 'custom' ? 'fakta' : d.narr_template;
  body.append(h('h2', {}, 'Opsi Prompt Cerita'), h('p', { class: 'muted' }, 'Memuat prompt…'));
  if (!$('#modal').open) $('#modal').showModal();
  let r;
  try { r = await api('POST', `/api/projects/${S.p.id}/narration-prompt`, { template: tpl, duration: d.narr_duration }); }
  catch (e) { body.lastChild.textContent = `Gagal: ${e.message}`; return; }
  body.innerHTML = '';
  body.append(h('h2', {}, `Prompt ${(S.meta.narr_templates || {})[tpl] || tpl}`),
    h('p', { class: 'muted small' }, tpl === 'tragedi' ? 'Tragedi memakai panjang tetap 1.400–1.700 karakter.' :
      `Durasi ${r.duration} dtk → ${r.words[0]}–${r.words[1]} kata (dihitung dari kecepatan suara proyek).`),
    h('pre', { style: 'white-space:pre-wrap;max-height:55vh;overflow:auto;font-size:12px' }, r.prompt),
    h('div', { class: 'row' },
      btn('📋 Salin prompt', () => copyText(r.prompt, 'Prompt disalin.'), 'small'),
      btn('✏ Edit sebagai Custom', () => { d.narr_custom = r.prompt; d.narr_template = 'custom'; markDirty(); $('#modal').close(); render(); }, 'small ghost')));
}

function rNarration(d) {
  const m = S.meta, short = S.p.format === 'short';
  if (!d.narr_template) d.narr_template = (S.p.research && S.p.research.narr_template) || 'fakta';
  if (d.narr_duration == null) d.narr_duration = (S.p.research && S.p.research.duration) || (short ? 50 : 480);
  if (d.use_sources == null) d.use_sources = true;
  const tpl = d.narr_template, factual = tpl === 'fakta' || tpl === 'tragedi';
  const sel = S.p.research && S.p.research.ideas ? S.p.research.ideas[S.p.research.selected] : null;
  const card = h('div', { class: 'card' });
  card.append(h('div', { class: 'row between' }, h('b', {}, '📝 Narasi'),
    sel ? h('span', { class: 'small muted' }, 'Ide: ', h('b', {}, sel.title)) : null));
  card.append(h('div', { class: 'row', style: 'flex-wrap:wrap' },
    h('span', { class: 'small muted' }, 'Template:'),
    h('div', { class: 'seg' }, Object.entries(m.narr_templates || {}).map(([k, l]) =>
      h('button', { class: tpl === k ? 'on' : '', onclick: () => { d.narr_template = k; markDirty(); render(); } }, l))),
    tpl !== 'tragedi' && tpl !== 'custom' ? h('label', { class: 'row small' }, 'Durasi video ',
      h('input', { type: 'number', min: 15, step: 5, value: d.narr_duration, style: 'width:80px',
        oninput: (e) => { d.narr_duration = +e.target.value || d.narr_duration; markDirty(); } }), ' detik') : null,
    factual ? field('Pakai sumber Wikipedia + cek fakta', d, 'use_sources', { type: 'checkbox' }) : null));
  if (tpl === 'custom') {
    card.append(field('Prompt custom (ditulis apa adanya ke AI)', d, 'narr_custom', { type: 'textarea', rows: 10,
      placeholder: 'Tulis atau tempel prompt kustom di sini… (atau buka "Lihat prompt" lalu "Edit sebagai Custom")' }));
  }
  card.append(h('div', { class: 'row' },
    btn(d.narration ? '🔄 Generate ulang narasi' : '✨ Generate Narasi', () => {
      if (d.narration && !confirm('Narasi & scene yang ada akan diganti. Lanjut?')) return;
      runAction('script.narrate', { template: tpl, duration: d.narr_duration, custom_prompt: tpl === 'custom' ? d.narr_custom : undefined,
        use_sources: factual ? d.use_sources : false });
    }, 'primary'),
    btn('👁 Lihat prompt', () => showNarrPrompt(d), 'ghost')));
  const words = (d.narration || '').trim() ? d.narration.trim().split(/\s+/).length : 0;
  const secs = voSeconds(words);
  const stats = h('span', { class: short && secs > 58 ? 'warn-text small' : 'muted small' },
    `${words} kata · ${(d.narration || '').length} karakter · ±${secs} dtk VO${short && secs > 58 ? ' — terlalu panjang untuk Shorts!' : ''}`);
  card.append(field('', d, 'narration', { type: 'textarea', rows: 10, placeholder: 'Narasi hasil AI muncul di sini — boleh diedit langsung.',
    onchange: (v) => { const w = v.trim() ? v.trim().split(/\s+/).length : 0; const s = voSeconds(w);
      stats.textContent = `${w} kata · ${v.length} karakter · ±${s} dtk VO${short && s > 58 ? ' — terlalu panjang untuk Shorts!' : ''}`; } }));
  card.append(h('div', { class: 'row' }, stats, h('div', { class: 'grow' }),
    btn('📋 Salin narasi', () => copyText(d.narration, 'Narasi disalin.'), 'small ghost')));
  card.append(h('div', { class: 'row' }, h('div', { class: 'grow' }, field('Hook text (thumbnail, 7-10 kata)', d, 'hook_text')),
    btn('📋', () => copyText(d.hook_text, 'Hook disalin.'), 'small ghost')));
  // Narasi diedit setelah dipecah → scene tidak lagi cocok.
  const joined = (d.scenes || []).map((s) => s.text.replace(/\*/g, '')).join(' ').replace(/\s+/g, ' ').trim();
  const differs = d.narration && joined && joined !== d.narration.replace(/\*/g, '').replace(/\s+/g, ' ').trim();
  card.append(h('div', { class: 'row' },
    d.narration ? btn('✂ Pecah jadi scene (AI)', () => {
      if ((d.scenes || []).length && !confirm('Scene yang ada (termasuk edit manual per scene) akan diganti dari narasi. Lanjut?')) return;
      runAction('script.split');
    }, differs ? 'primary' : '') : null,
    differs ? h('span', { class: 'warn-text small' }, 'Narasi berbeda dari scene di bawah — klik "Pecah jadi scene" supaya VO memakai narasi terbaru (atau abaikan bila sengaja edit per scene).') : null));
  return card;
}

// ================================================================ TAHAP 1
function rResearch() {
  const d = S.draft, box = h('div');
  if (S.p.mode === 'scratch') {
    // ---- Generator ide (meniru tab "Generate Narasi" repo test-google-ai-studio)
    const m = S.meta, short = S.p.format === 'short';
    if (d.idea_count == null) d.idea_count = 20;
    if (d.duration == null) d.duration = short ? 50 : 480;
    if (d.use_trends == null) d.use_trends = true;
    if (!d.idea_template) d.idea_template = m.idea_template || '';
    if (!d.narr_template) d.narr_template = 'fakta';
    const tplBox = field('', d, 'idea_template', { type: 'textarea', rows: 10 });
    box.append(h('div', { class: 'card' },
      h('b', {}, '💡 Generator Ide Konten Viral'),
      h('div', { class: 'muted small' }, 'Temukan ide judul & tema video berdasarkan topik pilihan Anda.'),
      h('div', { class: 'grid2' },
        field('Jumlah ide konten', d, 'idea_count', { type: 'number', min: 1, max: 50 }),
        field('Target durasi video (detik)', d, 'duration', { type: 'number', min: 15, step: 5 })),
      h('details', {}, h('summary', {}, 'Template prompt ide (bisa diedit)'),
        h('div', { class: 'row between' }, h('span', { class: 'small muted' }, 'Variabel: {numberOfIdeas}, {videoDuration}, {viralTopic}'),
          btn('Reset ke default', () => { d.idea_template = m.idea_template; markDirty(); render(); }, 'small ghost')),
        tplBox),
      field('Riset tren YouTube sebagai konteks (video populer, views/hari, saran pencarian)', d, 'use_trends', { type: 'checkbox' })));
    box.append(field('Masukkan topik utama', d, 'keyword', { type: 'textarea', rows: 3,
      placeholder: 'mis. sejarah konflik, sejarah berdirinya, sejarah runtuhnya, fakta yang jarang diketahui seputar …' }));
    const fileIn = h('input', { type: 'file', accept: 'application/json,.json', style: 'display:none', onchange: (e) => importIdeas(d, e) });
    box.append(h('div', { class: 'row' },
      btn('✨ Generate Ide', () => {
        if (!(d.keyword || '').trim()) return toast('Topik tidak boleh kosong.', true);
        if ((d.ideas || []).length && !confirm('Ide yang ada akan diganti dengan hasil baru. Lanjut? (Pakai "Tambah ide baru" di bawah untuk menambah.)')) return;
        S.batch.clear();
        runAction('research.ideas', { keyword: d.keyword, count: d.idea_count, duration: d.duration, template: d.idea_template, trends: d.use_trends });
      }, 'primary'),
      btn('📥 Impor JSON', () => fileIn.click(), 'ghost'),
      (d.ideas || []).length ? btn('⬇ Unduh JSON', () => exportIdeas(d), 'ghost') : null, fileIn));
    if (d.insight) box.append(h('div', { class: 'card' }, h('b', {}, 'Insight riset: '), d.insight));
    const ideas = d.ideas || [];
    if (ideas.length) {
      S.ideaView = S.ideaView || 'normal'; S.ideaFilter = S.ideaFilter || 'all';
      const shown = ideas.map((it, i) => [it, i]).filter(([it]) =>
        S.ideaFilter === 'all' || (S.ideaFilter === 'released' ? it.released : !it.released));
      const seg = (cur, opts, onPick) => h('div', { class: 'seg' }, opts.map(([v, l]) =>
        h('button', { class: cur === v ? 'on' : '', onclick: () => onPick(v) }, l)));
      box.append(h('div', { class: 'row', style: 'flex-wrap:wrap;margin-top:12px' },
        h('b', {}, `Hasil ${ideas.length} ide untuk: `, h('span', { class: 'muted' }, (d.keyword || '').slice(0, 80))),
        h('div', { class: 'grow' }),
        seg(S.ideaFilter, [['all', 'Semua'], ['unreleased', 'Belum release'], ['released', 'Sudah release']], (v) => { S.ideaFilter = v; render(); }),
        seg(S.ideaView, [['normal', 'Tampilan normal'], ['excel', 'Tampilan Excel']], (v) => { S.ideaView = v; render(); })));
      box.append(h('div', { class: 'row', style: 'flex-wrap:wrap' }, h('span', { class: 'small muted' }, 'Template narasi (tahap 2 & produksi massal):'),
        seg(d.narr_template, ['fakta', 'tragedi', 'kisah-hidup'].map((k) => [k, (m.narr_templates || {})[k] || k]),
          (v) => { d.narr_template = v; markDirty(); render(); })));
      if (S.ideaView === 'excel') {
        const tsv = () => [['No.', 'Judul', 'Deskripsi', 'Hook'].join('\t'), ...shown.map(([it, i]) =>
          [i + 1, it.title, it.description || it.angle || '', it.hook || ''].map((x) => String(x).replace(/[\t\n]+/g, ' ')).join('\t'))].join('\n');
        box.append(h('div', { class: 'row between' }, h('span', { class: 'small muted' }, 'Pilih sel lalu Ctrl+C, atau salin semua (bisa ditempel ke Excel / Google Sheets).'),
          btn('📋 Salin semua', () => copyText(tsv(), 'Tabel ide disalin (TSV).'), 'small')),
          h('div', { class: 'scroll' }, h('table', {}, h('tr', {}, ['No.', 'Judul', 'Deskripsi', 'Hook'].map((x) => h('th', {}, x))),
            shown.map(([it, i]) => h('tr', {}, h('td', {}, String(i + 1)), h('td', {}, it.title), h('td', {}, it.description || it.angle || ''), h('td', {}, it.hook || ''))))));
      } else {
        shown.forEach(([it, i]) => {
          if (it.description == null && it.angle) it.description = it.angle;
          box.append(h('div', { class: `card ${d.selected === i ? 'selected' : ''}` },
            h('div', { class: 'row' },
              h('label', { class: 'row' }, h('input', { type: 'radio', name: 'idea', checked: d.selected === i,
                onchange: () => { d.selected = i; markDirty(); render(); } }), h('b', {}, `#${i + 1}`), h('span', { class: 'small muted' }, 'pakai untuk proyek ini')),
              it.released ? h('span', { class: 'badge' }, '✓ sudah release') : null,
              h('div', { class: 'grow' }),
              h('label', { class: 'small muted' }, h('input', { type: 'checkbox', checked: !!it.released, onchange: (e) => { it.released = e.target.checked; markDirty(); render(); } }), ' sudah release'),
              h('label', { class: 'small muted' }, h('input', { type: 'checkbox', checked: S.batch.has(i), onchange: (e) => { e.target.checked ? S.batch.add(i) : S.batch.delete(i); render(); } }), ' pilih')),
            h('div', { class: 'row' }, h('div', { class: 'grow' }, field('Judul', it, 'title')),
              btn('📋', () => copyText(it.title, 'Judul disalin.'), 'small ghost')),
            field('Deskripsi', it, 'description', { type: 'textarea', rows: 2 }),
            h('div', { class: 'row' }, h('div', { class: 'grow' }, field('Hook text (thumbnail)', it, 'hook')),
              btn('📋', () => copyText(it.hook || '', 'Hook disalin.'), 'small ghost'))));
        });
      }
      const allSel = shown.length && shown.every(([, i]) => S.batch.has(i));
      S.moreIdeas = S.moreIdeas || 10;
      box.append(h('div', { class: 'row', style: 'flex-wrap:wrap' },
        btn(allSel ? 'Batal pilih semua' : 'Pilih semua', () => { shown.forEach(([, i]) => (allSel ? S.batch.delete(i) : S.batch.add(i))); render(); }, 'small ghost'),
        S.batch.size ? btn(`🏭 Produksi massal (${S.batch.size} ide)`, async () => {
          if (!confirm(`Buat ${S.batch.size} proyek baru dari ide terpilih? AI menulis narasi "${(m.narr_templates || {})[d.narr_template]}" lalu memecahnya jadi scene untuk tiap proyek satu per satu (±1 menit per proyek). Setelah itu cek & setujui naskah masing-masing.`)) return;
          const res = await runAction('research.batch', { indexes: [...S.batch].sort((a, b) => a - b), template: d.narr_template });
          S.batch.clear();
          if (res) toast(`${res.created.length} proyek dibuat. Lihat daftar Proyek di kiri.`);
          refreshList();
        }, 'primary') : null,
        S.batch.size ? btn(`🗑 Hapus (${S.batch.size})`, () => {
          if (!confirm(`Hapus ${S.batch.size} ide terpilih? Tidak bisa diurungkan setelah disimpan.`)) return;
          const keep = d.selected != null ? d.ideas[d.selected] : null;
          d.ideas = d.ideas.filter((_, i) => !S.batch.has(i));
          d.selected = keep && d.ideas.includes(keep) ? d.ideas.indexOf(keep) : null;
          S.batch.clear(); markDirty(); render();
        }, 'ghost danger') : null,
        h('div', { class: 'grow' }),
        h('span', { class: 'small muted' }, 'Tambah ide baru:'),
        h('input', { type: 'number', min: 1, max: 50, value: S.moreIdeas, style: 'width:70px', oninput: (e) => { S.moreIdeas = +e.target.value || 10; } }),
        btn('➕ Generate tambahan', () => runAction('research.ideas', { keyword: d.keyword, count: S.moreIdeas, duration: d.duration, template: d.idea_template, append: true }), 'small'),
        btn('+ Ide manual', () => { d.ideas.push({ title: '', description: '', hook: '' }); markDirty(); render(); }, 'small ghost')));
    }
    if ((d.competitors || []).length) {
      const comp = [...d.competitors].sort((a, b) => (b.views_per_day || -1) - (a.views_per_day || -1));
      box.append(h('details', {}, h('summary', {}, `Data riset YouTube (${d.competitors.length} video · urut views/hari)`),
        h('table', {}, h('tr', {}, h('th', {}, 'Judul'), h('th', {}, 'Channel'), h('th', {}, 'Views'), h('th', {}, 'Umur'), h('th', {}, 'Views/hari')),
          comp.map((c) => h('tr', {}, h('td', {}, h('a', { href: c.url, target: '_blank' }, c.title)),
            h('td', {}, c.channel || ''), h('td', {}, c.views ? c.views.toLocaleString('id') : '-'),
            h('td', {}, c.age_days ? `${c.age_days} hr` : '-'),
            h('td', {}, c.views_per_day != null ? h('b', {}, Math.round(c.views_per_day).toLocaleString('id')) : '-'))))));
    }
    if ((d.suggestions || []).length) {
      box.append(h('details', {}, h('summary', {}, `Yang sedang dicari di YouTube (${d.suggestions.length})`),
        h('div', { class: 'row', style: 'flex-wrap:wrap' }, d.suggestions.map((s) => h('span', { class: 'chip' }, s)))));
    }
  } else {
    const st = { url: (d.source && d.source.url) || '' };
    box.append(h('div', { class: 'row' },
      h('div', { class: 'grow' }, field('Link YouTube', st, 'url', { placeholder: 'https://www.youtube.com/watch?v=…', dirty: () => {} })),
      btn('⬇ Unduh (yt-dlp)', () => runAction('research.download', { url: st.url }), 'primary'),
      btn('📁 Unggah file video', () => uploadFile(`/api/projects/${S.p.id}/upload-source`, 'video/*'))));
    box.append(hint('Pastikan Anda punya izin dari pemilik konten (atau konten berlisensi CC / milik sendiri). Klip tanpa izin berisiko klaim hak cipta.', 'warn'));
    if (d.source && d.source.path) {
      box.append(h('div', { class: 'card' },
        h('b', {}, d.source.title || '(tanpa judul)'), h('div', { class: 'muted' },
          `${d.source.channel || ''} · ${d.source.duration ? fmtT(d.source.duration) : ''} · lisensi: ${d.source.license || '-'}`),
        h('video', { class: 'preview-v', src: media(d.source.path), controls: true, preload: 'metadata' })));
    }
  }
  return box;
}

// ================================================================ TAHAP 2
function rScript() { return S.p.mode === 'scratch' ? rScriptScratch() : rScriptClipper(); }

function rScriptScratch() {
  const d = S.draft, box = h('div'), m = S.meta;
  d.scenes = d.scenes || [];
  const words = d.scenes.reduce((a, s) => a + (s.text.trim() ? s.text.replace(/\*/g, '').trim().split(/\s+/).length : 0), 0);
  // Perkiraan durasi VO dari kecepatan suara (kata/dtk diukur dari edge-tts) + jeda antar-kalimat.
  const secs = voSeconds(words);
  const over = S.p.format === 'short' && secs > 58;
  box.append(rNarration(d));
  if (d.scenes.length) box.append(h('div', { class: 'row' }, h('b', {}, '🎬 Scene'),
    btn('🔍 Cek Fakta', () => runAction('script.factcheck'), 'small'),
    h('span', { class: over ? 'warn-text' : 'muted' }, `${d.scenes.length} scene · ${words} kata · ±${secs} detik${over ? ' — terlalu panjang untuk Shorts (maks 60 dtk)!' : ''}`)));
  // --- hasil riset sumber & cek fakta
  const sources = (S.p.research && S.p.research.sources) || [];
  const claims = d.claims || [];
  const VERD = { didukung: ['✅', 'Didukung sumber'], tanpa_sumber: ['⚠️', 'Tanpa sumber — perlu dicek'], bertentangan: ['❌', 'Bertentangan dengan sumber'] };
  if (sources.length || claims.length) {
    const n = (v) => claims.filter((c) => c.verdict === v).length;
    const risky = n('tanpa_sumber') + n('bertentangan');
    box.append(h('div', { class: `hint ${n('bertentangan') ? 'warn' : ''}` },
      claims.length ? `Cek fakta: ✅ ${n('didukung')} didukung · ⚠️ ${n('tanpa_sumber')} tanpa sumber · ❌ ${n('bertentangan')} bertentangan.`
        + (risky ? ' Perbaiki klaim bertanda ⚠️/❌ di bawah sebelum menyetujui naskah.' : ' Semua klaim didukung sumber.') : 'Belum dicek fakta.',
      S.dirty ? ' (Naskah sudah diedit — simpan lalu klik Cek Fakta lagi.)' : ''));
    if (sources.length) box.append(h('details', {}, h('summary', {}, `📚 Sumber riset (${sources.length} artikel Wikipedia)`),
      h('ul', {}, sources.map((s) => h('li', {}, h('b', {}, `[${s.id}] `), h('a', { href: s.url, target: '_blank' }, s.title), ` · Wikipedia ${s.lang.toUpperCase()}`)))));
  }
  const claimsFor = (sid) => claims.filter((c) => c.scene_id === sid);
  box.append(hint('Tandai kata kunci dengan *bintang* → ditekankan di subtitle (warna/ukuran) dan intonasi TTS. "Emosi" mengatur nada suara; "Mood" memilih musik latar; "Visual" = kata kunci stock footage (Inggris).'));
  if (d.framework) {
    box.append(h('details', { open: true }, h('summary', {}, 'Kerangka cerita'),
      h('div', { class: 'grid2' }, Object.keys(d.framework).map((k) => field(k.toUpperCase(), d.framework, k, { type: 'textarea', rows: 2 })))));
  }
  d.scenes.forEach((s, i) => {
    const move = (dir) => { const j = i + dir; if (j < 0 || j >= d.scenes.length) return; [d.scenes[i], d.scenes[j]] = [d.scenes[j], d.scenes[i]]; markDirty(); render(); };
    box.append(h('div', { class: 'card' },
      h('div', { class: 'row between' }, h('b', {}, `Scene ${i + 1} `, h('span', { class: 'muted small' }, s.id),
        s.role ? h('span', { class: 'tag', style: 'margin-left:6px' }, s.role) : null),
        h('div', { class: 'row' },
          btn('🔊 Preview', () => playTTS(s.text.split(/(?<=[.!?…])\s+/)[0], s.emotion), 'small'),
          btn('▲', () => move(-1), 'small ghost'), btn('▼', () => move(1), 'small ghost'),
          btn('✕', () => { d.scenes.splice(i, 1); markDirty(); render(); }, 'small ghost danger'))),
      field('', s, 'text', { type: 'textarea', rows: 3 }),
      claimsFor(s.id).length ? h('div', { class: 'claims' }, claimsFor(s.id).map((c) => h('div', { class: `claim ${c.verdict}` },
        h('span', { title: VERD[c.verdict][1] }, VERD[c.verdict][0], ' '), h('b', {}, c.claim),
        c.quote ? h('div', { class: 'small muted' }, `“${c.quote}”${c.source_id ? ` — ${c.source_id}` : ''}`) : null,
        c.fix ? h('div', { class: 'small' }, '💡 Usulan: ', c.fix, ' ',
          btn('Salin', () => { navigator.clipboard && navigator.clipboard.writeText(c.fix); toast('Usulan disalin — tempel ke teks scene.'); }, 'small ghost')) : null))) : null,
      i === 0 ? field('Teks hook di layar (2.5 dtk pertama, maks 5 kata)', s, 'on_screen_text', { placeholder: 'mis. KOTA KUNO LEBIH MAJU DARI ROMA?' }) : null,
      h('div', { class: 'grid' },
        field('Emosi', s, 'emotion', { type: 'select', options: m.emotions }),
        field('Mood musik', s, 'mood', { type: 'select', options: m.moods }),
        field('Visual (stock query)', s, 'visual_query'),
        field('SFX', s, 'sfx', { placeholder: 'whoosh / boom / ding' }))));
  });
  box.append(btn('+ Tambah scene', () => { d.scenes.push({ id: `s${Date.now().toString(36)}`, text: '', emotion: 'netral', mood: 'santai', visual_query: '', sfx: '' }); markDirty(); render(); }));
  return box;
}

const SUB_LABEL = { hook: 'Hook', alur: 'Alur', nilai: 'Nilai', bagikan: 'Dibagikan' };
function scoreBadge(c) {
  if (c.score === undefined || c.score === null) return null;
  const outOf = c.scores ? 100 : 10;               // proyek lama: skor 1-10
  const pct = c.score / outOf;
  return h('span', { class: `badge score ${pct >= 0.75 ? 'hi' : pct >= 0.55 ? 'mid' : 'lo'}` }, `skor ${c.score}/${outOf}`);
}

/* Klip yang dicentang untuk produksi massal (default: skor ≥ 70, atau semua bila skor lama). */
function picks() {
  const clips = (S.draft && S.draft.clips) || [];
  if (!S.pick || S.pick.pid !== S.p.id || S.pick.n !== clips.length) {
    const good = clips.filter((c) => (c.scores ? c.score >= 70 : true)).map((c) => c.id);
    S.pick = { pid: S.p.id, n: clips.length, set: new Set(good.length ? good : clips.map((c) => c.id)) };
  }
  return S.pick.set;
}

function subTemplateOptions() {
  const all = { ...(S.meta.sub_templates || {}), ...(S.meta.sub_user_templates || {}) };
  return Object.entries(all).map(([k, t]) => [k, t.label + (k.startsWith('u_') ? ' ★' : '')]);
}
function templateStyle(key) {
  const t = (S.meta.sub_user_templates || {})[key] || (S.meta.sub_templates || {})[key];
  return t ? { ...S.meta.sub_defaults, ...t.style, template: key } : null;
}

function producePanel(d) {
  const set = picks(), n = set.size;
  const opt = S.produceOpt = S.produceOpt || {
    crop_mode: S.p.assets.crop_mode || 'face', template: (S.p.subtitles.style || {}).template || 'pop', music: false, mood: 'santai' };
  const res = S.lastProduce && S.lastProduce.pid === S.p.id ? S.lastProduce.res : null;
  return h('div', { class: 'card produce' },
    h('div', { class: 'row between' }, h('b', {}, '🏭 Jadikan banyak video sekaligus'),
      h('span', { class: 'row small' },
        btn('Centang semua', () => { d.clips.forEach((c) => set.add(c.id)); render(); }, 'small ghost'),
        btn('Kosongkan', () => { set.clear(); render(); }, 'small ghost'))),
    h('div', { class: 'muted small' }, 'Tiap klip yang dicentang (☑) → video hasil di proyek ini → dipotong & dibingkai → subtitle → render → 3 thumbnail. Hasilnya masuk tab 📅 Upload untuk dicek sebelum diunggah; tiap video tetap bisa dibuka & diedit per tahap.'),
    h('div', { class: 'grid' },
      field('Bingkai 9:16', opt, 'crop_mode', { type: 'select', dirty: () => {}, options: [
        ['face', 'Ikuti wajah (auto-reframe)'], ['blur', 'Latar blur'], ['center', 'Crop tengah'], ['none', 'Letterbox']] }),
      field('Template subtitle', opt, 'template', { type: 'select', dirty: () => {}, options: subTemplateOptions() }),
      field('Musik latar (auto-ducking)', opt, 'music', { type: 'checkbox', dirty: () => {}, onchange: () => render() }),
      opt.music ? field('Mood musik', opt, 'mood', { type: 'select', dirty: () => {}, options: S.meta.moods || ['santai'] }) : null),
    h('div', { class: 'row' },
      btn(`🏭 Produksi ${n} video`, async () => {
        if (!n) return toast('Centang minimal satu klip (☑ di kiri judul).', true);
        if (!confirm(`Buat ${n} video sekaligus? Tiap video dipotong, diberi subtitle, dirender, dan dibuatkan thumbnail (±1-2 menit per video). Bisa ditinggal — progres terlihat di bar bawah.`)) return;
        const indexes = d.clips.map((c, i) => (set.has(c.id) ? i : -1)).filter((i) => i >= 0);
        const r = await runAction('script.produce_clips', { indexes, crop_mode: opt.crop_mode, sub_style: templateStyle(opt.template), music: opt.music, mood: opt.mood });
        if (r) { S.lastProduce = { pid: S.p.id, res: r }; toast(`${r.created.length} video jadi${r.failed.length ? `, ${r.failed.length} perlu dicek` : ''} — lihat daftar Video hasil & tab 📅 Upload`); await refreshList(); render(); }
      }, 'primary'),
      h('span', { class: 'muted small' }, `${n} dari ${d.clips.length} klip dicentang`)),
    res ? h('div', { class: 'produce-res' },
      res.created.map((c) => h('div', { class: 'row small' }, '✅ ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); loadProject(c.id, false, 'publish'); } }, c.title))),
      res.failed.map((c) => h('div', { class: 'row small warn-text' }, `⚠ ${c.title} — berhenti di tahap ${c.step}: ${c.error} `,
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); loadProject(c.id, false, 'clipper'); } }, 'buka & perbaiki')))) : null);
}

/* Kerjakan satu klip di proyek ini: atur bingkai, potong, cek hasil, lalu Setujui. */
function cutPanel(c) {
  const a = S.p.assets || {};
  const cutOk = a.clip_path && a.clip_range && Math.abs(a.clip_range[0] - c.start) < 0.06 && Math.abs(a.clip_range[1] - c.end) < 0.06;
  const opt = { crop_mode: a.crop_mode || 'face' };
  return h('div', { class: 'cutpanel' },
    h('div', { class: 'row' },
      h('div', { style: 'min-width:240px' }, field('Bingkai', opt, 'crop_mode', { type: 'select', dirty: () => {}, options: [
        ['face', 'Ikuti wajah (auto-reframe)'], ['blur', 'Latar blur'], ['center', 'Crop tengah'], ['none', 'Letterbox']] })),
      btn(cutOk ? '↻ Potong ulang' : '✂ Potong klip ini', async () => {
        if (S.dirty) await saveDraft(true);
        runAction('assets.cut_clip', { crop_mode: opt.crop_mode }, true);
      }, cutOk ? 'small' : 'small primary'),
      h('span', { class: 'muted small' }, cutOk ? '✓ Sudah dipotong — cek hasilnya, lalu "Setujui & Lanjut".' : 'Potong dulu sebelum Setujui.')),
    cutOk ? h('video', { class: 'preview-v small-v', src: media(a.clip_path), controls: true, preload: 'metadata' }) : null);
}

function rScriptClipper() {
  const d = S.draft, box = h('div');
  const src = S.p.research.source || {};
  const vid = h('video', { class: 'preview-v', src: media(src.path), controls: true, preload: 'metadata' });
  // Video hasil (proyek anak): hanya klip miliknya. Proyek sumber (wadah): semua klip → banyak video.
  const child = !!S.p.parent, container = isContainer(S.p), kids = kidsOf(S.p.id);
  const kidFor = (c) => kids.find((q) => q.clip_id === c.id);
  if (child) {
    box.append(hint('Ini satu video hasil dari proyek sumber. Atur waktu & bingkai klip ini, potong, lalu "Setujui & Lanjut". Klip lain & video baru dibuat di proyek sumber (link ← di atas).'));
  } else box.append(h('div', { class: 'row' },
    btn('1 · Transkrip (Whisper)', () => runAction('script.transcribe'), d.transcript ? '' : 'primary'),
    btn('2 · Cari Momen Viral (AI)', () => runAction('script.find_clips', { direction: d.direction || '' }), d.transcript ? 'primary' : ''),
    btn('+ Klip manual', () => { d.clips = d.clips || []; const t = vid.currentTime || 0; d.clips.push({ id: `m${Date.now().toString(36)}`, start: t, end: t + 30, title: 'Klip manual', hook: '', reason: '', score: 0, keywords: [] }); markDirty(); render(); }, 'ghost')));
  if (!child) box.append(field('Arahan AI (opsional)', d, 'direction', { placeholder: 'mis. cari momen lucu / fokus ke tips praktis / bagian tentang modal usaha' }));
  if (!child) box.append(hint('AI menilai tiap momen 0-100 dari hook, alur, nilai, dan potensi dibagikan. Klip selalu mulai & berakhir di batas kalimat (ditambah jeda napas), jadi tidak ada kata yang terpotong. Setelah mengubah waktu manual, klik "Snap".'));
  if (d.clip_note && !child) box.append(hint(`⚠ ${d.clip_note}`, 'warn'));
  box.append(vid);
  const sel = d.selected_clip;
  if (!child && kids.length) box.append(h('div', { class: 'card' },
    h('b', {}, `🎞 Video hasil dari proyek ini (${kids.length})`),
    kids.map((q, n) => h('div', { class: 'row small' }, `${n + 1}. `,
      h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProject(q.id); } }, q.title),
      h('span', { class: 'muted' }, kidStatus(q)),
      readyForUpload(q) ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); openProject(q.id, 'publish'); } }, '📅 Upload') : null))));
  if (!child && (d.clips || []).length) box.append(producePanel(d));
  (d.clips || []).forEach((c, i) => {
    if (child && i !== sel) return;
    const kid = container || !child ? kidFor(c) : null;
    const durEl = h('span', { class: 'badge' }, `${(c.end - c.start).toFixed(1)} dtk`);
    box.append(h('div', { class: `card ${sel === i ? 'selected' : ''}` },
      h('div', { class: 'row between' },
        h('label', { class: 'row' },
          child ? null : h('input', { type: 'checkbox', title: 'Ikut produksi massal', checked: picks().has(c.id),
            onchange: (e) => { e.target.checked ? picks().add(c.id) : picks().delete(c.id); render(); } }),
          child ? null : h('input', { type: 'radio', name: 'clip', title: container ? 'Klip aktif (untuk ⇤ awal / ⇥ akhir di transkrip)' : 'Kerjakan di proyek ini', checked: sel === i, onchange: () => { d.selected_clip = i; markDirty(); render(); } }),
          h('b', {}, c.title || c.id), durEl, scoreBadge(c),
          kid ? h('span', { class: 'badge' }, '🎞 sudah jadi video') : null),
        h('div', { class: 'row' },
          btn('▶ Putar', () => playSegment(vid, c.start, c.end), 'small'),
          btn('🧲 Snap ke kalimat', async () => {
            const r = await api('GET', `/api/projects/${S.p.id}/snap?start=${c.start}&end=${c.end}`);
            c.start = r.start; c.end = r.end; markDirty(); render();
          }, 'small'),
          child ? null : kid ? btn('🎞 Buka video', () => openProject(kid.id), 'small') : btn('🎬 Jadikan video', async () => {
            if (S.dirty) await saveDraft(true);
            const np = await api('POST', `/api/projects/${S.p.id}/fork`, { clip_index: i });
            toast(`Video dibuat di proyek ini: ${np.title} — atur & potong, lalu lanjut per tahap.`);
            await refreshList(); openProject(np.id);
          }, 'small ghost'),
          child ? null : btn('✕', () => { d.clips.splice(i, 1); if (d.selected_clip === i) d.selected_clip = null; markDirty(); render(); }, 'small ghost danger'))),
      h('div', { class: 'grid' },
        field('Mulai (dtk)', c, 'start', { type: 'number', step: 0.1, onchange: () => (durEl.textContent = `${(c.end - c.start).toFixed(1)} dtk`) }),
        field('Selesai (dtk)', c, 'end', { type: 'number', step: 0.1, onchange: () => (durEl.textContent = `${(c.end - c.start).toFixed(1)} dtk`) }),
        field('Judul', c, 'title'), field('Hook', c, 'hook')),
      field('Kata kunci ditekankan', c, 'keywords', { type: 'csv' }),
      c.scores ? h('div', { class: 'row small' }, Object.entries(SUB_LABEL).map(([k, l]) => h('span', { class: 'subscore' }, `${l} `, h('b', {}, c.scores[k] ?? '-')))) : null,
      c.reason ? h('div', { class: 'muted small' }, 'Alasan AI: ', c.reason) : null,
      sel === i && !container ? cutPanel(c) : null));
  });
  if (d.transcript) {
    const segs = d.transcript.segments || [];
    box.append(h('details', {}, h('summary', {}, `Transkrip (${segs.length} segmen, bahasa: ${d.transcript.language})`),
      h('div', { class: 'scroll' }, segs.map((s) => h('div', { class: 'segment' },
        h('span', { class: 't', onclick: () => { vid.currentTime = s.start; vid.play(); } }, fmtT(s.start)),
        h('span', { class: 'grow' }, s.text),
        sel !== null && sel !== undefined ? h('span', { class: 'row' },
          btn('⇤ awal', () => { d.clips[sel].start = s.start; markDirty(); render(); }, 'small ghost'),
          btn('⇥ akhir', () => { d.clips[sel].end = s.end; markDirty(); render(); }, 'small ghost')) : null)))));
  }
  return box;
}

// ================================================================ TAHAP 3 — Cerita AI
function rStory(d) {
  const box = h('div');
  const st = d.story = d.story || {};
  const keyOk = S.meta.keys.pollinations;
  box.append(hint(keyOk
    ? 'Alur: 1) AI menyusun buku karakter & prompt tiap scene → 2) buat gambar referensi karakter (cek & ulang bila kurang pas) → 3) buat gambar semua scene memakai referensi karakter supaya wajah & pakaian tetap sama.'
    : 'POLLINATIONS_KEY belum diisi → gambar dibuat tanpa referensi karakter (konsistensi wajah TERBATAS, ±1 gambar per beberapa puluh detik). Untuk karakter konsisten: daftar gratis di https://enter.pollinations.ai, buat API key (sk_…), isi POLLINATIONS_KEY=… di .env.', keyOk ? '' : 'warn'));
  st.style = st.style || '3d';
  box.append(h('div', { class: 'row' },
    h('div', { style: 'min-width:220px' }, field('Gaya gambar', st, 'style', { type: 'select', options: Object.entries(S.meta.story_styles || { '3d': 'Animasi 3D' }) })),
    btn(st.characters ? '↻ 1. Susun ulang karakter & prompt (AI)' : '1. Siapkan karakter & prompt (AI)', async () => {
      if (st.characters && !confirm('Susun ulang akan mengganti buku karakter & semua prompt scene. Lanjut?')) return;
      await runAction('assets.story_setup', { style: st.style });
    }, st.characters ? 'ghost' : 'primary')));
  if (!st.characters) return box;

  // --- buku karakter
  const chars = st.characters || [];
  box.append(h('h3', {}, `👤 Karakter (${chars.length})`));
  if (!chars.length) box.append(h('div', { class: 'muted small' }, 'Video ini tidak punya tokoh berulang — langsung ke langkah 3.'));
  const cg = h('div', { class: 'row', style: 'align-items:flex-start;flex-wrap:wrap' });
  chars.forEach((c) => {
    const up = h('input', { type: 'file', accept: 'image/*', style: 'display:none', onchange: async () => {
      const fd = new FormData(); fd.append('file', up.files[0]);
      try { S.p = await api('POST', `/api/projects/${S.p.id}/story/char/${c.id}/upload`, fd); await loadProject(S.p.id, true); toast('Gambar referensi dipakai ✓'); }
      catch (e) { toast(e.message, true); }
    } });
    cg.append(h('div', { class: 'card', style: 'width:260px' },
      c.image ? h('img', { src: media(c.image), style: 'width:100%;border-radius:8px' }) : h('div', { class: 'muted small' }, '(belum ada gambar)'),
      field('Nama', c, 'name'), field('Penampilan (Inggris, makin detail makin konsisten)', c, 'look', { type: 'textarea', rows: 5 }),
      h('div', { class: 'row' },
        btn(c.image ? '↻ Ulang' : '🎨 Buat', async () => { if (S.dirty) await saveDraft(true); runAction('assets.story_chars', { char_id: c.id, bump: Date.now() % 1000 }, true); }, 'small'),
        btn('📁 Unggah', () => up.click(), 'small ghost'), up)));
  });
  box.append(cg);
  const missing = chars.filter((c) => !c.image).length;
  box.append(h('div', { class: 'row' },
    chars.length ? btn(`2. Buat gambar ${missing ? `${missing} karakter` : 'semua karakter (ulang)'}`, () => runAction('assets.story_chars', missing ? {} : { bump: Date.now() % 1000 }), missing ? 'primary' : 'ghost') : null,
    btn('3. Buat gambar semua scene', () => {
      if (missing && !confirm('Masih ada karakter tanpa gambar referensi — scene-nya akan kurang konsisten. Lanjut?')) return;
      runAction('assets.story_scenes');
    }, missing ? '' : 'primary'),
    h('span', { class: 'muted small' }, 'Edit teks lalu "Simpan Edit Manual" sebelum membuat ulang gambar.')));

  // --- storyboard per scene
  const charName = Object.fromEntries(chars.map((c) => [c.id, c.name]));
  S.p.script.scenes.forEach((s, i) => {
    const sc = (st.scenes || {})[s.id]; if (!sc) return;
    const img = sc.image || ((d.scenes || {})[s.id] || {}).selected?.path;
    box.append(h('div', { class: 'card' }, h('div', { class: 'row', style: 'align-items:flex-start' },
      img ? h('img', { src: media(img), style: `width:${S.p.format === 'short' ? 130 : 230}px;border-radius:8px` }) : h('div', { class: 'muted', style: 'width:130px' }, '(belum ada gambar)'),
      h('div', { class: 'grow' },
        h('b', {}, `Scene ${i + 1} `), (sc.characters || []).map((cid) => h('span', { class: 'chip' }, charName[cid] || cid)),
        h('div', { class: 'muted small' }, s.text.replace(/\*/g, '')),
        field('Prompt gambar (Inggris)', sc, 'prompt', { type: 'textarea', rows: 3 }),
        btn(img ? '↻ Ulang gambar ini' : '🎨 Buat gambar ini', async () => { if (S.dirty) await saveDraft(true); runAction('assets.story_scenes', { scene_id: s.id, bump: Date.now() % 1000 }, true); }, 'small')))));
  });
  return box;
}

// ================================================================ TAHAP 3
function rAssets() {
  const d = S.draft, box = h('div');
  if (S.p.mode === 'clipper') {
    const clip = (S.p.script.clips || [])[S.p.script.selected_clip] || {};
    box.append(h('div', { class: 'card' }, h('b', {}, clip.title), h('div', { class: 'muted' }, `${fmtT(clip.start)} → ${fmtT(clip.end)}`)));
    box.append(h('div', { class: 'row' },
      h('div', { style: 'min-width:260px' }, field('Mode bingkai', d, 'crop_mode', { type: 'select', options: [
        ['face', 'Ikuti wajah otomatis (auto-reframe)'], ['blur', 'Latar blur (video utuh di tengah)'], ['center', 'Crop tengah (isi penuh)'], ['none', 'Letterbox hitam']] })),
      btn('✂ Potong klip (FFmpeg)', () => runAction('assets.cut_clip', { crop_mode: d.crop_mode }), 'primary')));
    if (d.clip_path) box.append(h('video', { class: 'preview-v', src: media(d.clip_path), controls: true }));
    return box;
  }
  const keys = S.meta.keys;
  d.visual_mode = d.visual_mode || 'stock';
  box.append(h('div', { class: 'row' }, h('b', {}, 'Sumber visual:'),
    h('div', { class: 'seg' },
      h('button', { class: d.visual_mode === 'stock' ? 'on' : '', onclick: () => { d.visual_mode = 'stock'; markDirty(); render(); } }, '🎞 Stok & foto asli'),
      h('button', { class: d.visual_mode === 'ai_story' ? 'on' : '', onclick: () => { d.visual_mode = 'ai_story'; markDirty(); render(); } }, '🎨 Cerita AI (karakter konsisten)'))));
  if (d.visual_mode === 'ai_story') { box.append(rStory(d)); return box; }
  if (!keys.pexels && !keys.pixabay) box.append(hint('PEXELS_API_KEY / PIXABAY_API_KEY belum diisi di .env — pencarian stok tidak aktif. Anda tetap bisa unggah file manual.', 'warn'));
  box.append(h('div', { class: 'row' }, btn('⚡ Isi otomatis semua scene (AI)', () => runAction('assets.auto_all'), 'primary'),
    btn('🔁 Pilih ulang semua (AI)', () => {
      if (confirm('AI akan mencari & memilih ulang footage untuk SEMUA scene (pilihan sekarang diganti). Lanjut?')) runAction('assets.auto_all', { redo: true });
    }, 'ghost'),
    h('span', { class: 'muted' }, 'AI memilih footage sesuai narasi. Ganti yang kurang pas per scene.')));
  d.scenes = d.scenes || {};
  S.p.script.scenes.forEach((s, i) => {
    const slot = d.scenes[s.id] = d.scenes[s.id] || {};
    const q = { query: slot.query || s.visual_query, provider: 'all' };
    const sel = slot.selected;
    const preview = sel && sel.path ? (sel.type === 'image'
      ? h('img', { src: media(sel.path), style: 'width:160px;border-radius:8px' })
      : h('video', { src: media(sel.path), muted: true, loop: true, autoplay: true, style: 'width:160px' })) : h('div', { class: 'muted' }, '(belum ada visual)');
    box.append(h('div', { class: 'card' },
      h('div', { class: 'row', style: 'align-items:flex-start' }, preview,
        h('div', { class: 'grow' },
          h('b', {}, `Scene ${i + 1}`), h('div', { class: 'muted small' }, s.text.replace(/\*/g, '')),
          sel ? h('div', { class: 'small muted' }, `${sel.provider} · ${sel.author || ''} · ${sel.license || ''}`,
            sel.requires_attribution ? ' · atribusi otomatis masuk deskripsi YouTube' : '') : null,
          sel && slot.pick ? h('div', { class: `hint ${slot.pick.fit === 'lemah' ? 'warn' : ''}`, style: 'margin-top:4px' },
            `${{ cocok: '✅ Cocok', lumayan: '🟡 Lumayan', lemah: '⚠ Kurang cocok — mohon dicek/ganti' }[slot.pick.fit] || slot.pick.fit}`,
            slot.pick.reason ? ` · ${slot.pick.reason}` : '') : null,
          sel && sel.tags ? h('div', { class: 'small muted' }, `Isi video: ${sel.tags}`) : null,
          sel ? h('div', { style: 'max-width:280px' }, field('Bingkai di layar', slot, 'fit', { type: 'select', options: [
            ['auto', 'Otomatis'], ['center', 'Isi penuh (crop tengah)'], ['blur', 'Utuh + latar blur']] })) : null,
          h('div', { class: 'row' },
            h('div', { class: 'grow' }, field('', q, 'query', { dirty: () => {} })),
            field('', q, 'provider', { type: 'select', options: [['all', 'Semua'], ['pixabay', 'Pixabay'], ['commons', 'Wikimedia (foto asli/sejarah)'], ['ai', 'Gambar AI (tulis deskripsi Inggris)'], ['pexels', 'Pexels'], ['photos', 'Foto Pexels']], dirty: () => {} }),
            btn('Cari', () => runAction('assets.search', { scene_id: s.id, query: q.query, provider: q.provider }), 'small'),
            btn('📁 Unggah', () => uploadFile(`/api/projects/${S.p.id}/assets/${s.id}/upload`, 'video/*,image/*'), 'small')))),
      (slot.candidates || []).length ? h('div', { class: 'grid', style: 'margin-top:8px' }, slot.candidates.map((c, k) =>
        h('div', { class: 'thumb', title: `${c.tags || '(tanpa tag)'}\n${c.provider} · ${c.author || ''}${c.query ? ` · dicari: "${c.query}"` : ''}`, onclick: () => runAction('assets.select', { scene_id: s.id, index: k }) },
          h('img', { src: c.thumb || '', loading: 'lazy' }),
          h('span', { class: 'tag' }, `${c.type}${c.duration ? ` · ${c.duration}s` : ''} · ${c.provider}`)))) : null));
  });
  return box;
}

// ================================================================ TAHAP 4
let libLoading = null, voicesLoading = null;
function ensureLib() {
  if (S.lib || libLoading) return libLoading;
  libLoading = Promise.all([api('GET', '/api/library/music'), api('GET', '/api/library/sfx')])
    .then(([music, sfx]) => { S.lib = { music, sfx }; if (stageKey() === 'audio' && !S.dirty) render(); })
    .finally(() => { libLoading = null; });
  return libLoading;
}
function ensureVoices() {
  if (S.voices.length || voicesLoading) return;
  voicesLoading = api('GET', '/api/voices')
    .then((v) => { S.voices = v; if (stageKey() === 'audio') render(); })
    .catch(() => { S.voices = [{ name: S.voiceDraft.voice }]; });
}
const libUrl = (it) => U(`/api/library-file?source=${it.source}&ref=${encodeURIComponent(it.ref)}&name=${encodeURIComponent(it.name)}`);

function libSelect(list, current, onPick) {
  const el = h('select', { onchange: (e) => onPick(e.target.value === '' ? null : list[+e.target.value]) },
    h('option', { value: '' }, '— tidak ada —'),
    list.map((it, i) => h('option', { value: i }, `${it.mood ? `[${it.mood}] ` : ''}${it.name}`)));
  const idx = current ? list.findIndex((x) => x.ref === current.ref) : -1;
  el.value = idx >= 0 ? String(idx) : '';
  return el;
}

function rAudio() {
  const d = S.draft, box = h('div'), v = S.voiceDraft;
  if (!S.lib) ensureLib().catch((e) => toast(`Library audio: ${e.message}`, true));

  if (S.p.mode === 'scratch') {
    ensureVoices();
    const vdirty = () => { S.voiceDirty = true; };
    const voiceOpts = (S.voices.length ? S.voices : [{ name: v.voice }]).map((x) => [x.name, `${x.name}${x.gender ? ` (${x.gender})` : ''}`]);
    box.append(h('div', { class: 'card' }, h('h3', {}, '🎙 Karakter suara (Tone of Voice)'),
      h('div', { class: 'grid' },
        field('Mesin suara', v, 'engine', { type: 'select', dirty: vdirty, onchange: () => render(), options: [
          ['edge', 'Edge-TTS (stabil, cepat)'], ['gemini', 'Gemini TTS (lebih ekspresif, kuota harian terbatas)']] }),
        field('Preset karakter', v, 'preset', { type: 'select', options: Object.keys(S.meta.voice_presets), dirty: vdirty,
          onchange: (k) => { Object.assign(v, S.meta.voice_presets[k]); render(); } }),
        v.engine === 'gemini'
          ? field('Suara Gemini', v, 'gemini_voice', { type: 'select', dirty: vdirty, options: ['Kore', 'Puck', 'Charon', 'Fenrir', 'Aoede', 'Leda', 'Orus', 'Zephyr'] })
          : field('Suara', v, 'voice', { type: 'select', options: voiceOpts, dirty: vdirty }),
        field('Kecepatan', v, 'speed', { type: 'range', min: 0.8, max: 1.5, step: 0.05, fmt: (x) => `${(+x).toFixed(2)}x`, dirty: vdirty }),
        field('Pitch', v, 'pitch_hz', { type: 'range', min: -20, max: 20, step: 1, fmt: (x) => `${x} Hz`, dirty: vdirty }),
        field('Jeda antar-kalimat', v, 'pause_ms', { type: 'range', min: 100, max: 1500, step: 50, fmt: (x) => `${x} ms`, dirty: vdirty })),
      field('Tekankan intonasi kalimat yang berisi *kata kunci*', v, 'emphasis_boost', { type: 'checkbox', dirty: vdirty }),
      v.engine === 'gemini' ? hint('Gemini TTS membaca seluruh naskah sekaligus dengan gaya sesuai preset & emosi tiap scene. Timing kata diambil dengan Whisper (pertama kali mengunduh model ±0.5 GB). Pitch & jeda tidak berlaku. Bila kuota habis, otomatis kembali ke Edge-TTS. Pratinjau 🔊 tetap memakai Edge-TTS.') : null,
      h('div', { class: 'row' },
        btn('💾 Simpan pengaturan suara', async () => {
          S.p = await api('PUT', `/api/projects/${S.p.id}/voice`, S.voiceDraft); S.voiceDirty = false; toast('Pengaturan suara tersimpan');
        }),
        btn('🔊 Preview scene 1', () => playTTS((S.p.script.scenes[0] || {}).text || 'Halo, ini Shiroe Syn.', (S.p.script.scenes[0] || {}).emotion)))));

    // --- kamus fonetik
    d.pronunciation = d.pronunciation || {};
    const rows = Object.entries(d.pronunciation);
    const sync = () => { d.pronunciation = Object.fromEntries(rows.filter((r) => r[0].trim())); markDirty(); };
    const testIn = { text: '' };
    box.append(h('div', { class: 'card' }, h('h3', {}, '📖 Kamus pelafalan (pronunciation)'),
      hint('Tulis istilah asing/singkatan/nama → ejaan fonetik. Contoh: "AI" → "ei ai", "Shiroe" → "shi-ro-e", "YouTube" → "yutub". Berlaku saat render ulang VO.'),
      h('table', {}, h('tr', {}, h('th', {}, 'Istilah'), h('th', {}, 'Dibaca sebagai'), h('th', {}, '')),
        rows.map((r, i) => h('tr', {},
          h('td', {}, h('input', { value: r[0], oninput: (e) => { r[0] = e.target.value; sync(); } })),
          h('td', {}, h('input', { value: r[1], oninput: (e) => { r[1] = e.target.value; sync(); } })),
          h('td', {}, h('div', { class: 'row' }, btn('🔊', () => playTTS(r[0], 'netral'), 'small'),
            btn('✕', () => { rows.splice(i, 1); sync(); render(); }, 'small ghost danger')))))),
      h('div', { class: 'row', style: 'margin-top:8px' },
        btn('+ Istilah', () => { d.pronunciation[`istilah${rows.length + 1}`] = ''; markDirty(); render(); }, 'small'),
        btn('⤴ Simpan ke kamus global (Sheets)', async () => {
          const g = await api('GET', '/api/pronunciation');
          S.globalPron = await api('PUT', '/api/pronunciation', { ...g, ...d.pronunciation });
          toast(`Kamus global: ${Object.keys(S.globalPron).length} istilah`);
        }, 'small')),
      h('div', { class: 'row', style: 'margin-top:8px' }, h('div', { class: 'grow' }, field('', testIn, 'text', { placeholder: 'Uji kalimat bebas…', dirty: () => {} })),
        btn('🔊 Uji', () => playTTS(testIn.text, 'netral'), 'small'))));

    // --- render VO
    box.append(h('div', { class: 'card' }, h('h3', {}, '🗣 Voice over'),
      h('div', { class: 'row' }, btn('▶ Render Voice Over (Edge-TTS)', async () => {
        if (S.voiceDirty) { S.p = await api('PUT', `/api/projects/${S.p.id}/voice`, S.voiceDraft); S.voiceDirty = false; }
        runAction('audio.render_vo');
      }, 'primary'), d.vo_path ? h('span', { class: 'muted' }, `durasi ${fmtT(d.duration)} · ${(d.words || []).length} kata`) : null),
      d.vo_path ? voTimeline(d) : hint('Belum dirender. Pratinjau ritme (timeline per kalimat) muncul di sini sebelum digabung dengan visual.')));
  } else {
    box.append(hint('Mode clipper memakai audio asli klip. Tambahkan musik latar (otomatis di-ducking saat ada suara) dan SFX bila perlu.'));
  }

  // --- musik & SFX
  const lib = S.lib || { music: [], sfx: [] };
  d.music = d.music || { enabled: false, volume: 0.12 };
  d.sfx = d.sfx || [];
  box.append(h('div', { class: 'card' }, h('h3', {}, '🎵 Musik latar & SFX (Emotional Register)'),
    lib.music.length + lib.sfx.length === 0 ? hint('Library kosong. Isi folder Drive (DRIVE_MUSIC_FOLDER_ID / DRIVE_SFX_FOLDER_ID) atau data/library/music/<mood>/ dan data/library/sfx/.', 'warn') : null,
    h('div', { class: 'row' },
      field('Pakai musik latar', d.music, 'enabled', { type: 'checkbox' }),
      h('div', { style: 'min-width:150px' }, field('Mood', d.music, 'mood', { type: 'select', options: ['', ...S.meta.moods] })),
      btn('🎯 Pilih otomatis sesuai mood naskah', () => runAction('audio.auto_plan', { mood: S.p.mode === 'clipper' ? d.music.mood : null }), 'small'),
      btn('↻ Muat ulang library', async () => { S.lib = null; await api('GET', '/api/library/music?refresh=true'); await api('GET', '/api/library/sfx?refresh=true'); ensureLib(); }, 'small ghost')),
    h('div', { class: 'grid2' },
      h('label', { class: 'field' }, h('span', {}, 'Track musik'), libSelect(lib.music, d.music.file, (it) => { d.music.file = it; markDirty(); render(); })),
      field('Volume musik', d.music, 'volume', { type: 'range', min: 0, max: 0.5, step: 0.01, fmt: (x) => `${Math.round(x * 100)}%` })),
    d.music.file ? h('audio', { src: libUrl(d.music.file), controls: true }) : null,
    h('h3', { style: 'margin-top:12px' }, 'Efek suara'),
    h('table', {}, h('tr', {}, h('th', {}, 'Detik ke-'), h('th', {}, 'File'), h('th', {}, 'Volume'), h('th', {}, '')),
      d.sfx.map((s, i) => h('tr', {},
        h('td', { style: 'width:100px' }, field('', s, 'at', { type: 'number', step: 0.1 })),
        h('td', {}, libSelect(lib.sfx, s.item, (it) => { s.item = it; markDirty(); })),
        h('td', { style: 'width:140px' }, field('', s, 'volume', { type: 'range', min: 0, max: 1.5, step: 0.05, fmt: (x) => `${Math.round(x * 100)}%` })),
        h('td', {}, h('div', { class: 'row' }, s.item ? btn('🔊', () => new Audio(libUrl(s.item)).play(), 'small') : null,
          btn('✕', () => { d.sfx.splice(i, 1); markDirty(); render(); }, 'small ghost danger')))))),
    btn('+ SFX', () => { d.sfx.push({ at: 0, item: null, volume: 0.6, label: '' }); markDirty(); render(); }, 'small')));
  return box;
}

function voTimeline(d) {
  const audio = h('audio', { src: media(d.vo_path), controls: true, style: 'width:100%' });
  const total = d.duration || 1;
  const cursor = h('div', { class: 'muted small' }, '');
  const tl = h('div', { class: 'timeline' }, (d.sentences || []).map((s) => h('div', {
    style: `width:${((s.end - s.start) / total) * 100}%`, title: `${fmtT(s.start)} · ${s.text} (${s.rate}, ${s.pitch})`,
    onclick: () => { audio.currentTime = s.start; audio.play(); },
  }, s.text)));
  audio.addEventListener('timeupdate', () => {
    const s = (d.sentences || []).find((x) => audio.currentTime >= x.start && audio.currentTime <= x.end);
    cursor.textContent = s ? `▶ ${s.scene_id}: ${s.text}` : '';
  });
  return h('div', {}, audio, tl, cursor);
}

// ================================================================ TAHAP 5
const TOKEN_RE = /\*([^*]+)\*|(\S+)/g;
function tokenizeJS(text) {
  const out = []; let m;
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(text))) {
    if (m[1] !== undefined) m[1].split(/\s+/).filter(Boolean).forEach((w) => out.push({ text: w, emphasis: true }));
    else out.push({ text: m[2].replace(/\*/g, ''), emphasis: false });
  }
  return out.filter((t) => t.text);
}

function rSubtitles() {
  const d = S.draft, box = h('div');
  d.style = normStyle(d.style || {});
  const st = d.style;
  box.append(h('div', { class: 'row' },
    btn('🔤 Bangun subtitle dari timestamp kata', () => runAction('subtitles.build'), d.words && d.words.length ? '' : 'primary'),
    btn('↻ Kelompokkan ulang baris', () => runAction('subtitles.regroup', { style: st }, true), 'ghost'),
    h('span', { class: 'muted' }, `${(d.lines || []).length} baris · ${(d.words || []).length} kata`)));

  // ---------- pratinjau (bisa digeser)
  const [W, H] = S.p.format === 'short' ? [1080, 1920] : [1920, 1080];
  const pw = S.p.format === 'short' ? 300 : 600, ph = (pw * H) / W, k = ph / H;
  const isClip = S.p.mode === 'clipper';
  const player = isClip ? h('video', { src: media(S.p.assets.clip_path), controls: true, playsInline: true })
    : h('audio', { src: media(S.p.audio.vo_path), controls: true, style: 'width:100%' });
  const overlay = h('div', { class: 'sub-overlay2', title: 'Geser untuk memindah posisi subtitle' });
  const exact = h('img', { class: 'sub-exact', hidden: true });
  const safe = S.p.format === 'short' ? h('div', { class: 'safezone' }, h('span', {}, 'tertutup UI YouTube')) : null;
  const stage = h('div', { class: 'sub-stage', style: `width:${pw}px;height:${ph}px` },
    isClip ? player : h('div', { class: 'bg' }), safe, overlay, exact);
  const lineEls = [];
  const changed = () => { markDirty(); exact.hidden = true; draw(); };

  const draw = () => {
    const t = player.currentTime;
    overlay.style.left = `${st.pos_x}%`; overlay.style.top = `${st.pos_y}%`;
    overlay.style.transform = `translate(-50%,-50%) rotate(${-(+st.rotation || 0)}deg)`;
    overlay.style.fontFamily = `"${st.font}", Arial, sans-serif`;
    overlay.style.fontSize = `${st.size * k}px`;
    overlay.style.fontWeight = st.bold ? '900' : '400';
    overlay.style.letterSpacing = `${(st.letter_spacing || 0) * k}px`;
    const boxed = st.box === 'box';
    overlay.style.background = boxed ? hexA(st.box_color, st.box_alpha) : 'transparent';
    overlay.style.padding = boxed ? `${st.box_pad * k}px` : '0';
    const shadows = [];
    if (st.shadow_alpha > 0 && !boxed) shadows.push(`${st.shadow_x * k}px ${st.shadow_y * k}px ${(st.blur || 0) * k * 2}px ${hexA(st.shadow, st.shadow_alpha)}`);
    if (st.glow && !boxed) shadows.push(`0 0 ${st.glow_w * k * 1.5}px ${st.glow}`, `0 0 ${st.glow_w * k * 3}px ${st.glow}`);
    overlay.style.textShadow = shadows.join(',');
    const idx = (d.lines || []).findIndex((l) => t >= l.start && t <= l.end + 0.05);
    lineEls.forEach((el, i) => el.classList.toggle('active', i === idx));
    overlay.innerHTML = '';
    // contoh teks saat tidak ada baris aktif → posisi & gaya tetap terlihat saat diatur
    const line = idx >= 0 ? d.lines[idx] : (d.lines || [])[0];
    if (!line) { overlay.append(h('span', {}, 'CONTOH SUBTITLE')); return; }
    const toks = tokenizeJS(line.text);
    const ws = (d.words || []).slice(line.i0, line.i1 + 1);
    const timed = ws.length === toks.length ? ws : toks.map((_, j) => ({
      start: line.start + ((line.end - line.start) * j) / toks.length }));
    const tt = idx >= 0 ? t : (timed[1] || timed[0]).start + 0.01;
    const brk = st.max_lines == 2 && toks.length >= 4 ? Math.floor(toks.length / 2) : -1;
    toks.forEach((tk, j) => {
      const active = st.highlight_mode !== 'none' && tt >= timed[j].start && (j + 1 >= timed.length || tt < timed[j + 1].start);
      const sung = tt >= timed[j].start;
      let color = tk.emphasis ? st.emphasis : st.primary, sc = 1, bg = '';
      if (st.highlight_mode === 'karaoke') { if (sung) color = tk.emphasis ? st.emphasis : st.highlight; }
      else if (active && (st.highlight_mode === 'pill' || st.highlight_mode === 'box')) {
        color = st.highlight_text; bg = st.highlight_bg;
      } else if (active) {
        color = tk.emphasis ? st.emphasis : st.highlight;
        sc = (tk.emphasis ? st.emphasis_scale : st.highlight_scale) / 100;
      }
      const hidden = st.anim_in === 'typewriter' && idx >= 0 && j > toks.findIndex((_, q) => q + 1 >= timed.length || tt < timed[q + 1].start);
      const txt = st.case === 'upper' ? tk.text.toUpperCase() : st.case === 'lower' ? tk.text.toLowerCase() : tk.text;
      if (j === brk) overlay.append(h('br'));
      overlay.append(h('span', { class: 'w', style: [
        `color:${color}`, `transform:scale(${boxed ? 1 : sc})`, hidden ? 'visibility:hidden' : '',
        boxed ? '' : `-webkit-text-stroke:${(st.stroke_w || 0) * k * 2}px ${st.stroke}`,
        bg ? `background:${bg};border-radius:${st.highlight_mode === 'pill' ? 999 : 6}px;padding:0 ${st.box_pad * k}px;-webkit-text-stroke:0` : '',
      ].join(';') }, txt), ' ');
    });
  };
  // geser posisi dengan mouse/jari
  overlay.addEventListener('pointerdown', (e) => {
    overlay.setPointerCapture(e.pointerId);
    const rect = stage.getBoundingClientRect();
    const move = (ev) => {
      st.pos_x = Math.round(Math.max(5, Math.min(95, ((ev.clientX - rect.left) / rect.width) * 100)));
      st.pos_y = Math.round(Math.max(5, Math.min(95, ((ev.clientY - rect.top) / rect.height) * 100)));
      changed(); syncPosInputs();
    };
    const up = () => { overlay.removeEventListener('pointermove', move); overlay.removeEventListener('pointerup', up); };
    overlay.addEventListener('pointermove', move); overlay.addEventListener('pointerup', up);
  });
  let raf;
  const loop = () => { draw(); if (!player.paused) raf = requestAnimationFrame(loop); };
  player.addEventListener('play', () => { exact.hidden = true; cancelAnimationFrame(raf); loop(); });
  player.addEventListener('seeked', draw);
  setTimeout(draw, 0);

  const exactBtn = btn('🎯 Pratinjau akurat', async () => {
    exactBtn.disabled = true; exactBtn.textContent = 'Merender…';
    try {
      const blob = await api('POST', `/api/projects/${S.p.id}/subtitles/preview`, { style: st, t: player.currentTime + 0.25 });
      exact.src = URL.createObjectURL(blob); exact.hidden = false;
    } catch (e) { toast(e.message, true); }
    finally { exactBtn.disabled = false; exactBtn.textContent = '🎯 Pratinjau akurat'; }
  }, 'small');

  // ---------- kontrol gaya
  const f = (label, key, o = {}) => field(label, st, key, { ...o, onchange: () => { if (o.rerender) render(); else changed(); } });
  const range = (label, key, min, max, step = 1, fmt) => f(label, key, { type: 'range', min, max, step, fmt });
  const posX = range('Posisi X (%)', 'pos_x', 5, 95), posY = range('Posisi Y (%)', 'pos_y', 5, 95);
  const syncPosInputs = () => {
    [[posX, st.pos_x], [posY, st.pos_y]].forEach(([el, v]) => { $('input', el).value = v; $('b', el).textContent = v; });
  };
  const all = { ...(S.meta.sub_templates || {}), ...(S.meta.sub_user_templates || {}) };
  const gallery = h('div', { class: 'tpl-grid' }, Object.entries(all).map(([key, t]) => {
    const ts = { ...S.meta.sub_defaults, ...t.style };
    const sample = h('div', { class: 'tpl-sample', style: [
      `font-family:"${ts.font}",Arial`, `color:${ts.primary}`, `-webkit-text-stroke:${Math.min(3, ts.stroke_w / 3)}px ${ts.stroke}`,
      ts.box === 'box' ? `background:${hexA(ts.box_color, ts.box_alpha)};padding:2px 6px` : '',
      ts.glow ? `text-shadow:0 0 8px ${ts.glow}` : '', `transform:rotate(${-(ts.rotation || 0)}deg)`].join(';') },
      ts.case === 'lower' ? 'ini ' : 'INI ',
      h('span', { style: ts.highlight_mode === 'pill' || ts.highlight_mode === 'box'
        ? `background:${ts.highlight_bg};color:${ts.highlight_text};border-radius:${ts.highlight_mode === 'pill' ? 99 : 4}px;padding:0 5px;-webkit-text-stroke:0`
        : `color:${ts.highlight}` }, ts.case === 'lower' ? 'contoh' : 'CONTOH'));
    return h('div', { class: `tpl ${st.template === key ? 'on' : ''}`, title: t.desc, onclick: () => {
      const keep = { pos_x: st.pos_x };
      d.style = { ...S.meta.sub_defaults, ...t.style, template: key, ...(S.p.format === 'short' ? {} : keep) };
      markDirty(); render();
      const wpl = (d.style.words_per_line || 3) * (d.style.max_lines || 1);
      toast(`Template "${t.label}" dipakai.` + (wpl ? ' Klik "↻ Kelompokkan ulang baris" bila jumlah kata per tampilan berubah.' : ''));
    } }, sample, h('div', { class: 'tpl-name' }, t.label,
      key.startsWith('u_') ? h('span', { class: 'tpl-del', title: 'Hapus template', onclick: async (e) => {
        e.stopPropagation();
        if (!confirm(`Hapus template "${t.label}"?`)) return;
        S.meta.sub_user_templates = await api('DELETE', `/api/sub-templates/${key}`); render();
      } }, ' ✕') : null));
  }));
  const saveTpl = btn('💾 Simpan gaya ini jadi template', async () => {
    const name = prompt('Nama template (mis. "Channel Shiroe"):'); if (!name) return;
    try { S.meta.sub_user_templates = await api('POST', '/api/sub-templates', { name, style: st }); toast('Template tersimpan ✓ — bisa dipakai di semua proyek'); render(); }
    catch (e) { toast(e.message, true); }
  }, 'small');

  const sec = (title, open, ...kids) => h('details', { class: 'sub-sec', open }, h('summary', {}, title), h('div', { class: 'grid' }, kids));
  const controls = h('div', { class: 'sub-controls' },
    h('div', { class: 'row between' }, h('b', {}, '🎨 Template'), saveTpl), gallery,
    sec('🔠 Teks', true,
      f('Font', 'font', { type: 'select', options: S.meta.fonts || [] }), range('Ukuran', 'size', 30, 180),
      f('Huruf', 'case', { type: 'select', options: [['upper', 'BESAR SEMUA'], ['none', 'Seperti diucapkan'], ['lower', 'kecil semua']] }),
      f('Tebal (faux bold)', 'bold', { type: 'checkbox' }), range('Jarak huruf', 'letter_spacing', -5, 15),
      f('Warna teks', 'primary', { type: 'color' }), f('Warna kata kunci (*kata*)', 'emphasis', { type: 'color' }),
      range('Ukuran kata kunci (%)', 'emphasis_scale', 100, 150)),
    sec('✨ Sorot kata yang diucapkan', true,
      f('Mode sorot', 'highlight_mode', { type: 'select', rerender: true, options: [['color', 'Ganti warna'], ['pill', 'Latar pill'], ['box', 'Latar kotak'], ['karaoke', 'Karaoke (sapuan)'], ['none', 'Tanpa sorot']] }),
      f('Warna sorot', 'highlight', { type: 'color' }),
      st.highlight_mode === 'color' ? range('Ukuran kata aktif (%)', 'highlight_scale', 100, 140) : null,
      ['pill', 'box'].includes(st.highlight_mode) ? f('Warna latar sorot', 'highlight_bg', { type: 'color' }) : null,
      ['pill', 'box'].includes(st.highlight_mode) ? f('Warna teks tersorot', 'highlight_text', { type: 'color' }) : null),
    sec('🖌 Garis tepi, bayangan & cahaya', false,
      f('Warna garis tepi', 'stroke', { type: 'color' }), range('Tebal garis tepi', 'stroke_w', 0, 14),
      f('Warna bayangan', 'shadow', { type: 'color' }), range('Bayangan (%)', 'shadow_alpha', 0, 100),
      range('Bayangan X', 'shadow_x', -12, 12), range('Bayangan Y', 'shadow_y', -12, 12), range('Blur tepi', 'blur', 0, 10),
      h('label', { class: 'field' }, h('span', {}, 'Cahaya (glow)'), h('div', { class: 'row' },
        h('input', { type: 'checkbox', checked: !!st.glow, onchange: (e) => { st.glow = e.target.checked ? (st.glow_prev || '#00E5FF') : ''; changed(); render(); } }),
        st.glow ? h('input', { type: 'color', value: st.glow, oninput: (e) => { st.glow = st.glow_prev = e.target.value; changed(); } }) : null)),
      st.glow ? range('Lebar cahaya', 'glow_w', 2, 24) : null),
    sec('⬛ Kotak latar per baris', false,
      f('Kotak latar', 'box', { type: 'select', rerender: true, options: [['none', 'Tidak ada'], ['box', 'Ada']] }),
      st.box === 'box' ? f('Warna kotak', 'box_color', { type: 'color' }) : null,
      st.box === 'box' ? range('Kepekatan kotak (%)', 'box_alpha', 10, 100) : null,
      range('Padding kotak / pill', 'box_pad', 0, 30)),
    sec('🎬 Animasi', true,
      f('Masuk (tiap baris)', 'anim_in', { type: 'select', options: [['none', 'Tanpa'], ['pop', 'Pop'], ['bounce', 'Bounce'], ['fade', 'Fade'], ['slide_up', 'Naik dari bawah'], ['typewriter', 'Muncul per kata (ketik)']] }),
      f('Kata aktif', 'active_anim', { type: 'select', options: [['none', 'Diam'], ['pop', 'Meletup'], ['bounce', 'Memantul']] }),
      f('Keluar', 'anim_out', { type: 'select', options: [['none', 'Langsung'], ['fade', 'Fade']] }),
      f('Teks hook di 2.5 dtk awal', 'hook_text', { type: 'checkbox' })),
    sec('📍 Tata letak & posisi', true,
      posX, posY, range('Rotasi (°)', 'rotation', -30, 30),
      f('Kata per baris', 'words_per_line', { type: 'number', min: 1, max: 12 }),
      f('Jumlah baris', 'max_lines', { type: 'select', options: [[1, '1 baris'], [2, '2 baris']], num: true }),
      h('div', { class: 'row' }, ['Atas:25', 'Tengah:50', 'Bawah aman:62'].map((x) => {
        const [lbl, v] = x.split(':');
        return btn(lbl, () => { st.pos_y = +v; st.pos_x = 50; changed(); syncPosInputs(); }, 'small ghost');
      }))));

  const lines = h('div', { class: 'scroll', style: 'max-height:420px' }, (d.lines || []).map((l, i) => {
    const row = h('div', { class: 'subline' },
      btn('▶', () => { player.currentTime = l.start; player.play(); }, 'small ghost'),
      field('', l, 'start', { type: 'number', step: 0.05 }), field('', l, 'end', { type: 'number', step: 0.05 }),
      h('div', { class: 'row' }, h('div', { class: 'grow' }, field('', l, 'text', { onchange: draw })),
        btn('✕', () => { d.lines.splice(i, 1); markDirty(); render(); }, 'small ghost danger')));
    lineEls.push(row);
    return row;
  }));
  box.append(h('div', { class: 'sub-editor' },
    h('div', { class: 'sub-left' }, stage, isClip ? null : player,
      h('div', { class: 'row' }, exactBtn, h('span', { class: 'muted small' }, 'Geser teks di layar untuk memindah. "Pratinjau akurat" = hasil render sebenarnya (libass).'))),
    controls));
  box.append(h('details', { open: true }, h('summary', {}, `📝 Teks per baris (${(d.lines || []).length}) — bungkus *kata* untuk penekanan`), lines));
  return box;
}

/* Sama dengan subtitles.normalize_style di server: terjemahkan gaya lama (uppercase/outline/position/
   animation/karaoke/active_scale) ke field editor baru, lalu isi default. */
function normStyle(raw) {
  const s = { ...raw }, short = S.p.format === 'short';
  if (!s.template && s.preset) s.template = s.preset;
  if ('uppercase' in s && !('case' in s)) s.case = s.uppercase ? 'upper' : 'none';
  if ('outline' in s && !('stroke_w' in s)) s.stroke_w = s.outline;
  if ('active_scale' in s && !('highlight_scale' in s)) s.highlight_scale = s.active_scale;
  if ('animation' in s && !('active_anim' in s)) { s.active_anim = s.animation === 'pop' ? 'pop' : 'none'; if (!('anim_in' in s)) s.anim_in = 'none'; }
  if ('karaoke' in s && !('highlight_mode' in s)) s.highlight_mode = s.karaoke ? 'color' : 'none';
  if ('position' in s && !('pos_y' in s)) s.pos_y = (short ? { middle: 50, lower: 53, bottom: 60 } : { middle: 50, lower: 75, bottom: 88 })[s.position] || 60;
  const base = { ...S.meta.sub_defaults };
  if (!short) Object.assign(base, { size: 56, case: 'none', words_per_line: 7, pos_y: 88, anim_in: 'fade', active_anim: 'none', highlight_scale: 100, stroke_w: 3 });
  return { ...base, ...s };
}

function hexA(hex, pct) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex || 'transparent';
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${(pct ?? 100) / 100})`;
}

// ================================================================ TAHAP 6
function rRender() {
  const d = S.draft, box = h('div');
  box.append(h('div', { class: 'row' }, btn('🎬 Render video final (FFmpeg)', () => runAction('render.run'), 'primary'),
    h('span', { class: 'muted' }, S.p.format === 'short' ? '1080×1920 · 30fps' : '1920×1080 · 30fps')));
  if (S.p.mode === 'scratch') {
    d.cut_style = d.cut_style || (S.p.format === 'short' ? 'dinamis' : 'statis');
    box.append(h('div', { style: 'max-width:420px' }, field('Gaya potongan visual', d, 'cut_style', { type: 'select', options: [
      ['dinamis', 'Dinamis — shot baru tiap kalimat, zoom bergantian (cocok Shorts)'], ['statis', 'Statis — 1 shot per scene']] }),
      field('Transisi antar-scene', d, 'transition', { type: 'select', options: [
        ['', 'Otomatis (fade bila dinamis)'], ['fade', 'Fade'], ['geser', 'Geser'], ['zoom', 'Zoom in'], ['hitam', 'Fade hitam'], ['lingkaran', 'Lingkaran'], ['none', 'Tanpa transisi']] })));
  }
  box.append(hint('Menggabungkan visual + voice over + musik (auto-ducking) + SFX + burn-in subtitle. Untuk font khusus, taruh file .ttf di data/fonts/. Ubah gaya potongan → klik "Simpan Edit Manual" lalu render.'));
  if (d.output_path) {
    box.append(h('video', { class: 'preview-v', src: media(d.output_path), controls: true }));
    box.append(h('div', { class: 'row' },
      h('a', { href: media(d.output_path), download: `${S.p.id}.mp4` }, '⬇ Unduh MP4'),
      h('a', { href: media('subs.srt'), download: `${S.p.id}.srt` }, '⬇ Subtitle .srt'),
      d.drive_file_id ? h('a', { href: `https://drive.google.com/file/d/${d.drive_file_id}/view`, target: '_blank' }, '☁ Buka di Google Drive') : null,
      d.duration ? h('span', { class: 'muted' }, `durasi ${fmtT(d.duration)}`) : null));
  }
  return box;
}

// ================================================================ TAHAP Thumbnail
function rThumbnail() {
  const d = S.draft, box = h('div');
  const fmt = S.p.format, tw = fmt === 'short' ? 300 : 560;
  d.frames = d.frames || []; d.thumbs = d.thumbs || []; d.designs = d.designs || [];
  const tpls = S.meta.thumb_templates || {};
  // desain yang sedang diedit: dari slot terpilih, atau template awal + teks default
  if (!S.tw || S.tw.pid !== S.p.id) {
    const cur = d.designs[d.selected || 0];
    S.tw = { pid: S.p.id, slot: d.thumbs.length ? (d.selected || 0) : null,
      design: cur ? { ...cur } : { template: fmt === 'short' ? 'atas' : 'kiri', text: '' } };
  }
  const ds = S.tw.design;
  const full = () => ({ ...S.meta.thumb_defaults, ...((tpls[ds.template] || {}).design || {}), ...ds });

  box.append(h('div', { class: 'row' },
    btn(d.thumbs.length ? '⚡ Buat ulang 3 variasi otomatis' : '⚡ Buat 3 thumbnail otomatis', async () => {
      if (d.thumbs.length && !confirm('Ganti 3 slot pertama dengan variasi otomatis baru?')) return;
      S.tw = null; await runAction('thumbnail.auto');
    }, d.thumbs.length ? '' : 'primary'),
    btn('🎞 Ambil kandidat frame', () => runAction('thumbnail.frames'), 'ghost'),
    btn('📁 Unggah gambar latar', () => uploadFile(`/api/projects/${S.p.id}/thumbnail/upload`, 'image/*'), 'ghost'),
    S.p.mode === 'clipper' ? h('span', { class: 'row small' }, 'frame di detik',
      h('input', { type: 'number', step: 0.1, min: 0, style: 'width:80px', id: 'tf-t' }),
      btn('+', () => runAction('thumbnail.frames', { t: +$('#tf-t').value || 0 }), 'small')) : null));
  box.append(hint('Thumbnail diambil dari video TANPA subtitle. Tips: 1-4 kata, kontras tinggi, wajah ekspresif. Bungkus *kata* untuk warna sorot, Enter = baris baru. Buat 2-3 variasi untuk fitur "Test & Compare" YouTube.'));

  // --- kandidat frame
  if (d.frames.length) {
    box.append(h('div', { class: 'frame-strip' }, d.frames.map((fr) => h('div', {
      class: `fr ${d.frame === fr.path ? 'on' : ''}`, title: `skor ${fr.score}${fr.face ? ' · ada wajah' : ''}`,
      onclick: () => { d.frame = fr.path; markDirty(); render(); } },
    h('img', { src: media(fr.path) }), fr.face ? h('span', { class: 'fr-face' }, '🙂') : null))));
  }
  if (!d.frame) { box.append(h('div', { class: 'muted' }, 'Ambil kandidat frame dulu (atau klik "Buat 3 thumbnail otomatis").')); return box; }

  // --- editor
  const img = h('img', { class: 'th-prev', style: `width:${tw}px`, title: 'Klik untuk memindah teks ke titik ini' });
  let timer, seq = 0;
  const refresh = (now) => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const my = ++seq;
      img.classList.add('loading');
      try {
        const blob = await api('POST', `/api/projects/${S.p.id}/thumbnail/preview`, { frame: d.frame, design: ds });
        if (my === seq) img.src = URL.createObjectURL(blob);
      } catch (e) { toast(e.message, true); }
      finally { if (my === seq) img.classList.remove('loading'); }
    }, now ? 0 : 450);
  };
  img.onclick = (e) => {
    const r = img.getBoundingClientRect(), f0 = full();
    const x = ((e.clientX - r.left) / r.width) * 100, y = ((e.clientY - r.top) / r.height) * 100;
    ds.text_x = Math.round(f0.align === 'left' ? x : f0.align === 'right' ? x : x); ds.text_y = Math.round(y);
    refresh(true); render();
  };
  const F = full();
  const set = (key, v) => { ds[key] = v; refresh(); };
  const fld = (label, key, o = {}) => {
    const proxy = { [key]: F[key] };
    return field(label, proxy, key, { ...o, dirty: () => {}, onchange: (v) => { set(key, v); if (o.rerender) render(); } });
  };
  const rng = (label, key, min, max, step = 1) => fld(label, key, { type: 'range', min, max, step });
  const tgal = h('div', { class: 'tpl-grid' }, Object.entries(tpls).map(([k, t]) => h('div', {
    class: `tpl ${F.template === k ? 'on' : ''}`, title: t.desc,
    onclick: () => { S.tw.design = { template: k, text: ds.text || '' }; refresh(true); render(); } },
  h('div', { class: 'tpl-name' }, t.label), h('div', { class: 'muted small' }, t.desc))));
  const sec = (title, open, ...kids) => h('details', { class: 'sub-sec', open }, h('summary', {}, title), h('div', { class: 'grid' }, kids));
  const textBox = h('textarea', { rows: 2, placeholder: 'kosong = hook / judul otomatis', oninput: (e) => set('text', e.target.value) });
  textBox.value = ds.text || '';
  const controls = h('div', { class: 'sub-controls' },
    h('b', {}, '🧩 Template layout'), tgal,
    sec('🔠 Teks', true,
      h('label', { class: 'field', style: 'grid-column:1/-1' }, h('span', {}, 'Teks (bungkus *kata* untuk sorot)'), textBox),
      fld('Font', 'font', { type: 'select', options: S.meta.fonts || [] }), rng('Ukuran (% lebar)', 'size', 4, 30, 0.5),
      fld('Huruf', 'case', { type: 'select', options: [['upper', 'BESAR'], ['none', 'Apa adanya'], ['lower', 'kecil']] }),
      fld('Rata', 'align', { type: 'select', options: [['center', 'Tengah'], ['left', 'Kiri'], ['right', 'Kanan']] }),
      fld('Warna teks', 'text_color', { type: 'color' }), fld('Warna sorot', 'highlight_color', { type: 'color' }),
      h('label', { class: 'field' }, h('span', {}, 'Kotak di belakang kata sorot'), h('div', { class: 'row' },
        h('input', { type: 'checkbox', checked: !!F.highlight_bg, onchange: (e) => { set('highlight_bg', e.target.checked ? '#E50914' : ''); render(); } }),
        F.highlight_bg ? h('input', { type: 'color', value: F.highlight_bg, oninput: (e) => set('highlight_bg', e.target.value) }) : null)),
      fld('Warna garis tepi', 'stroke_color', { type: 'color' }), rng('Tebal garis tepi', 'stroke_w', 0, 20),
      rng('Bayangan', 'shadow', 0, 100), rng('Rotasi (°)', 'rotation', -15, 15),
      rng('Posisi X (%)', 'text_x', 0, 100), rng('Posisi Y (%)', 'text_y', 0, 100), rng('Lebar maks (%)', 'max_width', 30, 95)),
    sec('🌄 Latar', false,
      rng('Gelapkan', 'darken', 0, 80), fld('Gradasi gelap', 'gradient', { type: 'select', options: [['none', 'Tidak'], ['bottom', 'Bawah'], ['top', 'Atas'], ['left', 'Kiri'], ['right', 'Kanan']] }),
      rng('Kekuatan gradasi', 'gradient_strength', 0, 100), rng('Saturasi', 'saturation', 0.6, 1.8, 0.05), rng('Kontras', 'contrast', 0.7, 1.6, 0.05),
      rng('Blur latar', 'bg_blur', 0, 20), rng('Vignette', 'vignette', 0, 100), rng('Zoom', 'zoom', 1, 2, 0.05),
      rng('Posisi subjek (% dari kiri)', 'subject_x', 10, 90)),
    sec('✂ Potong subjek & outline', false,
      fld('Potong subjek (butuh wajah)', 'cutout', { type: 'checkbox', rerender: true }),
      fld('Warna outline', 'outline_color', { type: 'color' }), rng('Tebal outline', 'outline_w', 0, 24)),
    sec('➡ Stiker', false,
      fld('Stiker', 'sticker', { type: 'select', rerender: true, options: [['none', 'Tidak ada'], ['arrow', 'Panah + lingkaran'], ['circle', 'Lingkaran']] }),
      fld('Warna', 'sticker_color', { type: 'color' }), rng('X (%)', 'sticker_x', 0, 100), rng('Y (%)', 'sticker_y', 0, 100)));

  const slot = S.tw.slot;
  const saveRow = h('div', { class: 'row' },
    slot !== null && slot < d.thumbs.length ? btn(`💾 Simpan ke variasi ${slot + 1}`, () => runAction('thumbnail.render', { frame: d.frame, design: ds, slot }), 'primary') : null,
    btn('➕ Simpan sebagai variasi baru', async () => { S.tw.slot = d.thumbs.length; await runAction('thumbnail.render', { frame: d.frame, design: ds, slot: d.thumbs.length }); }, slot === null ? 'primary' : ''));

  // --- slot variasi
  const slots = h('div', { class: 'row', style: 'align-items:flex-start;flex-wrap:wrap' }, d.thumbs.map((t, i) => h('div', { class: `slot ${d.selected === i ? 'on' : ''}` },
    h('img', { src: media(t), style: `width:${fmt === 'short' ? 110 : 200}px`, onclick: () => {
      d.selected = i; markDirty(); S.tw = { pid: S.p.id, slot: i, design: { ...(d.designs[i] || {}) } };
      if ((d.designs[i] || {}).frame) d.frame = d.designs[i].frame;
      render(); } }),
    h('div', { class: 'row small' }, d.selected === i ? h('b', {}, '✓ dipakai') : h('span', { class: 'muted' }, `variasi ${i + 1}`),
      btn('✕', async () => { if (confirm('Hapus variasi ini?')) { S.tw = null; await runAction('thumbnail.remove', { slot: i }, true); } }, 'small ghost danger')))));

  box.append(h('div', { class: 'sub-editor' },
    h('div', { class: 'sub-left' }, img, saveRow,
      d.thumbs.length ? h('div', {}, h('b', {}, 'Variasi tersimpan'), h('div', { class: 'muted small' }, 'Klik untuk mengedit; variasi bertanda ✓ dipakai saat upload. Klik "Simpan Edit Manual" setelah mengganti pilihan.'), slots) : null),
    controls));
  refresh(true);
  return box;
}

// ================================================================ TAHAP Upload
function rPublish() {
  const d = S.draft, box = h('div');
  if (!S.meta.google) box.append(hint('Google belum terhubung. Jalankan: python run.py --setup-google', 'warn'));
  box.append(h('div', { class: 'row' }, btn('✨ Generate judul, deskripsi & tag (AI)', () => runAction('publish.metadata'), 'primary')));
  const counter = h('span', { class: 'muted small' }, `${(d.title || '').length}/100`);
  box.append(field('Judul', d, 'title', { onchange: (v) => (counter.textContent = `${v.length}/100`) }), counter);
  box.append(field('Deskripsi', d, 'description', { type: 'textarea', rows: 8 }));
  box.append(field('Tag (pisahkan koma)', d, 'tags', { type: 'csv' }));
  box.append(h('div', { class: 'grid' },
    field('Privasi', d, 'privacy', { type: 'select', options: [['private', 'Private'], ['unlisted', 'Unlisted'], ['public', 'Public']] }),
    field('Jadwal tayang (kosong = sesuai privasi)', d, 'publish_at', { type: 'datetime-local' }),
    field('Kategori', d, 'category_id', { type: 'select', options: [['22', 'People & Blogs'], ['24', 'Entertainment'], ['27', 'Education'],
      ['28', 'Science & Tech'], ['20', 'Gaming'], ['10', 'Music'], ['17', 'Sports'], ['25', 'News & Politics'], ['19', 'Travel']] }),
    field('Konten untuk anak-anak', d, 'made_for_kids', { type: 'checkbox' }),
    field('Berisi konten AI/sintetis yang tampak nyata', d, 'synthetic_media', { type: 'checkbox' })));
  const aiUsed = Object.values((S.p.assets && S.p.assets.scenes) || {}).some((sl) => sl.selected && sl.selected.provider === 'ai');
  if (aiUsed) box.append(hint('⚠️ Proyek ini memakai GAMBAR AI di tahap visual. Bila gambarnya tampak nyata (orang/tempat/peristiwa), centang "konten AI/sintetis".', 'warn'));
  box.append(hint('Centang "konten AI/sintetis" bila video memuat gambar/video buatan AI yang tampak nyata (orang, tempat, atau peristiwa yang tidak pernah terjadi). Suara AI + stok footage biasa umumnya tidak wajib — lihat kebijakan YouTube "altered or synthetic content".'));
  if (d.pinned_comment) box.append(field('Komentar untuk di-pin (salin manual setelah tayang)', d, 'pinned_comment', { type: 'textarea', rows: 2 }));
  box.append(hint('Jadwal memakai fitur publishAt bawaan YouTube: video diunggah private lalu YouTube menayangkannya otomatis — komputer tidak perlu menyala saat jam tayang. Kuota upload API ±100 video/hari. Login Google (mode Testing) perlu dihubungkan ulang tiap 7 hari.'));
  box.append(h('div', { class: 'row' }, btn('🚀 Upload & Jadwalkan ke YouTube', () => {
    if (!d.title) return toast('Judul masih kosong', true);
    if (S.p.publish.video_id) {
      if (confirm(`Proyek ini SUDAH diunggah (${S.p.publish.url}).\nUpload ulang akan membuat video DUPLIKAT di channel. Tetap upload ulang?`)) runAction('publish.upload', { force: true });
      return;
    }
    if (confirm(`Upload "${d.title}" ke channel YouTube Anda${d.publish_at ? `, tayang ${d.publish_at}` : ` sebagai ${d.privacy}`}?`)) runAction('publish.upload');
  }, 'success')));
  if (d.video_id) box.append(h('div', { class: 'card' }, '✅ Terunggah: ', h('a', { href: d.url, target: '_blank' }, d.url), ` · status: ${d.status}`));

  // --- thumbnail (dibuat di tahap Thumbnail)
  const th = S.p.thumbnail || {}, thumbs = th.thumbs || [], tsel = Math.min(th.selected || 0, Math.max(0, thumbs.length - 1));
  const tb = h('div', { class: 'card' }, h('b', {}, '🖼 Thumbnail'),
    thumbs.length ? h('div', { class: 'row', style: 'align-items:flex-start' },
      h('img', { src: media(thumbs[tsel]), style: `width:${S.p.format === 'short' ? 120 : 240}px;border-radius:8px` }),
      h('div', {},
        h('div', { class: 'muted small' }, `Variasi ${tsel + 1} dari ${thumbs.length} dipakai. Ganti/ubah di tahap Thumbnail.`),
        d.video_id ? btn('⬆ Pasang thumbnail ke YouTube', () => runAction('publish.thumbnail', { index: tsel }), 'small primary')
          : h('div', { class: 'muted small' }, 'Bisa dipasang setelah video terunggah (YouTube perlu selesai memproses video).'),
        th.uploaded ? h('div', { class: 'muted small' }, `terpasang ${th.uploaded}`) : null))
      : h('div', { class: 'muted small' }, 'Belum ada thumbnail — buat di tahap Thumbnail.'));
  box.append(tb);
  return box;
}

const RENDER = { research: rResearch, script: rScript, assets: rAssets, audio: rAudio, subtitles: rSubtitles, render: rRender, thumbnail: rThumbnail, publish: rPublish };

// ---------------------------------------------------------------- sidebar
async function refreshList() {
  const all = await api('GET', '/api/projects');
  S.all = all;
  S.counts = { scratch: 0, clipper: 0, publish: 0 };
  all.forEach((p) => {
    S.counts[p.mode] = (S.counts[p.mode] || 0) + (p.parent ? 0 : 1);  // video hasil Clipper ikut proyek sumbernya
    if (readyForUpload(p) && !p.publish.video_id) S.counts.publish += 1;  // yang belum diunggah
  });
  document.querySelectorAll('#tabs button').forEach((b) => { $('.cnt', b).textContent = S.counts[b.dataset.mode] || ''; });
  const ul = $('#project-list'); ul.innerHTML = '';
  if (S.tab === 'publish') return publishList(all, ul);
  // Clipper: video hasil (proyek anak) tampil bersarang di bawah proyek sumbernya, bukan sejajar.
  const ids = new Set(all.map((p) => p.id));
  const list = all.filter((p) => p.mode === S.tab && !(p.parent && ids.has(p.parent)));
  if (!list.length) ul.append(h('li', { class: 'muted small', style: 'cursor:default' },
    S.tab === 'clipper' ? 'Belum ada proyek clipper.' : 'Belum ada proyek dari nol.'));
  list.forEach((p) => {
    const done = Object.values(p.stage_status).filter((s) => s === 'approved').length;
    const kids = kidsOf(p.id);
    const openGroup = S.p && (S.p.id === p.id || S.p.parent === p.id);
    const status = kids.length && isContainer(p)
      ? `🎞 ${kids.length} video · ${kids.filter((q) => readyForUpload(q)).length} siap upload`
      : readyForUpload(p) ? (p.publish.video_id ? '✅ sudah diunggah' : '📅 siap upload') : labelsFor(p.mode)[S.meta.stages[p.current_stage]];
    ul.append(h('li', { class: S.p && S.p.id === p.id ? 'active' : '', onclick: () => openProject(p.id) },
      h('div', {}, p.mode === 'clipper' && kids.length ? `📁 ${p.title}` : p.title),
      h('div', { class: 'muted small' }, `${p.format === 'short' ? 'Short' : 'Long'} · ${status}`),
      kids.length && isContainer(p) ? null : h('div', { class: 'pbar' }, h('i', { style: `width:${Math.min(done, PUB) / PUB * 100}%` }))));
    if (openGroup) kids.forEach((q, n) => ul.append(h('li', { class: `child ${S.p.id === q.id ? 'active' : ''}`, onclick: () => openProject(q.id) },
      h('div', { class: 'small' }, `↳ ${n + 1}. ${q.title}`), h('div', { class: 'muted small' }, kidStatus(q)))));
  });
}


// ================================================================ CLIPPER SEDERHANA (ala 2short.ai)
/* Satu halaman: tempel link → "Buat Klip" → galeri klip jadi. Wizard per tahap tetap ada sebagai
   "Mode lanjutan" & untuk mengedit satu klip. */
S.adv = {};
const CLIP_PHASES = ['Mengunduh video', 'Transkrip suara', 'Mencari momen viral', 'Membuat klip'];
const clipOpts = () => {
  let o = {};
  try { o = JSON.parse(lsGet('clipOpts') || '{}'); } catch (_) {}
  return { duration: 'auto', count: 0, template: 'pop', crop_mode: 'face', music: false, format: 'short', direction: '', ...o };
};
const saveClipOpts = (o) => lsSet('clipOpts', JSON.stringify(o));

function seg(opts, key, choices, onchange) {
  return h('div', { class: 'seg' }, choices.map(([v, l]) => h('button', {
    class: String(opts[key]) === String(v) ? 'on' : '', onclick: () => { opts[key] = v; onchange(); } }, l)));
}

function tplCard(key, t, on, onclick) {
  const ts = { ...S.meta.sub_defaults, ...t.style };
  return h('div', { class: `tpl ${on ? 'on' : ''}`, title: t.desc, onclick },
    h('div', { class: 'tpl-sample', style: [`font-family:"${ts.font}",Arial`, `color:${ts.primary}`,
      `-webkit-text-stroke:${Math.min(3, ts.stroke_w / 3)}px ${ts.stroke}`,
      ts.box === 'box' ? `background:${hexA(ts.box_color, ts.box_alpha)};padding:2px 6px` : '',
      ts.glow ? `text-shadow:0 0 8px ${ts.glow}` : ''].join(';') },
    ts.case === 'lower' ? 'ini ' : 'INI ',
    h('span', { style: ['pill', 'box'].includes(ts.highlight_mode)
      ? `background:${ts.highlight_bg};color:${ts.highlight_text};border-radius:${ts.highlight_mode === 'pill' ? 99 : 4}px;padding:0 5px;-webkit-text-stroke:0`
      : `color:${ts.highlight}` }, ts.case === 'lower' ? 'viral' : 'VIRAL')),
    h('div', { class: 'tpl-name' }, t.label));
}

function clipOptionsView(o, rerender) {
  const tpls = { ...(S.meta.sub_templates || {}), ...(S.meta.sub_user_templates || {}) };
  const upd = () => { saveClipOpts(o); rerender(); };
  return h('div', { class: 'ch-opts' },
    h('div', { class: 'ch-opt' }, h('b', {}, 'Durasi klip'),
      seg(o, 'duration', [['auto', 'Otomatis'], ['pendek', '15–30 dtk'], ['sedang', '30–60 dtk'], ['panjang', '60–90 dtk']], upd)),
    h('div', { class: 'ch-opt' }, h('b', {}, 'Jumlah klip'),
      seg(o, 'count', [[0, 'Semua yang bagus'], [3, '3'], [5, '5'], [10, '10']], upd)),
    h('div', { class: 'ch-opt' }, h('b', {}, 'Gaya subtitle'),
      h('div', { class: 'tpl-row' }, Object.entries(tpls).filter(([k]) => k !== 'klasik').map(([k, t]) =>
        tplCard(k, t, o.template === k, () => { o.template = k; upd(); })))),
    h('details', { class: 'ch-more', open: !!S.brandOpen, ontoggle: (e) => { S.brandOpen = e.target.open; } },
      h('summary', {}, '🏷 Logo & overlay (semua klip)'),
      brandPanel({ format: o.format })),
    h('details', { class: 'ch-more' }, h('summary', {}, 'Opsi lain'),
      h('div', { class: 'ch-opt' }, h('b', {}, 'Bingkai'),
        seg(o, 'crop_mode', [['face', 'Ikuti wajah'], ['blur', 'Latar blur'], ['center', 'Crop tengah']], upd)),
      h('div', { class: 'ch-opt' }, h('b', {}, 'Musik latar'), seg(o, 'music', [[false, 'Tidak'], [true, 'Ya (otomatis pelan saat ada suara)']], upd)),
      h('div', { class: 'ch-opt' }, h('b', {}, 'Format'), seg(o, 'format', [['short', 'Vertikal 9:16'], ['long', 'Lebar 16:9']], upd)),
      h('div', { class: 'ch-opt' }, h('b', {}, 'Arahan AI (opsional)'),
        h('input', { value: o.direction || '', placeholder: 'mis. cari momen lucu / fokus ke tips praktis',
          oninput: (e) => { o.direction = e.target.value; saveClipOpts(o); } }))));
}

// ---------------------------------------------------------------- branding Clipper (logo + overlay)
/* Pengaturan global (data/brand.json di backend) → otomatis dipakai SEMUA video hasil Clipper,
   termasuk klip dari video sumber lain. Disimpan otomatis setiap kali diubah. */
const BRAND_POS = [['tl', '↖'], ['tc', '↑'], ['tr', '↗'], ['ml', '←'], ['c', '•'], ['mr', '→'], ['bl', '↙'], ['bc', '↓'], ['br', '↘']];
let brandSaveT = null;

async function loadBrand() {
  try { S.brand = await api('GET', '/api/brand'); } catch (e) { toast(e.message, true); S.brand = null; }
  return S.brand;
}

function brandPanel(sample) {
  const box = h('div', { class: 'brand-panel' });
  const draw = () => {
    box.innerHTML = '';
    const b = S.brand;
    if (!b) { box.append(h('div', { class: 'muted small' }, 'Memuat pengaturan…')); loadBrand().then((x) => x && draw()); return; }
    const save = (redraw = true) => {
      if (redraw) draw();
      clearTimeout(brandSaveT);
      brandSaveT = setTimeout(async () => {
        try { S.brand = await api('PUT', '/api/brand', { enabled: b.enabled, logo: b.logo, overlay: b.overlay }); } catch (e) { toast(e.message, true); }
      }, 400);
    };
    const pick = (slot, accept) => {
      const inp = h('input', { type: 'file', accept });
      inp.onchange = async () => {
        if (!inp.files[0]) return;
        const fd = new FormData(); fd.append('file', inp.files[0]);
        try { S.brand = await api('POST', `/api/brand/${slot}`, fd); S.brandVer = Date.now(); toast('Tersimpan ✓'); draw(); } catch (e) { toast(e.message, true); }
      };
      inp.click();
    };
    const drop = async (slot) => { try { S.brand = await api('DELETE', `/api/brand/${slot}`); draw(); } catch (e) { toast(e.message, true); } };
    const url = (f) => U(`/brand/${f}?v=${S.brandVer || 0}`);
    const isVid = (f) => /\.(mp4|webm|mov)$/i.test(f || '');
    const slider = (it, key, min, max, step, label, fmt) => {
      const out = h('span', { class: 'muted small' }, fmt(it[key]));
      return h('label', { class: 'bp-slider' }, h('span', {}, label),
        h('input', { type: 'range', min, max, step, value: it[key], oninput: (e) => { it[key] = +e.target.value; out.textContent = fmt(it[key]); prevDraw(); save(false); } }),
        out);
    };
    const posGrid = (it, full) => h('div', { class: 'bp-pos' },
      full ? h('button', { class: `bp-full ${it.pos === 'full' ? 'on' : ''}`, onclick: () => { it.pos = 'full'; save(); } }, 'Layar penuh') : null,
      h('div', { class: 'bp-grid' }, BRAND_POS.map(([v, l]) => h('button', { class: it.pos === v ? 'on' : '', title: v, onclick: () => { it.pos = v; save(); } }, l))));
    const fileRow = (slot, it, accept, hint) => h('div', { class: 'row' },
      it.file ? h('span', { class: 'badge' }, it.file) : h('span', { class: 'muted small' }, hint),
      h('button', { class: 'small', onclick: () => pick(slot, accept) }, it.file ? 'Ganti' : '📁 Pilih file'),
      it.file ? h('button', { class: 'small ghost danger', onclick: () => drop(slot) }, 'Hapus') : null);

    // pratinjau (perkiraan CSS; hasil akhir dirender FFmpeg dengan posisi/ukuran yang sama)
    const fmt = (sample && sample.format) || 'short';
    const PW = fmt === 'short' ? 180 : 300, PH = fmt === 'short' ? 320 : 169;
    const prev = h('div', { class: 'bp-prev', style: `width:${PW}px;height:${PH}px` });
    const place = (el, it) => {
      if (it.pos === 'full') { el.style.cssText = 'left:0;top:0;width:100%;height:100%;object-fit:fill'; }
      else {
        const m = PW * it.margin / 100, p = it.pos, w = PW * it.size / 100;
        let css = `width:${w}px;`;
        css += p === 'c' || p[1] === 'c' ? `left:${(PW - w) / 2}px;` : p[1] === 'l' ? `left:${m}px;` : `right:${m}px;`;
        css += p === 'c' || p[0] === 'm' ? 'top:50%;transform:translateY(-50%);' : p[0] === 't' ? `top:${m}px;` : `bottom:${m}px;`;
        el.style.cssText = css;
      }
      el.style.opacity = it.opacity;
      if (it.chroma) el.style.mixBlendMode = 'screen';
    };
    const prevDraw = () => {
      prev.innerHTML = '';
      if (sample && sample.thumb) prev.append(h('img', { class: 'bp-bg', src: sample.thumb }));
      if (!b.enabled) return;
      if (b.overlay.file) {
        const el = isVid(b.overlay.file) ? h('video', { src: url(b.overlay.file), autoplay: true, muted: true, loop: true, playsInline: true }) : h('img', { src: url(b.overlay.file) });
        el.className = 'bp-layer'; place(el, b.overlay); prev.append(el);
      }
      if (b.logo.file) { const el = h('img', { class: 'bp-layer', src: url(b.logo.file) }); place(el, b.logo); prev.append(el); }
      prev.append(h('div', { class: 'bp-sub' }, 'SUBTITLE'));
    };
    prevDraw();

    const L = b.logo, O = b.overlay;
    box.append(
      h('label', { class: 'row' }, h('input', { type: 'checkbox', checked: b.enabled, onchange: (e) => { b.enabled = e.target.checked; save(); } }),
        h('b', {}, 'Pasang logo & overlay di semua video hasil Clipper')),
      h('div', { class: 'bp-body' }, prev,
        h('div', { class: 'bp-cols' },
          h('div', { class: 'bp-col' }, h('h3', {}, '🏷 Logo'),
            fileRow('logo', L, 'image/png,image/jpeg,image/webp', 'PNG transparan disarankan'),
            L.file ? [posGrid(L, false),
              slider(L, 'size', 5, 50, 1, 'Ukuran', (v) => `${v}% lebar`),
              slider(L, 'opacity', 0.1, 1, 0.05, 'Transparansi', (v) => `${Math.round(v * 100)}%`),
              slider(L, 'margin', 0, 15, 0.5, 'Jarak tepi', (v) => `${v}%`)] : null),
          h('div', { class: 'bp-col' }, h('h3', {}, '🎞 Overlay'),
            fileRow('overlay', O, 'image/*,video/mp4,video/webm,video/quicktime,.gif,.mov', 'Bingkai PNG / animasi (mp4, webm, gif)'),
            O.file ? [posGrid(O, true),
              O.pos !== 'full' ? slider(O, 'size', 5, 100, 1, 'Ukuran', (v) => `${v}% lebar`) : null,
              O.pos !== 'full' ? slider(O, 'margin', 0, 15, 0.5, 'Jarak tepi', (v) => `${v}%`) : null,
              slider(O, 'opacity', 0.1, 1, 0.05, 'Transparansi', (v) => `${Math.round(v * 100)}%`),
              h('label', { class: 'row small' }, h('input', { type: 'checkbox', checked: O.chroma, onchange: (e) => { O.chroma = e.target.checked; save(); } }),
                'Buang latar hijau (green screen)'),
              h('div', { class: 'bp-slider' }, h('span', {}, 'Tampil'),
                seg(O, 'show', [['always', 'Sepanjang video'], ['first', 'Awal'], ['last', 'Akhir']], () => save())),
              O.show !== 'always' ? slider(O, 'secs', 1, 15, 1, 'Durasi', (v) => `${v} dtk ${O.show === 'first' ? 'pertama' : 'terakhir'}`) : null] : null))),
      h('div', { class: 'muted small' }, 'Tersimpan otomatis & dipakai untuk semua klip berikutnya (juga dari video lain). Lapisan: video → overlay → logo → subtitle. ',
        fmt === 'short' ? 'Shorts: hindari sisi kanan & bawah (tertutup tombol YouTube).' : ''));
  };
  draw();
  return box;
}

function clipHome() {
  applyTheme('clipper');
  const main = $('#main'); main.innerHTML = '';
  const o = clipOpts();
  const url = h('input', { class: 'ch-url', placeholder: 'Tempel link YouTube di sini…', autofocus: true });
  const go = async (file) => {
    const u = url.value.trim();
    if (!file && !/^https?:\/\//.test(u)) { toast('Tempel link video (https://…) dulu.', true); url.focus(); return; }
    try {
      const p = await api('POST', '/api/projects', { mode: 'clipper', format: o.format, title: '', keyword: '' });
      await loadProject(p.id, false, 'clipper');
      if (file) {
        await uploadFile(`/api/projects/${p.id}/upload-source`, 'video/*');
        if (!((S.p.research || {}).source || {}).path) return;
      }
      startAutoClip(file ? {} : { url: u });
    } catch (e) { toast(e.message, true); }
  };
  url.onkeydown = (e) => { if (e.key === 'Enter') go(); };
  const opts = h('div');
  const draw = () => { opts.innerHTML = ''; opts.append(clipOptionsView(o, draw)); };
  draw();
  main.append(h('div', { class: 'clip-home' },
    h('h1', {}, '✂ Ubah video panjang jadi klip viral'),
    h('p', { class: 'muted' }, 'AI mencari momen terbaik, memotong ke 9:16 mengikuti wajah, memberi subtitle & thumbnail — tinggal unduh atau jadwalkan.'),
    h('div', { class: 'ch-input' }, url, h('button', { class: 'primary ch-go', onclick: () => go() }, '✨ Buat Klip')),
    h('div', { class: 'row', style: 'justify-content:center' }, h('span', { class: 'muted small' }, 'atau'),
      h('button', { class: 'ghost small', onclick: () => go(true) }, '📁 Unggah file video')),
    opts,
    h('div', { class: 'muted small', style: 'text-align:center;margin-top:12px' },
      'Gunakan hanya video milik sendiri, berlisensi CC, atau yang sudah diizinkan pemiliknya.')));
}

async function startAutoClip(extra = {}) {
  const o = clipOpts();
  S.clipRun = S.p.id;
  render();
  const r = await runAction('research.auto_clip', { ...extra, duration: o.duration, count: o.count, template: o.template,
    crop_mode: o.crop_mode, music: o.music, direction: o.direction });
  S.clipRun = null;
  if (r) toast(`${r.created.length} klip jadi 🎉${r.failed.length ? ` (${r.failed.length} gagal)` : ''}`);
  await refreshList(); render();
}

function clipProgressView(msg) {
  const m = /^\[(\d)\/4\]\s*(.*)$/.exec(msg || '');
  const cur = m ? +m[1] - 1 : 0;
  return h('div', { class: 'clip-progress' },
    h('div', { class: 'cp-steps' }, CLIP_PHASES.map((ph, i) => h('div', { class: `cp-step ${i < cur ? 'done' : i === cur ? 'now' : ''}` },
      h('span', { class: 'cp-dot' }, i < cur ? '✓' : String(i + 1)), ph))),
    h('div', { class: 'cp-msg' }, h('span', { class: 'spin' }), ' ', m ? m[2] : (msg || 'Memulai…')),
    h('div', { class: 'muted small' }, 'Boleh ditinggal / buka tab lain — proses tetap jalan. Video 1 jam biasanya selesai dalam beberapa menit.'));
}

function renderClipSource() {
  const p = S.p, main = $('#main'); main.innerHTML = '';
  applyTheme('clipper');
  const src = (p.research || {}).source || {};
  const kids = (S.all || []).filter((c) => c.parent === p.id);
  const running = S.clipRun === p.id || (p._jobs || []).some((j) => ['research.auto_clip', 'script.produce_clips', 'research.rebrand'].includes(j.kind));
  main.append(h('div', { class: 'row between' },
    h('div', {}, h('h1', {}, p.title || 'Video baru'),
      h('div', { class: 'muted small' }, [src.channel, src.duration ? fmtT(src.duration) : '', src.url ? h('a', { href: src.url, target: '_blank' }, 'sumber') : null]
        .filter(Boolean).flatMap((x, i) => (i ? [' · ', x] : [x])))),
    h('div', { class: 'row' },
      btn('⚙ Mode lanjutan', () => { S.adv[p.id] = true; S.view = Math.min(p.current_stage, PUB - 1); fixView(); S.draft = clone(S.p[stageKey()]); render(); }, 'small ghost'),
      h('button', { class: 'ghost danger small', onclick: deleteProject }, 'Hapus'))));

  if (running) {
    const box = h('div', {}, clipProgressView(''));
    S.onJob = (j) => { box.innerHTML = ''; box.append(clipProgressView(j.progress || ((j.steps || []).slice(-1)[0] || {}).msg)); };
    main.append(box);
  } else if (!src.path) {
    const o = clipOpts();
    const url = h('input', { class: 'ch-url', placeholder: 'Tempel link YouTube…' });
    main.append(h('div', { class: 'clip-home' }, h('div', { class: 'ch-input' }, url,
      h('button', { class: 'primary ch-go', onclick: () => /^https?:\/\//.test(url.value.trim()) ? startAutoClip({ url: url.value.trim() }) : toast('Tempel link dulu', true) }, '✨ Buat Klip')),
      h('div', { class: 'row', style: 'justify-content:center' }, h('button', { class: 'ghost small', onclick: async () => {
        await uploadFile(`/api/projects/${p.id}/upload-source`, 'video/*'); if (((S.p.research || {}).source || {}).path) startAutoClip();
      } }, '📁 Unggah file video')), (() => { const d = h('div'); const r = () => { d.innerHTML = ''; d.append(clipOptionsView(o, r)); }; r(); return d; })()));
    return;
  } else if ((p.autoclip || {}).status === 'error' && !kids.length) {
    main.append(h('div', { class: 'hint warn' }, `Proses berhenti: ${p.autoclip.error || 'gagal'}`),
      h('div', { class: 'row' }, btn('↻ Coba lagi', () => startAutoClip(), 'primary'),
        h('span', { class: 'muted small' }, 'Langkah yang sudah selesai (unduh, transkrip) tidak diulang.')));
  } else if (!kids.length) {
    main.append(h('div', { class: 'card' }, h('p', {}, 'Video sudah siap. Buat klip sekarang?'),
      (() => { const o = clipOpts(); const d = h('div'); const r = () => { d.innerHTML = ''; d.append(clipOptionsView(o, r)); }; r(); return d; })(),
      btn('✨ Buat Klip', () => startAutoClip(), 'primary')));
  }

  // --- galeri klip jadi
  if (kids.length) {
    kids.sort((a, b) => ((b.clip || {}).score || 0) - ((a.clip || {}).score || 0));
    main.append(h('div', { class: 'row between', style: 'margin-top:14px' }, h('h2', {}, `🎬 ${kids.length} klip jadi`),
      h('span', { class: 'muted small' }, 'Klik video untuk memutar. Skor = potensi viral menurut AI.')));
    const top = kids.find((c) => c.thumb);
    main.append(h('details', { class: 'ch-more', open: !!S.brandOpen, ontoggle: (e) => { S.brandOpen = e.target.open; } },
      h('summary', {}, '🏷 Logo & overlay (berlaku untuk semua klip)'),
      brandPanel({ format: p.format, thumb: top ? U(`/media/${top.id}/${top.thumb}`) : null }),
      h('div', { class: 'row', style: 'margin-top:8px' },
        btn(`↻ Terapkan ke ${kids.length} klip ini (render ulang)`, async () => {
          if (!confirm(`Render ulang ${kids.length} klip dengan logo/overlay terbaru? Klip yang sudah terunggah ke YouTube dilewati.`)) return;
          S.clipRun = p.id; render();
          const r = await runAction('research.rebrand', {});
          S.clipRun = null;
          if (r) toast(`${r.done.length} klip diperbarui ✓${r.failed.length ? ` (${r.failed.length} gagal)` : ''}${r.skipped_uploaded ? ` · ${r.skipped_uploaded} sudah terunggah, dilewati` : ''}`);
          await refreshList(); render();
        }, 'primary small'),
        h('span', { class: 'muted small' }, 'Klip baru otomatis memakai pengaturan ini — tombol ini hanya untuk klip yang sudah jadi.'))));
    main.append(h('div', { class: 'clip-grid' }, kids.map((c) => clipCard(c))));
  }
  // --- momen lain yang belum dibuat
  const made = new Set(kids.map((c) => (c.clip || {}).id));
  const rest = ((p.script || {}).clips || []).map((c, i) => [c, i]).filter(([c]) => !made.has(c.id));
  if (rest.length && !running) {
    main.append(h('details', { class: 'more-moments', open: !kids.length },
      h('summary', {}, `💡 ${rest.length} momen lain ditemukan AI`),
      rest.map(([c, i]) => h('div', { class: 'mm-row' },
        h('span', { class: `badge score ${c.score >= 75 ? 'hi' : c.score >= 55 ? 'mid' : 'lo'}` }, c.score),
        h('div', { class: 'grow' }, h('b', {}, c.title || c.id), h('div', { class: 'muted small' }, `${fmtT(c.start)} · ${Math.round(c.end - c.start)} dtk · ${c.hook || ''}`)),
        btn('✨ Buat klip ini', async () => {
          const o = clipOpts(); S.clipRun = p.id; render();
          const tpl = (S.meta.sub_user_templates || {})[o.template] || (S.meta.sub_templates || {})[o.template];
          await runAction('script.produce_clips', { indexes: [i], crop_mode: o.crop_mode, music: o.music,
            sub_style: tpl ? { ...S.meta.sub_defaults, ...tpl.style, template: o.template } : null });
          S.clipRun = null; await refreshList(); render();
        }, 'small')))));
  }
}

// Layar penuh: video selalu tampil utuh (9:16 tetap 9:16), walau di kartu dipotong "cover".
['fullscreenchange', 'webkitfullscreenchange'].forEach((ev) => document.addEventListener(ev, () => {
  const fs = document.fullscreenElement || document.webkitFullscreenElement;
  document.querySelectorAll('video').forEach((v) => { v.style.objectFit = v === fs ? 'contain' : ''; });
}));

function clipCard(c) {
  const src = c.output ? U(`/media/${c.id}/${c.output}`) : '';
  const poster = c.thumb ? U(`/media/${c.id}/${c.thumb}`) : undefined;
  const sc = (c.clip || {}).score;
  const vid = h('video', { src, poster, preload: 'none', playsInline: true, onclick: (e) => {
    const v = e.currentTarget; if (v.paused) { document.querySelectorAll('.clip-grid video').forEach((x) => x !== v && x.pause()); v.controls = true; v.play(); } else v.pause();
  } });
  const up = c.publish && c.publish.video_id;
  return h('div', { class: 'clip-card' },
    h('div', { class: 'cc-media' }, vid,
      sc != null ? h('span', { class: `cc-score ${sc >= 75 ? 'hi' : sc >= 55 ? 'mid' : 'lo'}` }, `🔥 ${sc}`) : null,
      c.duration ? h('span', { class: 'cc-dur' }, fmtT(c.duration).replace(/\.\d$/, '')) : null,
      up ? h('span', { class: 'cc-up' }, '✅ diunggah') : null),
    h('div', { class: 'cc-title', title: c.title }, c.title),
    h('div', { class: 'cc-actions' },
      src ? h('a', { class: 'btnlike', href: src, download: `${(c.title || c.id).replace(/[\\/:*?"<>|]+/g, '').slice(0, 60)}.mp4`, title: 'Unduh MP4' }, '⬇ Unduh') : null,
      h('button', { title: 'Judul, deskripsi & jadwal upload', onclick: () => loadProject(c.id, false, 'publish') }, up ? '📅 Detail' : '📅 Jadwalkan'),
      h('button', { title: 'Edit subtitle, bingkai, thumbnail…', onclick: () => { loadProject(c.id, false, 'clipper').then(() => setView(S.meta.stages.indexOf('subtitles'))); } }, '✏ Edit'),
      h('button', { class: 'danger', title: 'Hapus klip', onclick: async () => {
        if (!confirm(`Hapus klip "${c.title}"?`)) return;
        await api('DELETE', `/api/projects/${c.id}`); await refreshList(); render();
      } }, '🗑 Hapus')));
}

// ---------------------------------------------------------------- tab Upload & Jadwal
/* Dipakai bersama oleh jalur Dari Nol dan Clipper: semua video yang render-nya sudah disetujui
   masuk ke sini, dikelompokkan: siap diunggah → terjadwal → sudah terunggah. */
function pubState(p) {
  const pub = p.publish || {};
  if (!pub.video_id) return 'ready';
  if (pub.publish_at && new Date(pub.publish_at) > new Date()) return 'scheduled';
  return 'live';
}
const PUB_GROUPS = [['ready', '⏳ Siap diunggah'], ['scheduled', '📅 Terjadwal'], ['live', '✅ Sudah terunggah']];
const fmtWhen = (iso) => { if (!iso) return ''; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString('id', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); };

function publishList(all, ul) {
  const ready = all.filter(readyForUpload);
  if (!ready.length) ul.append(h('li', { class: 'muted small', style: 'cursor:default' }, 'Belum ada video siap upload. Selesaikan sampai tahap Render di tab Dari Nol / Clipper.'));
  PUB_GROUPS.forEach(([key, label]) => {
    const rows = ready.filter((p) => pubState(p) === key)
      .sort((a, b) => (a.publish.publish_at || '9').localeCompare(b.publish.publish_at || '9'));
    if (!rows.length) return;
    ul.append(h('li', { class: 'group' }, `${label} (${rows.length})`));
    rows.forEach((p) => ul.append(h('li', { class: S.p && S.p.id === p.id ? 'active' : '', onclick: () => {
      if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
      loadProject(p.id, false, 'publish');
    } },
      h('div', {}, p.mode === 'clipper' ? '✂ ' : '🎬 ', p.publish.title || p.title),
      h('div', { class: 'muted small' }, `${p.format === 'short' ? 'Short' : 'Long'}`,
        p.parent ? ` · 📁 ${((all.find((q) => q.id === p.parent) || {}).title || '').slice(0, 30)}` : '',
        p.publish.publish_at ? ` · tayang ${fmtWhen(p.publish.publish_at)}` : p.publish.video_id ? ` · ${p.publish.privacy || ''}` : ''))));
  });
  const wip = all.length - ready.length;
  if (wip) ul.append(h('li', { class: 'muted small', style: 'cursor:default' }, `${wip} proyek lain masih dikerjakan.`));
}

function renderPublishView() {
  const p = S.p, main = $('#main');
  main.innerHTML = '';
  applyTheme('publish');
  if (!p || !readyForUpload(p)) return renderWelcome('publish');
  const r = p.render || {};
  main.append(h('div', { class: 'row between' },
    h('div', {},
      h('h1', {}, p.publish.title || p.title),
      h('div', { class: 'row' },
        h('span', { class: 'badge' }, p.mode === 'scratch' ? '🎬 Dari nol' : '✂ Clipper'),
        h('span', { class: 'badge' }, p.format === 'short' ? 'Short 9:16' : 'Long 16:9'),
        h('span', { class: `badge ${p.stage_status.publish}` }, statusLabel(p.stage_status.publish)))),
    h('div', { class: 'row' },
      h('button', { class: 'ghost small', onclick: () => {
        if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
        S.view = PUB - 1; loadProject(p.id, true, p.mode);
      } }, `✎ Edit video / thumbnail di tab ${p.mode === 'clipper' ? 'Clipper' : 'Dari Nol'}`),
      h('button', { class: 'ghost small', onclick: () => { S.p = null; lsSet('lastProject_publish', null); renderWelcome('publish'); refreshList(); } }, '📅 Ringkasan jadwal'))));

  const panel = h('div', { class: 'panel' });
  panel.append(h('div', { class: 'gate' },
    h('b', { class: 'grow' }, 'Upload & Jadwal'),
    h('label', { class: 'row small' }, h('input', { type: 'checkbox', checked: S.raw, onchange: (e) => { S.raw = e.target.checked; render(); } }), 'Edit Manual (JSON mentah)'),
    h('button', { id: 'btn-save', onclick: () => saveDraft().catch((e) => toast(e.message, true)), disabled: S.busy },
      S.dirty ? '💾 Simpan *' : '💾 Simpan'),
    h('button', { class: 'success', onclick: approve, disabled: S.busy }, '✓ Tandai selesai')));
  if (p.mode === 'clipper') panel.append(hint('Klip dari video orang lain: cantumkan kredit & link sumber di deskripsi.', 'warn'));
  const left = h('div', { class: 'pub-video' },
    r.output_path ? h('video', { class: 'preview-v', src: media(r.output_path), controls: true, preload: 'metadata' }) : hint('File video belum ada — render ulang di tab jalurnya.', 'warn'),
    r.output_path ? h('div', { class: 'row small' }, h('a', { href: media(r.output_path), download: `${p.id}.mp4` }, '⬇ Unduh MP4'),
      r.duration ? h('span', { class: 'muted' }, `durasi ${fmtT(r.duration)}`) : null) : null);
  panel.append(h('div', { class: `pub-grid ${p.format}` }, left, h('div', {}, S.raw ? rawEditor() : rPublish())));
  main.append(panel);
  main.append(h('details', {}, h('summary', {}, 'Log proyek'), h('pre', { class: 'log' }, (p.log || []).slice().reverse().join('\n'))));
}

async function renderScheduleOverview(box) {
  let rows = [];
  try { rows = await api('GET', '/api/schedule'); } catch (e) { box.append(hint(`Gagal memuat jadwal: ${e.message}`, 'warn')); return; }
  const ready = (S.all || []).filter((p) => readyForUpload(p) && !p.publish.video_id);
  box.append(h('div', { class: 'grid2 pub-summary' },
    h('div', { class: 'card' }, h('div', { class: 'big' }, String(ready.length)), 'video siap diunggah'),
    h('div', { class: 'card' }, h('div', { class: 'big' }, String(rows.filter((r) => r.publish_at && new Date(r.publish_at) > new Date()).length)), 'terjadwal tayang')));
  box.append(h('h3', {}, 'Jadwal tayang'), rows.length
    ? h('table', {}, h('tr', {}, h('th', {}, 'Tayang'), h('th', {}, 'Judul'), h('th', {}, 'Status')),
      rows.map((r) => h('tr', { class: 'clickable', onclick: () => loadProject(r.id, false, 'publish') },
        h('td', {}, r.publish_at ? fmtWhen(r.publish_at) : '-'),
        h('td', {}, r.url ? h('a', { href: r.url, target: '_blank', onclick: (e) => e.stopPropagation() }, r.title) : r.title),
        h('td', {}, r.status || 'belum diunggah'))))
    : h('p', { class: 'muted' }, 'Belum ada video terjadwal.'));
}

/* Cek koneksi ke backend dan jelaskan penyebab gagalnya dalam bahasa manusia. */
async function probeBackend(base) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(base + '/api/meta', { signal: ctl.signal });
    if (!r.ok) return { ok: false, why: `Backend menjawab tetapi error (HTTP ${r.status}).` };
    return { ok: true, meta: await r.json() };
  } catch (e) {
    if (e.name === 'AbortError') return { ok: false, why: 'Waktu habis (6 dtk): backend tidak menjawab.' };
    // Fetch biasa gagal. Coba mode no-cors: kalau ini lolos, server hidup tapi browser memblokir.
    try {
      await fetch(base + '/api/meta', { mode: 'no-cors', signal: ctl.signal });
      return { ok: false, why: 'Backend hidup, tetapi browser memblokir aksesnya (CORS / izin jaringan lokal). ' +
        'Pastikan CORS_ORIGINS di .env berisi ' + location.origin + ', restart "python run.py", ' +
        'dan jika Chrome menanyakan izin akses jaringan lokal, pilih Izinkan (ikon gembok di kiri alamat).' };
    } catch (_) {
      return { ok: false, why: `Tidak ada backend di ${base}. Jalankan "python run.py" di laptop lalu klik Hubungkan lagi.` };
    } finally { clearTimeout(t); }
  } finally { clearTimeout(t); }
}

async function showHealth(ping = false) {
  const body = $('#modal-body'); body.innerHTML = '';
  body.append(h('h2', {}, '🩺 Cek Kesiapan'), h('p', { class: 'muted' }, ping ? 'Menguji AI…' : 'Memeriksa…'));
  if (!$('#modal').open) $('#modal').showModal();
  let rows;
  try { rows = await api('GET', `/api/health${ping ? '?ping=1' : ''}`); }
  catch (e) { body.lastChild.textContent = `Gagal: ${e.message}`; return; }
  body.innerHTML = '';
  const icon = { ok: '✅', warn: '⚠️', fail: '❌' };
  const bad = rows.filter((r) => r.status === 'fail').length, warn = rows.filter((r) => r.status === 'warn').length;
  body.append(h('h2', {}, '🩺 Cek Kesiapan'),
    h('p', { class: bad ? 'warn-text' : 'muted' }, bad ? `${bad} hal wajib dibereskan, ${warn} peringatan.` : warn ? `Siap dipakai · ${warn} peringatan (opsional).` : 'Semua siap ✓'));
  let group = '';
  const table = h('table', { class: 'health' });
  rows.forEach((r) => {
    if (r.group !== group) { group = r.group; table.append(h('tr', {}, h('th', { colspan: 3 }, group))); }
    table.append(h('tr', {}, h('td', {}, icon[r.status] || ''), h('td', {}, h('b', {}, r.name)),
      h('td', {}, r.detail, r.fix ? h('div', { class: 'small muted' }, '→ ', r.fix) : null)));
  });
  body.append(table, h('div', { class: 'row', style: 'margin-top:10px' },
    btn('🤖 Tes jawaban AI', () => showHealth(true), 'small'), btn('↻ Cek ulang', () => showHealth(false), 'small ghost')));
}

function backendBox(errMsg) {
  const box = $('#backend');
  box.hidden = SERVED_BY_BACKEND && !errMsg;
  box.innerHTML = '';
  const inp = h('input', { value: API_BASE || location.origin });
  const msg = errMsg
    ? h('div', { class: 'hint warn' }, `Tidak terhubung ke ${API_BASE || location.origin}. ${errMsg}`)
    : h('div', { class: 'muted small' }, `Terhubung ✓ ${API_BASE || location.origin}`);
  const btn = h('button', { class: 'small', onclick: async () => {
    const base = inp.value.trim().replace(/\/+$/, '');
    btn.disabled = true; btn.textContent = 'Menghubungkan…';
    msg.className = 'muted small'; msg.textContent = `Mencoba ${base}…`;
    const r = await probeBackend(base);
    if (r.ok) {
      API_BASE = base; lsSet('apiBase', base);
      msg.className = 'muted small'; msg.textContent = `Terhubung ✓ ${base} — memuat ulang…`;
      toast('Terhubung ke backend ✓');
      setTimeout(() => location.reload(), 600);
    } else {
      msg.className = 'hint warn'; msg.textContent = `Gagal terhubung ke ${base}. ${r.why}`;
      toast('Gagal terhubung ke backend', true);
      btn.disabled = false; btn.textContent = 'Hubungkan';
    }
  } }, 'Hubungkan');
  box.append(h('h3', {}, 'Backend'), msg, h('div', { class: 'row' }, h('div', { class: 'grow' }, inp), btn));
}

// ---------------------------------------------------------------- ruang kerja (tab) per jalur
/* Jalur "Dari Nol" dan "Clipper" kebutuhannya beda, jadi masing-masing punya tab sendiri:
   daftar proyek, form proyek baru, nama tahap, warna aksen, dan layar sambutan. */
function applyTheme(mode) {
  document.body.classList.toggle('mode-clipper', mode === 'clipper');
  document.body.classList.toggle('mode-publish', mode === 'publish');
}

function renderSidebarTab() {
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.mode === S.tab));
  applyTheme(S.tab);
  const box = $('#newproj'); box.innerHTML = '';
  if (S.tab === 'publish') {
    box.append(h('h3', {}, '📅 Upload & Jadwal'),
      h('div', { class: 'muted small' }, 'Semua video dari jalur Dari Nol dan Clipper yang tahap Render-nya sudah disetujui masuk ke sini. Atur judul, thumbnail, lalu unggah atau jadwalkan.'),
      ...(S.meta.google ? [] : [h('div', { class: 'hint warn small', style: 'margin-top:8px' }, 'Google belum terhubung: python run.py --setup-google')]));
    return;
  }
  const fmtSel = (def) => {
    const el = h('select', {}, h('option', { value: 'short' }, 'Short (9:16, ≤60 dtk)'), h('option', { value: 'long' }, 'Long (16:9)'));
    el.value = def; return el;
  };
  if (S.tab === 'scratch') {
    const fmt = fmtSel('short');
    const kw = h('input', { placeholder: 'mis. misteri sejarah nusantara' });
    const go = async () => {
      try {
        const t = kw.value.trim();
        const p = await api('POST', '/api/projects', { mode: 'scratch', format: fmt.value, title: t, keyword: t });
        kw.value = ''; await loadProject(p.id);
      } catch (e) { toast(e.message, true); }
    };
    kw.onkeydown = (e) => { if (e.key === 'Enter') go(); };
    box.append(h('h3', {}, '🎬 Konten baru dari nol'),
      h('label', { class: 'field' }, h('span', {}, 'Niche / kata kunci'), kw),
      h('label', { class: 'field' }, h('span', {}, 'Format'), fmt),
      h('button', { class: 'primary', onclick: go }, '+ Buat proyek'));
  } else {
    box.append(h('h3', {}, '✂ Clipper'),
      h('div', { class: 'muted small' }, 'Video panjang → banyak klip pendek siap upload, sekali klik.'),
      h('button', { class: 'primary', style: 'margin-top:8px;width:100%', onclick: () => {
        if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
        S.p = null; S.dirty = false; lsSet('lastProject_clipper', null); renderWelcome('clipper'); refreshList();
      } }, '+ Video baru'));
  }
}

function setTab(mode) {
  if (mode === S.tab) return;
  if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
  S.tab = mode; lsSet('tab', mode); S.dirty = false;
  renderSidebarTab();
  const last = lsGet(`lastProject_${mode}`);
  const blank = () => { S.p = null; renderWelcome(mode); refreshList(); };
  if (last) loadProject(last, false, mode).catch(() => { lsSet(`lastProject_${mode}`, null); blank(); });
  else blank();
}

function renderWelcome(mode) {
  applyTheme(mode);
  const main = $('#main'); main.innerHTML = '';
  if (mode === 'publish') {
    const box = h('div', { class: 'pub-home' }, h('h1', {}, '📅 Upload & Jadwal'),
      h('p', { class: 'muted' }, 'Antrean upload untuk kedua jalur. Pilih video di kiri untuk mengisi judul, deskripsi, tag, thumbnail, lalu unggah atau jadwalkan. YouTube yang menayangkan sesuai jadwal — laptop tidak perlu menyala.'));
    main.append(box);
    renderScheduleOverview(box);
    return;
  }
  if (mode === 'clipper') return clipHome();
  const L = labelsFor(mode);
  const clip = mode === 'clipper';
  main.append(h('div', { class: 'empty' },
    h('h1', {}, clip ? '✂ Clipper' : '🎬 Konten dari Nol'),
    h('p', {}, clip
      ? 'Ubah video panjang (podcast, wawancara, live) jadi klip pendek. Tempel link YouTube atau unggah file di kiri.'
      : 'Bangun video tanpa bahan apa pun: ide → naskah → visual → suara → subtitle → render. Isi niche di kiri.'),
    h('ol', { class: 'flow' }, wizardStages(mode).map(([k]) => h('li', {}, h('b', {}, L[k].replace(/^\d+\.\s*/, '')), ' — ', h('span', { class: 'muted' }, helpFor(mode, k))))),
    h('p', { class: 'muted small' }, 'Di setiap tahap Anda bisa menjalankan AI, mengedit manual, lalu "Setujui & Lanjut". ',
      'Setelah Render disetujui, video pindah ke tab 📅 Upload & Jadwal.')));
}

async function init() {
  try { S.meta = await api('GET', '/api/meta'); PUB = S.meta.stages.indexOf('publish'); loadFonts(); }
  catch (e) {
    const r = SERVED_BY_BACKEND ? { why: e.message } : await probeBackend(API_BASE);
    backendBox(r.ok ? e.message : r.why); return;
  }
  backendBox(null);
  $('#sysinfo').textContent = `LLM: ${S.meta.llm} · Google: ${S.meta.google ? 'terhubung ✓' : 'belum terhubung'}`;
  const [hashTab, hashPid, hashStage] = location.hash.slice(1).split('/');  // link langsung: …/#clipper, …/#publish/<id>, …/#clipper/<id>/subtitles
  if (TABS.includes(hashTab)) { S.tab = hashTab; lsSet('tab', hashTab); if (hashPid) lsSet(`lastProject_${hashTab}`, hashPid); }
  document.querySelectorAll('#tabs button').forEach((b) => { b.onclick = () => setTab(b.dataset.mode); });
  renderSidebarTab();
  $('#btn-health').onclick = () => showHealth().catch((e) => toast(e.message, true));
  window.addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
  await refreshList();
  const last = lsGet(`lastProject_${S.tab}`) || (hashTab ? null : lsGet('lastProject'));
  if (last) loadProject(last, false, S.tab === 'publish' ? 'publish' : null)
    .then(() => { const i = S.meta.stages.indexOf(hashStage || ''); if (i >= 0 && i < PUB) setView(i); })
    .catch(() => { lsSet(`lastProject_${S.tab}`, null); renderWelcome(S.tab); });
  else renderWelcome(S.tab);
}

init().catch((e) => toast(`Gagal memuat: ${e.message}`, true));
