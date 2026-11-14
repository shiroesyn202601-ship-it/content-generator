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
  'script.generate': '✍ Menulis naskah', 'script.factcheck': '🔍 Cek fakta', 'research.batch': '🏭 Produksi massal',
  'publish.thumbs': '🖼 Membuat thumbnail', 'publish.thumbnail': '🖼 Memasang thumbnail',
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
const PUB = 6;          // indeks tahap "publish": dikerjakan di tab Upload & Jadwal, bukan di wizard
const TABS = ['scratch', 'clipper', 'publish'];
const readyForUpload = (p) => p.current_stage >= PUB;
const stageKey = () => S.meta.stages[S.view];
const labelsFor = (mode) => ((S.meta.labels_by_mode || {})[mode]) || S.meta.labels;
const helpFor = (mode, st) => (((S.meta.stage_help || {})[mode]) || {})[st] || '';
const media = (rel) => rel ? U(`/media/${S.p.id}/${rel}?v=${encodeURIComponent(S.p.updated_at)}`) : '';

// ---------------------------------------------------------------- data flow
async function loadProject(pid, keepView = false, tab = null) {
  S.p = await api('GET', `/api/projects/${pid}`);
  // Tab tujuan: yang diminta, atau tetap di Upload bila proyek sudah siap upload, selain itu tab jalurnya.
  const want = tab || (S.tab === 'publish' && readyForUpload(S.p) ? 'publish' : S.p.mode);
  if (S.tab !== want) { S.tab = want; lsSet('tab', want); renderSidebarTab(); }
  if (S.tab === 'publish') S.view = PUB;
  else if (!keepView || S.view >= PUB) S.view = Math.min(S.p.current_stage, PUB - 1);
  S.draft = clone(S.p[stageKey()]);
  S.voiceDraft = clone(S.p.voice);
  S.dirty = false; S.voiceDirty = false;
  lsSet('lastProject', pid); lsSet(`lastProject_${S.tab}`, pid);
  render(); refreshList();
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
    if (S.tab !== 'publish' && S.view + 1 >= PUB) {
      toast('Video siap ✓ — lanjut di tab 📅 Upload & Jadwal');
      await loadProject(S.p.id, false, 'publish');
    } else {
      S.view = Math.min(S.view + 1, S.tab === 'publish' ? PUB : PUB - 1);
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
  const p = S.p, main = $('#main');
  main.innerHTML = '';
  const st = stageKey(), locked = S.view > p.current_stage, L = labelsFor(p.mode);
  applyTheme(p.mode);

  main.append(h('div', { class: 'row between' },
    h('div', {},
      h('h1', {}, p.title),
      h('div', { class: 'row' },
        h('span', { class: 'badge' }, p.mode === 'scratch' ? 'Jalur 1 · Dari nol' : 'Jalur 2 · Clipper'),
        h('span', { class: 'badge' }, p.format === 'short' ? 'Short 9:16' : 'Long 16:9'),
        h('span', { class: 'badge' }, p.id))),
    h('button', { class: 'ghost danger small', onclick: deleteProject }, 'Hapus proyek')));

  main.append(h('div', { class: 'stepper' }, S.meta.stages.slice(0, PUB).map((k, i) => h('div', {
    class: `step s-${p.stage_status[k]} ${i === S.view ? 'current' : ''} ${i > p.current_stage ? 'locked' : ''}`,
    onclick: () => setView(i),
  }, L[k], h('span', { class: 'st' }, statusLabel(p.stage_status[k]))))));

  const panel = h('div', { class: 'panel' });
  panel.append(h('div', { class: 'gate' },
    h('b', { class: 'grow' }, L[st], ' ', h('span', { class: `badge ${p.stage_status[st]}` }, statusLabel(p.stage_status[st]))),
    h('label', { class: 'row small' }, h('input', { type: 'checkbox', checked: S.raw, onchange: (e) => { S.raw = e.target.checked; render(); } }), 'Edit Manual (JSON mentah)'),
    h('button', { id: 'btn-save', onclick: () => saveDraft().catch((e) => toast(e.message, true)), disabled: locked || S.busy },
      S.dirty ? '💾 Simpan Edit Manual *' : '💾 Simpan Edit Manual'),
    h('button', { class: 'success', onclick: approve, disabled: locked || S.busy }, '✓ Setujui & Lanjut')));

  if (helpFor(p.mode, st)) panel.append(h('div', { class: 'muted small stage-help' }, helpFor(p.mode, st)));
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

async function deleteProject() {
  if (!confirm(`Hapus proyek "${S.p.title}" beserta semua file lokalnya? (Backup di Google Drive tidak ikut terhapus)`)) return;
  await api('DELETE', `/api/projects/${S.p.id}`);
  lsSet(`lastProject_${S.p.mode}`, null); lsSet('lastProject_publish', null); S.p = null; lsSet('lastProject', null);
  renderWelcome(S.tab);
  refreshList();
}

// ================================================================ TAHAP 1
function rResearch() {
  const d = S.draft, box = h('div');
  if (S.p.mode === 'scratch') {
    box.append(h('div', { class: 'row' },
      h('div', { class: 'grow' }, field('Niche / kata kunci', d, 'keyword', { placeholder: 'mis. misteri sejarah nusantara' })),
      btn('🔎 Riset & Generate Ide (AI)', () => runAction('research.ideas', { keyword: d.keyword }), 'primary')));
    box.append(hint('AI membaca video populer di YouTube untuk topik ini (judul, views, umur → views/hari untuk melihat topik yang sedang naik) dan saran pencarian YouTube, lalu mencari celah/angle baru. Pilih satu ide; semua field bisa diedit. Centang beberapa ide lalu "Produksi massal" untuk membuat banyak proyek sekaligus.'));
    if (d.insight) box.append(h('div', { class: 'card' }, h('b', {}, 'Insight riset: '), d.insight));
    (d.ideas || []).forEach((it, i) => {
      box.append(h('div', { class: `card ${d.selected === i ? 'selected' : ''}` },
        h('label', { class: 'row' }, h('input', { type: 'radio', name: 'idea', checked: d.selected === i,
          onchange: () => { d.selected = i; markDirty(); render(); } }), h('b', {}, `Ide ${i + 1}`),
          h('span', { class: 'badge' }, `potensi ${it.potential ?? '?'}/10`),
          h('label', { class: 'small muted', style: 'margin-left:auto', onclick: (e) => e.stopPropagation() },
            h('input', { type: 'checkbox', checked: S.batch.has(i), onchange: (e) => { e.target.checked ? S.batch.add(i) : S.batch.delete(i); render(); } }),
            ' produksi massal')),
        field('Judul', it, 'title'),
        h('div', { class: 'grid2' }, field('Angle', it, 'angle', { type: 'textarea', rows: 2 }), field('Hook 3 detik', it, 'hook', { type: 'textarea', rows: 2 })),
        h('div', { class: 'grid2' }, field('Kata kunci SEO', it, 'keywords', { type: 'csv' }), field('Target penonton', it, 'target_audience'))));
    });
    box.append(h('div', { class: 'row' },
      btn('+ Tambah ide manual', () => { d.ideas = d.ideas || []; d.ideas.push({ title: '', angle: '', hook: '', keywords: [] }); markDirty(); render(); }, 'ghost'),
      S.batch.size ? btn(`🏭 Produksi massal (${S.batch.size} ide)`, async () => {
        if (!confirm(`Buat ${S.batch.size} proyek baru dari ide yang dicentang? AI akan riset fakta & menulis naskah tiap proyek satu per satu (±1 menit per proyek). Setelah itu cek & setujui naskah masing-masing.`)) return;
        const res = await runAction('research.batch', { indexes: [...S.batch].sort((a, b) => a - b) });
        S.batch.clear();
        if (res) toast(`${res.created.length} proyek dibuat. Lihat daftar Proyek di kiri.`);
        refreshList();
      }, 'primary') : null));
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
  const v = S.p.voice || {}, wps = ({ 'id-ID-ArdiNeural': 2.5, 'id-ID-GadisNeural': 2.0 }[v.voice] || 2.2) * (v.speed || 1);
  const secs = Math.round(words / wps + (words / 9) * ((v.pause_ms || 350) / 1000));
  const over = S.p.format === 'short' && secs > 58;
  box.append(h('div', { class: 'row' },
    btn('✍ Generate Kerangka & Naskah (AI)', () => runAction('script.generate'), 'primary'),
    d.scenes.length ? btn('🔍 Cek Fakta', () => runAction('script.factcheck'), 'small') : null,
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

function rScriptClipper() {
  const d = S.draft, box = h('div');
  const src = S.p.research.source || {};
  const vid = h('video', { class: 'preview-v', src: media(src.path), controls: true, preload: 'metadata' });
  box.append(h('div', { class: 'row' },
    btn('1 · Transkrip (Whisper)', () => runAction('script.transcribe'), d.transcript ? '' : 'primary'),
    btn('2 · Cari Momen Viral (AI)', () => runAction('script.find_clips', { direction: d.direction || '' }), d.transcript ? 'primary' : ''),
    btn('+ Klip manual', () => { d.clips = d.clips || []; const t = vid.currentTime || 0; d.clips.push({ id: `m${Date.now().toString(36)}`, start: t, end: t + 30, title: 'Klip manual', hook: '', reason: '', score: 0, keywords: [] }); markDirty(); render(); }, 'ghost')));
  box.append(field('Arahan AI (opsional)', d, 'direction', { placeholder: 'mis. cari momen lucu / fokus ke tips praktis / bagian tentang modal usaha' }));
  box.append(hint('AI menilai tiap momen 0-100 dari hook, alur, nilai, dan potensi dibagikan. Klip selalu mulai & berakhir di batas kalimat (ditambah jeda napas), jadi tidak ada kata yang terpotong. Setelah mengubah waktu manual, klik "Snap".'));
  box.append(vid);
  const sel = d.selected_clip;
  (d.clips || []).forEach((c, i) => {
    const durEl = h('span', { class: 'badge' }, `${(c.end - c.start).toFixed(1)} dtk`);
    box.append(h('div', { class: `card ${sel === i ? 'selected' : ''}` },
      h('div', { class: 'row between' },
        h('label', { class: 'row' }, h('input', { type: 'radio', name: 'clip', checked: sel === i, onchange: () => { d.selected_clip = i; markDirty(); render(); } }),
          h('b', {}, c.title || c.id), durEl, scoreBadge(c)),
        h('div', { class: 'row' },
          btn('▶ Putar', () => playSegment(vid, c.start, c.end), 'small'),
          btn('🧲 Snap ke kalimat', async () => {
            const r = await api('GET', `/api/projects/${S.p.id}/snap?start=${c.start}&end=${c.end}`);
            c.start = r.start; c.end = r.end; markDirty(); render();
          }, 'small'),
          btn('⑂ Jadikan proyek baru', async () => {
            if (S.dirty) await saveDraft(true);
            const np = await api('POST', `/api/projects/${S.p.id}/fork`, { clip_index: i });
            toast(`Proyek baru dibuat: ${np.title}`); refreshList();
          }, 'small ghost'),
          btn('✕', () => { d.clips.splice(i, 1); if (d.selected_clip === i) d.selected_clip = null; markDirty(); render(); }, 'small ghost danger'))),
      h('div', { class: 'grid' },
        field('Mulai (dtk)', c, 'start', { type: 'number', step: 0.1, onchange: () => (durEl.textContent = `${(c.end - c.start).toFixed(1)} dtk`) }),
        field('Selesai (dtk)', c, 'end', { type: 'number', step: 0.1, onchange: () => (durEl.textContent = `${(c.end - c.start).toFixed(1)} dtk`) }),
        field('Judul', c, 'title'), field('Hook', c, 'hook')),
      field('Kata kunci ditekankan', c, 'keywords', { type: 'csv' }),
      c.scores ? h('div', { class: 'row small' }, Object.entries(SUB_LABEL).map(([k, l]) => h('span', { class: 'subscore' }, `${l} `, h('b', {}, c.scores[k] ?? '-')))) : null,
      c.reason ? h('div', { class: 'muted small' }, 'Alasan AI: ', c.reason) : null));
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
  const st = d.style = d.style || {};
  box.append(h('div', { class: 'row' },
    btn('🔤 Bangun subtitle dari timestamp kata', () => runAction('subtitles.build'), d.words && d.words.length ? '' : 'primary'),
    btn('↻ Kelompokkan ulang baris', () => runAction('subtitles.regroup', { style: st }, true), 'ghost'),
    h('span', { class: 'muted' }, `${(d.lines || []).length} baris · ${(d.words || []).length} kata`)));
  box.append(hint('Edit teks langsung per baris. Bungkus kata dengan *bintang* untuk penekanan (warna kata kunci + membesar saat diucapkan). Klik "Simpan Edit Manual" untuk menerapkan — timing kata dipertahankan bila jumlah kata sama.'));

  const presets = S.meta.sub_presets || {};
  const presetSel = h('select', { onchange: () => {
    const pr = presets[presetSel.value]; if (!pr) return;
    Object.assign(st, pr, { preset: presetSel.value }); markDirty(); render();
    toast('Preset diterapkan. Klik "↻ Kelompokkan ulang baris" bila jumlah kata per baris berubah.');
  } }, h('option', { value: '' }, '— pilih preset —'),
    Object.keys(presets).map((k) => h('option', { value: k, selected: st.preset === k },
      { pop: 'Pop (kata meletup, 3 kata)', tebal2: 'Tebal 2 kata (gaya viral)', elegan: 'Elegan (4 kata, tanpa animasi)', klasik: 'Klasik (1 baris panjang)' }[k] || k)));
  box.append(h('div', { class: 'row' }, h('b', {}, 'Preset gaya:'), presetSel));
  box.append(h('details', {}, h('summary', {}, 'Gaya subtitle (detail)'), h('div', { class: 'grid' },
    field('Font', st, 'font'), field('Ukuran', st, 'size', { type: 'number' }),
    field('Animasi kata aktif', st, 'animation', { type: 'select', options: [['pop', 'Pop (meletup)'], ['none', 'Tanpa animasi']] }),
    field('Skala kata aktif (%)', st, 'active_scale', { type: 'number' }),
    field('Teks hook di 2.5 dtk awal', st, 'hook_text', { type: 'checkbox' }),
    field('Warna teks', st, 'primary', { type: 'color' }), field('Warna kata aktif', st, 'highlight', { type: 'color' }),
    field('Warna kata kunci', st, 'emphasis', { type: 'color' }), field('Skala kata kunci (%)', st, 'emphasis_scale', { type: 'number' }),
    field('Outline', st, 'outline', { type: 'number' }),
    field('Posisi', st, 'position', { type: 'select', options: [['middle', 'Tengah'], ['lower', 'Sepertiga bawah'], ['bottom', 'Bawah']] }),
    field('Kata per baris', st, 'words_per_line', { type: 'number', min: 1, max: 12 }),
    field('Karaoke (sorot kata aktif)', st, 'karaoke', { type: 'checkbox' }),
    field('HURUF BESAR', st, 'uppercase', { type: 'checkbox' }))));

  // --- pratinjau live
  const [W, H] = S.p.format === 'short' ? [1080, 1920] : [1920, 1080];
  const pw = S.p.format === 'short' ? 270 : 560, ph = (pw * H) / W;
  const isClip = S.p.mode === 'clipper';
  const player = isClip ? h('video', { src: media(S.p.assets.clip_path), controls: true, playsInline: true })
    : h('audio', { src: media(S.p.audio.vo_path), controls: true, style: 'width:100%' });
  const overlay = h('div', { class: 'sub-overlay' });
  const stage = h('div', { class: 'sub-stage', style: `width:${pw}px;height:${ph}px` }, isClip ? player : h('div', { class: 'bg' }), overlay);
  const lineEls = [];
  const draw = () => {
    const t = player.currentTime;
    const scale = ph / H;
    overlay.style.fontSize = `${(st.size || 60) * scale}px`;
    overlay.style.fontFamily = `"${st.font}", Arial, sans-serif`;
    overlay.style.top = st.position === 'middle' ? '45%' : '';
    // Shorts: posisi bawah dinaikkan di atas UI YouTube (sama seperti subs.ass: 700 / 900 px dari bawah).
    const short = S.p.format === 'short';
    overlay.style.bottom = st.position === 'middle' ? '' : st.position === 'lower' ? (short ? '47%' : '22%') : (short ? '36%' : '8%');
    const idx = (d.lines || []).findIndex((l) => t >= l.start && t <= l.end + 0.05);
    lineEls.forEach((el, i) => el.classList.toggle('active', i === idx));
    overlay.innerHTML = '';
    if (idx < 0) return;
    const line = d.lines[idx], toks = tokenizeJS(line.text);
    const ws = (d.words || []).slice(line.i0, line.i1 + 1);
    const timed = ws.length === toks.length ? ws : toks.map((_, j) => ({
      start: line.start + ((line.end - line.start) * j) / toks.length, end: line.start + ((line.end - line.start) * (j + 1)) / toks.length }));
    toks.forEach((tk, j) => {
      const active = st.karaoke && t >= timed[j].start && (j + 1 >= timed.length || t < timed[j + 1].start);
      let color = tk.emphasis ? st.emphasis : st.primary, sc = 1;
      if (active) { color = tk.emphasis ? st.emphasis : st.highlight; sc = tk.emphasis ? (st.emphasis_scale || 125) / 100 : 1; }
      overlay.append(h('span', { style: `color:${color};transform:scale(${sc});-webkit-text-stroke:${(st.outline || 3) * scale}px #000` },
        st.uppercase ? tk.text.toUpperCase() : tk.text));
    });
  };
  let raf;
  const loop = () => { draw(); if (!player.paused) raf = requestAnimationFrame(loop); };
  player.addEventListener('play', () => { cancelAnimationFrame(raf); loop(); });
  player.addEventListener('seeked', draw);

  const lines = h('div', { class: 'scroll', style: 'max-height:520px' }, (d.lines || []).map((l, i) => {
    const row = h('div', { class: 'subline' },
      btn('▶', () => { player.currentTime = l.start; player.play(); }, 'small ghost'),
      field('', l, 'start', { type: 'number', step: 0.05 }), field('', l, 'end', { type: 'number', step: 0.05 }),
      h('div', { class: 'row' }, h('div', { class: 'grow' }, field('', l, 'text', { onchange: draw })),
        btn('✕', () => { d.lines.splice(i, 1); markDirty(); render(); }, 'small ghost danger')));
    lineEls.push(row);
    return row;
  }));
  box.append(h('div', { class: 'row', style: 'align-items:flex-start;gap:16px' },
    h('div', {}, stage, isClip ? null : player, h('div', { class: 'muted small' }, 'Pratinjau perkiraan; hasil akhir dirender oleh FFmpeg/libass.')),
    h('div', { class: 'grow' }, lines)));
  return box;
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

// ================================================================ TAHAP 7
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

  // --- thumbnail
  const tb = h('div', { class: 'card' }, h('b', {}, '🖼 Thumbnail'),
    h('div', { class: 'row' },
      btn((d.thumbs || []).length ? '↻ Buat ulang thumbnail' : '🖼 Buat 3 thumbnail dari video', () => runAction('publish.thumbs', { text: d.thumb_text || undefined }), 'small'),
      h('div', { class: 'grow' }, field('Teks di thumbnail', d, 'thumb_text', { placeholder: 'kosong = teks hook / judul' }))));
  if ((d.thumbs || []).length) {
    d.thumb_selected = d.thumb_selected || 0;
    tb.append(h('div', { class: 'row', style: 'align-items:flex-start;flex-wrap:wrap' }, d.thumbs.map((t, k) =>
      h('img', { src: media(t), class: `thumbpick ${d.thumb_selected === k ? 'on' : ''}`, style: `width:${S.p.format === 'short' ? 120 : 220}px`,
        onclick: () => { d.thumb_selected = k; markDirty(); render(); } }))));
    tb.append(h('div', { class: 'row' },
      d.video_id ? btn('⬆ Pasang thumbnail terpilih ke YouTube', () => runAction('publish.thumbnail', { index: d.thumb_selected }), 'small primary')
        : h('span', { class: 'muted small' }, 'Thumbnail bisa dipasang setelah video terunggah.'),
      d.thumb_uploaded ? h('span', { class: 'muted small' }, `terpasang ${d.thumb_uploaded}`) : null));
  }
  box.append(tb);
  return box;
}

const RENDER = { research: rResearch, script: rScript, assets: rAssets, audio: rAudio, subtitles: rSubtitles, render: rRender, publish: rPublish };

// ---------------------------------------------------------------- sidebar
async function refreshList() {
  const all = await api('GET', '/api/projects');
  S.all = all;
  S.counts = { scratch: 0, clipper: 0, publish: 0 };
  all.forEach((p) => {
    S.counts[p.mode] = (S.counts[p.mode] || 0) + 1;
    if (readyForUpload(p) && !p.publish.video_id) S.counts.publish += 1;  // yang belum diunggah
  });
  document.querySelectorAll('#tabs button').forEach((b) => { $('.cnt', b).textContent = S.counts[b.dataset.mode] || ''; });
  const ul = $('#project-list'); ul.innerHTML = '';
  if (S.tab === 'publish') return publishList(all, ul);
  const list = all.filter((p) => p.mode === S.tab);
  if (!list.length) ul.append(h('li', { class: 'muted small', style: 'cursor:default' },
    S.tab === 'clipper' ? 'Belum ada proyek clipper.' : 'Belum ada proyek dari nol.'));
  list.forEach((p) => {
    const done = Object.values(p.stage_status).filter((s) => s === 'approved').length;
    ul.append(h('li', { class: S.p && S.p.id === p.id ? 'active' : '', onclick: () => {
      if (S.dirty && !confirm('Ada perubahan belum disimpan. Tinggalkan?')) return;
      loadProject(p.id);
    } },
      h('div', {}, p.title),
      h('div', { class: 'muted small' }, `${p.format === 'short' ? 'Short' : 'Long'} · ${readyForUpload(p) ? (p.publish.video_id ? '✅ sudah diunggah' : '📅 siap upload') : labelsFor(p.mode)[S.meta.stages[p.current_stage]]}`),
      h('div', { class: 'pbar' }, h('i', { style: `width:${Math.min(done, PUB) / PUB * 100}%` }))));
  });
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
      } }, `✎ Edit video di tab ${p.mode === 'clipper' ? 'Clipper' : 'Dari Nol'}`),
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
    const fmt = fmtSel('short');
    const url = h('input', { placeholder: 'https://www.youtube.com/watch?v=…' });
    const create = () => api('POST', '/api/projects', { mode: 'clipper', format: fmt.value, title: '', keyword: '' });
    const fromLink = async () => {
      const u = url.value.trim();
      if (!/^https?:\/\//.test(u)) return toast('Tempel link video (https://…) dulu.', true);
      try {
        const p = await create(); url.value = '';
        await loadProject(p.id);
        await runAction('research.download', { url: u });
      } catch (e) { toast(e.message, true); }
    };
    const fromFile = async () => {
      try {
        const p = await create();
        await loadProject(p.id);
        await uploadFile(`/api/projects/${p.id}/upload-source`, 'video/*');
      } catch (e) { toast(e.message, true); }
    };
    url.onkeydown = (e) => { if (e.key === 'Enter') fromLink(); };
    box.append(h('h3', {}, '✂ Klip baru'),
      h('label', { class: 'field' }, h('span', {}, 'Link YouTube'), url),
      h('label', { class: 'field' }, h('span', {}, 'Format hasil'), fmt),
      h('div', { class: 'row' }, h('button', { class: 'primary', onclick: fromLink }, '⬇ Buat & unduh'),
        h('button', { class: 'ghost', onclick: fromFile }, '📁 Unggah file')),
      h('div', { class: 'muted small', style: 'margin-top:6px' }, 'Hanya untuk video milik sendiri, berlisensi CC, atau yang sudah diizinkan pemiliknya.'));
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
  const L = labelsFor(mode);
  const clip = mode === 'clipper';
  main.append(h('div', { class: 'empty' },
    h('h1', {}, clip ? '✂ Clipper' : '🎬 Konten dari Nol'),
    h('p', {}, clip
      ? 'Ubah video panjang (podcast, wawancara, live) jadi klip pendek. Tempel link YouTube atau unggah file di kiri.'
      : 'Bangun video tanpa bahan apa pun: ide → naskah → visual → suara → subtitle → render. Isi niche di kiri.'),
    h('ol', { class: 'flow' }, S.meta.stages.slice(0, PUB).map((k) => h('li', {}, h('b', {}, L[k].replace(/^\d+\.\s*/, '')), ' — ', h('span', { class: 'muted' }, helpFor(mode, k))))),
    h('p', { class: 'muted small' }, 'Di setiap tahap Anda bisa menjalankan AI, mengedit manual, lalu "Setujui & Lanjut". ',
      'Setelah Render disetujui, video pindah ke tab 📅 Upload & Jadwal.')));
}

async function init() {
  try { S.meta = await api('GET', '/api/meta'); }
  catch (e) {
    const r = SERVED_BY_BACKEND ? { why: e.message } : await probeBackend(API_BASE);
    backendBox(r.ok ? e.message : r.why); return;
  }
  backendBox(null);
  $('#sysinfo').textContent = `LLM: ${S.meta.llm} · Google: ${S.meta.google ? 'terhubung ✓' : 'belum terhubung'}`;
  const [hashTab, hashPid] = location.hash.slice(1).split('/');  // link langsung: …/#clipper, …/#publish/<id proyek>
  if (TABS.includes(hashTab)) { S.tab = hashTab; lsSet('tab', hashTab); if (hashPid) lsSet(`lastProject_${hashTab}`, hashPid); }
  document.querySelectorAll('#tabs button').forEach((b) => { b.onclick = () => setTab(b.dataset.mode); });
  renderSidebarTab();
  $('#btn-health').onclick = () => showHealth().catch((e) => toast(e.message, true));
  window.addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
  await refreshList();
  const last = lsGet(`lastProject_${S.tab}`) || (hashTab ? null : lsGet('lastProject'));
  if (last) loadProject(last, false, S.tab === 'publish' ? 'publish' : null).catch(() => { lsSet(`lastProject_${S.tab}`, null); renderWelcome(S.tab); });
  else renderWelcome(S.tab);
}

init().catch((e) => toast(`Gagal memuat: ${e.message}`, true));
