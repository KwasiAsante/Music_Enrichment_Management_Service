/**
 * Field Overrides page — search albums (against GET /api/v1/library/albums,
 * its `q` param OR's across artist/album/folder) and, once one is picked,
 * render a per-field value picker sourced from GET /api/v1/overrides/options
 * (VGMDB candidates + what's currently on disk + any saved override).
 * Save/Clear both go through PUT/DELETE /api/v1/overrides?folder=...
 *
 * Saving never touches a file — see the page's tip banner and
 * app/core/field_overrides.py — so this file only ever talks to the
 * overrides CRUD endpoints, never /enrich.
 */

const DEBOUNCE_MS = 350;
const FIELD_LABELS = {
  artist: 'Artist / Album Artist',
  composer: 'Composer',
  performer: 'Performer',
  arranger: 'Arranger',
  lyricist: 'Lyricist',
  genre: 'Genre',
  label: 'Label',
  catalog: 'Catalog Number',
};

let debounceTimer = null;
let searchToken = 0;
let currentFolder = null;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('search-input')?.addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    const value = e.target.value.trim();
    debounceTimer = setTimeout(() => runSearch(value), DEBOUNCE_MS);
  });
  document.getElementById('save-overrides-btn')?.addEventListener('click', saveOverrides);
  document.getElementById('clear-overrides-btn')?.addEventListener('click', clearOverrides);
});

// ── Search ────────────────────────────────────────────────────────────────
async function runSearch(q) {
  const container = document.getElementById('search-results');

  if (!q) {
    document.getElementById('search-count').textContent = '0';
    container.innerHTML = `<div class="empty"><div class="empty-icon">⌕</div>Start typing to find an album.</div>`;
    return;
  }

  // Guards against a slow earlier request resolving after a faster later
  // one and clobbering its (more current) results.
  const token = ++searchToken;
  try {
    const res = await fetch(`/api/v1/library/albums?q=${encodeURIComponent(q)}&limit=25`);
    if (!res.ok) throw new Error(`search failed (HTTP ${res.status})`);
    const data = await res.json();
    if (token !== searchToken) return;
    renderSearchResults(data.albums);
  } catch (err) {
    if (token !== searchToken) return;
    container.innerHTML = `<div class="empty"><div class="empty-icon">⚠</div>${escapeHtml(err.message)}</div>`;
  }
}

function renderSearchResults(rows) {
  const container = document.getElementById('search-results');
  document.getElementById('search-count').textContent = rows.length;

  if (rows.length === 0) {
    container.innerHTML = `<div class="empty"><div class="empty-icon">⌕</div>No matching albums.</div>`;
    return;
  }

  container.innerHTML = `<div class="results">${rows.map(renderResultRow).join('')}</div>`;
  container.querySelectorAll('.result-card[data-folder]').forEach((row) => {
    row.addEventListener('click', () => openEditor(row.dataset.folder));
  });
}

function renderResultRow(item) {
  const badge = item.enriched
    ? '<span class="badge badge-green">enriched</span>'
    : item.mapped
      ? '<span class="badge badge-blue">mapped</span>'
      : '<span class="badge badge-yellow">unmapped</span>';
  const selected = item.folder === currentFolder ? ' result-card-selected' : '';
  return `
    <div class="result-card${selected}" data-folder="${escapeHtml(item.folder)}" style="cursor:pointer;">
      <div class="info">
        <div class="info-title">${escapeHtml(item.artist)} — ${escapeHtml(item.album)}</div>
        <div class="info-meta">
          ${badge}
          <span>${escapeHtml(item.folder)}</span>
        </div>
      </div>
    </div>
  `;
}

// ── Editor ────────────────────────────────────────────────────────────────
async function openEditor(folder) {
  const section = document.getElementById('editor-section');
  const titleEl = document.getElementById('editor-title');
  const resultEl = document.getElementById('editor-result');

  section.style.display = 'block';
  resultEl.innerHTML = '';
  titleEl.textContent = 'Loading…';
  document.getElementById('editor-fields').innerHTML = '';
  document.getElementById('editor-warnings').innerHTML = '';
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });

  document.querySelectorAll('#search-results .result-card').forEach((row) => {
    row.classList.toggle('result-card-selected', row.dataset.folder === folder);
  });

  try {
    const res = await fetch(`/api/v1/overrides/options?folder=${encodeURIComponent(folder)}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const data = await res.json();
    currentFolder = folder;
    renderEditor(data);
  } catch (err) {
    titleEl.textContent = 'Field Overrides';
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  }
}

function renderEditor(data) {
  document.getElementById('editor-title').textContent = `Editing: ${data.artist} — ${data.album}`;
  document.getElementById('editor-meta').innerHTML = `
    ${data.mapped
      ? `<span class="badge badge-blue">vgmdb:${escapeHtml(data.vgmdb_id)}</span>`
      : '<span class="badge badge-yellow">no vgmdb mapping</span>'}
    <span>${escapeHtml(data.folder)}</span>
  `;
  document.getElementById('editor-warnings').innerHTML = (data.warnings || [])
    .map((w) => `<div class="album-warning">⚠ ${escapeHtml(w)}</div>`)
    .join('');

  const fieldsEl = document.getElementById('editor-fields');
  fieldsEl.innerHTML = data.fields.map(renderFieldRow).join('');

  fieldsEl.querySelectorAll('.settings-row[data-field]').forEach((row) => {
    const select = row.querySelector('.js-field-select');
    const customInput = row.querySelector('.js-field-custom');
    select.addEventListener('change', () => {
      customInput.style.display = select.value === '__custom__' ? 'block' : 'none';
    });
  });
}

function renderFieldRow(f) {
  const label = FIELD_LABELS[f.field] || f.field;
  const isCustom = !!(f.override_value && !f.candidates.some((c) => c.value === f.override_value));

  const options = [`<option value="">— No override —</option>`]
    .concat(f.candidates.map((c) => {
      const sel = !isCustom && f.override_value === c.value ? ' selected' : '';
      return `<option value="${escapeHtml(c.value)}"${sel}>${escapeHtml(c.label)}: ${escapeHtml(c.value)}</option>`;
    }))
    .concat([`<option value="__custom__"${isCustom ? ' selected' : ''}>Custom value…</option>`]);

  return `
    <div class="settings-row" data-field="${escapeHtml(f.field)}">
      <div class="settings-row-label">
        <label>${escapeHtml(label)}</label>
        ${f.current_value ? `<p class="settings-help">On disk: <code>${escapeHtml(f.current_value)}</code></p>` : ''}
      </div>
      <div class="settings-row-input">
        <select class="js-field-select">${options.join('')}</select>
        <input type="text" class="js-field-custom" placeholder="Custom value..."
               style="display:${isCustom ? 'block' : 'none'};margin-top:6px;"
               value="${isCustom ? escapeHtml(f.override_value) : ''}">
      </div>
    </div>
  `;
}

// ── Save / Clear ──────────────────────────────────────────────────────────
async function saveOverrides() {
  if (!currentFolder) return;
  const fieldsEl = document.getElementById('editor-fields');
  const resultEl = document.getElementById('editor-result');
  const btn = document.getElementById('save-overrides-btn');

  const fields = {};
  fieldsEl.querySelectorAll('.settings-row[data-field]').forEach((row) => {
    const select = row.querySelector('.js-field-select');
    const customInput = row.querySelector('.js-field-custom');
    fields[row.dataset.field] = select.value === '__custom__' ? customInput.value.trim() : select.value;
  });

  btn.disabled = true;
  btn.textContent = 'Saving…';
  resultEl.innerHTML = '';

  try {
    const res = await fetch(`/api/v1/overrides?folder=${encodeURIComponent(currentFolder)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `save failed (HTTP ${res.status})`);

    const count = Object.keys(data.fields || {}).length;
    resultEl.innerHTML =
      `<span class="badge badge-green">saved</span> ` +
      `<span>${count} field override${count === 1 ? '' : 's'} set — applies next time this album is (re-)enriched.</span>`;
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Mapping';
  }
}

async function clearOverrides() {
  if (!currentFolder) return;
  if (!confirm('Clear every saved field override for this album?')) return;

  const resultEl = document.getElementById('editor-result');
  const btn = document.getElementById('clear-overrides-btn');
  btn.disabled = true;

  try {
    const res = await fetch(`/api/v1/overrides?folder=${encodeURIComponent(currentFolder)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`clear failed (HTTP ${res.status})`);
    resultEl.innerHTML = `<span class="badge badge-green">cleared</span>`;
    await openEditor(currentFolder);
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
