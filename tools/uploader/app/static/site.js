(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('form');
  const picker = $('files');
  const dropzone = $('dropzone');
  const note = $('note');
  const noDate = $('no-date');
  const date = $('date');
  const submit = $('submit');
  const theme = $('theme');
  const progress = $('progress-fill');
  const progressTrack = $('progress-track');
  let busy = false;
  let activeJob = null;
  let pollTimer = null;
  let selected = [];

  const bytes = n => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;
  const setMessage = (s, error = false) => {
    $('status').textContent = s;
    $('error').hidden = !error;
    $('error').textContent = error ? s : '';
  };
  const setProgress = pct => {
    const n = Math.max(0, Math.min(100, Number(pct) || 0));
    progress.style.width = `${n}%`;
    progressTrack.setAttribute('aria-valuenow', String(Math.round(n)));
    $('percentage').textContent = n ? `${Math.round(n)}%` : '';
  };
  const setBusy = yes => {
    busy = yes;
    submit.disabled = yes;
    submit.textContent = yes ? 'working…' : 'upload';
  };
  const setTheme = mode => {
    document.documentElement.dataset.theme = mode;
    theme.textContent = mode === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('uploader-theme', mode); } catch (_) { /* private browsing */ }
  };
  let savedTheme = null;
  try { savedTheme = localStorage.getItem('uploader-theme'); } catch (_) { /* private browsing */ }
  setTheme(savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  theme.addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  note.addEventListener('input', () => $('note-count').textContent = `${note.value.length.toLocaleString()} / 12,000`);
  noDate.addEventListener('change', () => { date.disabled = noDate.checked; });

  const displayFiles = () => {
    const total = selected.reduce((sum, f) => sum + f.size, 0);
    $('selection').textContent = selected.length ? `${selected.length} file${selected.length === 1 ? '' : 's'} · ${bytes(total)}` : 'no files selected';
    const list = $('file-list');
    list.replaceChildren();
    selected.slice(0, 12).forEach(f => {
      const li = document.createElement('li');
      const name = document.createElement('span'); name.textContent = f.name;
      const size = document.createElement('span'); size.textContent = bytes(f.size);
      li.append(name, size); list.append(li);
    });
    if (selected.length > 12) {
      const li = document.createElement('li');
      li.textContent = `and ${selected.length - 12} more…`;
      list.append(li);
    }
  };
  picker.addEventListener('change', () => { selected = Array.from(picker.files); displayFiles(); });
  ['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, ev => {
    ev.preventDefault(); if (!busy) dropzone.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, ev => {
    ev.preventDefault(); dropzone.classList.remove('dragging');
  }));
  dropzone.addEventListener('drop', ev => {
    if (busy) return;
    const items = Array.from(ev.dataTransfer?.files || []);
    if (!items.length) return;
    selected = items;
    displayFiles();
  });

  async function api(url, options) {
    const res = await fetch(url, {cache: 'no-store', ...options});
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || `Server returned ${res.status}`);
    return payload;
  }
  async function refreshHealth() {
    try {
      const payload = await api('/api/status');
      Object.entries(payload.components || {}).forEach(([key, ok]) => {
        const node = document.querySelector(`[data-service="${key}"]`);
        if (node) { node.classList.toggle('ok', !!ok); node.classList.toggle('down', !ok); }
      });
    } catch (_) { document.querySelectorAll('.service').forEach(node => node.classList.add('down')); }
  }
  async function refreshRecent() {
    try {
      const data = await api('/api/jobs');
      const parent = $('recent'); parent.replaceChildren();
      if (!data.jobs.length) { parent.textContent = 'No recent jobs.'; return; }
      for (const job of data.jobs) {
        const row = document.createElement('div'); row.className = 'recent-item';
        const info = document.createElement('span');
        info.textContent = `${job.collection} · ${job.date} · ${job.state}`;
        const btn = document.createElement('button'); btn.type = 'button';
        if (job.state === 'failed' || job.state === 'pending_push') {
          btn.textContent = 'retry';
          btn.addEventListener('click', async () => {
            try { await api(`/api/jobs/${job.id}/retry`, {method: 'POST'}); watch(job.id); }
            catch (e) { setMessage(e.message, true); }
          });
        } else {
          btn.textContent = job.state === 'published' ? 'published' : `${Math.round(job.progress || 0)}%`;
          btn.disabled = true;
        }
        row.append(info, btn); parent.append(row);
      }
    } catch (e) { $('recent').textContent = 'Recent jobs unavailable.'; }
  }
  async function watch(id) {
    activeJob = id;
    if (pollTimer) clearInterval(pollTimer);
    const tick = async () => {
      try {
        const job = await api(`/api/jobs/${id}`);
        setProgress(job.progress ?? 0);
        setMessage(job.message || job.state, job.state === 'failed');
        if (['published', 'failed'].includes(job.state)) {
          clearInterval(pollTimer); pollTimer = null;
          setBusy(false);
          if (job.state === 'published') {
            selected = []; picker.value = ''; displayFiles(); note.value = '';
            $('note-count').textContent = '0 / 12,000';
          }
          await refreshRecent(); await refreshHealth();
        }
      } catch (e) { setMessage(`Status check failed: ${e.message}`, true); }
    };
    setBusy(true);
    await tick();
    if (busy) pollTimer = setInterval(tick, 2400);
  }

  form.addEventListener('submit', ev => {
    ev.preventDefault(); if (busy) return;
    if (!selected.length && !note.value.trim()) { setMessage('Choose a file or write a note.', true); return; }
    if (selected.length > 30) { setMessage('Maximum 30 files per upload.', true); return; }
    const data = new FormData();
    data.append('collection', form.querySelector('input[name="collection"]:checked').value);
    data.append('date', date.value);
    data.append('no_date', String(noDate.checked));
    data.append('note', note.value);
    data.append('allow_duplicates', String($('allow-duplicates').checked));
    selected.forEach(file => data.append('files', file, file.name));
    setBusy(true); setProgress(0); setMessage('Uploading to server…');
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.upload.addEventListener('progress', ev => {
      if (ev.lengthComputable) {
        const pct = Math.round(ev.loaded * 100 / ev.total);
        setProgress(pct);
        $('status').textContent = `Transferring files · ${pct}%`;
      }
    });
    xhr.onload = async () => {
      let result = {};
      try { result = JSON.parse(xhr.responseText); } catch (_) { /* bad server response */ }
      if (xhr.status === 202 && result.id) {
        setProgress(0);
        await watch(result.id);
      } else {
        setBusy(false); setMessage(result.error || `Upload failed (HTTP ${xhr.status})`, true);
      }
    };
    xhr.onerror = () => { setBusy(false); setMessage('Network error. Refresh activity before resubmitting to avoid duplicates.', true); };
    xhr.ontimeout = () => { setBusy(false); setMessage('Request timed out. Check activity before retrying.', true); };
    xhr.send(data);
  });
  refreshHealth(); refreshRecent();
  setInterval(refreshHealth, 30000);
  setInterval(refreshRecent, 45000);
})();
