/**
 * Mappings page — search/set/skip/restore/exclude against
 * /api/v1/mapping/*, plus an artist filter (debounced) against
 * GET /api/v1/mapping/unmapped, plus multi-select bulk actions
 * (Skip Selected / Exclude Artist(s)) on the Unmapped Albums list.
 *
 * Three independent lists share this file, each scoped to its own
 * container so their identical `.result-card` markup never gets
 * double-counted or cross-wired:
 *   #results-container  — unmapped albums (search / set / skip / bulk)
 *   #skipped-container   — albums marked vgmdb_id="skip" (restore)
 *   #excluded-list        — excluded-artist chips (remove)
 *
 * One initUnmappedRow() call per unmapped `.result-card`. Search replaces
 * that row's action buttons with a result (badge + input + Set/Skip);
 * Set/Skip both funnel into setMapping(), which PUTs the mapping and
 * removes the row on success (and, since that album now exists in
 * vgmdb_mapping.json, refreshes the Skipped list too when it was a skip).
 *
 * Bulk selection state (`selectedUnmapped`, a Map of mb_release_id ->
 * {artist, album, folder}) lives independently of the DOM rather than
 * being read off checkboxes on demand — `renderResults()` fully replaces
 * `#results-container`'s innerHTML on every filter change, which would
 * silently wipe any DOM-only selection state. Deliberately cleared on
 * every re-render for the same reason: a selection made against a
 * pre-filter set of rows shouldn't silently carry over to a different
 * filtered view where some of those rows aren't even visible anymore.
 */

const DEBOUNCE_MS = 350;
let debounceTimer = null;
const selectedUnmapped = new Map();

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('#results-container .result-card[data-mb-id]').forEach(initUnmappedRow);
  document.querySelectorAll('#skipped-container .result-card[data-mb-id]').forEach(initSkippedRow);
  document.querySelectorAll('#excluded-list .js-remove-excluded').forEach(initExcludedChip);

  document.getElementById('filter-artist')?.addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => loadUnmapped(e.target.value.trim()), DEBOUNCE_MS);
  });

  document.getElementById('import-form')?.addEventListener('submit', handleImportSubmit);
  document.getElementById('excluded-form')?.addEventListener('submit', handleExcludeSubmit);

  document.getElementById('select-all-unmapped')?.addEventListener('change', (e) => {
    document.querySelectorAll('#results-container .js-row-select:not(:disabled)').forEach((cb) => {
      cb.checked = e.target.checked;
      toggleRowSelection(cb.closest('.result-card'), e.target.checked);
    });
  });
  document.getElementById('bulk-skip-btn')?.addEventListener('click', handleBulkSkip);
  document.getElementById('bulk-exclude-btn')?.addEventListener('click', handleBulkExclude);
  document.getElementById('bulk-clear-btn')?.addEventListener('click', clearSelection);
});

// ── Bulk select ───────────────────────────────────────────────────────────
function toggleRowSelection(row, checked) {
  const mbId = row?.dataset.mbId;
  if (!row || !mbId) return;
  if (checked) {
    selectedUnmapped.set(mbId, { artist: row.dataset.artist, album: row.dataset.album, folder: row.dataset.folder });
  } else {
    selectedUnmapped.delete(mbId);
  }
  row.classList.toggle('result-card-selected', checked);
  updateBulkToolbar();
}

function clearSelection({ clearResult = true } = {}) {
  document.querySelectorAll('#results-container .js-row-select').forEach((cb) => { cb.checked = false; });
  document.querySelectorAll('#results-container .result-card-selected').forEach((row) => row.classList.remove('result-card-selected'));
  selectedUnmapped.clear();
  updateBulkToolbar();
  if (clearResult) document.getElementById('bulk-result').innerHTML = '';
}

function updateBulkToolbar() {
  const toolbar = document.getElementById('bulk-toolbar');
  const count = selectedUnmapped.size;
  document.getElementById('bulk-count').textContent = `${count} selected`;
  toolbar.style.display = count > 0 ? 'flex' : 'none';

  const selectAll = document.getElementById('select-all-unmapped');
  const selectable = document.querySelectorAll('#results-container .js-row-select:not(:disabled)');
  if (selectAll) {
    selectAll.checked = selectable.length > 0 && count >= selectable.length;
    selectAll.indeterminate = count > 0 && count < selectable.length;
  }
}

async function handleBulkSkip() {
  const items = [...selectedUnmapped.entries()].map(([mb_release_id, v]) => ({ mb_release_id, ...v }));
  if (items.length === 0) return;
  if (!confirm(`Skip ${items.length} album(s)? They'll move to Skipped Albums and won't be asked about again.`)) return;

  const btn = document.getElementById('bulk-skip-btn');
  const resultEl = document.getElementById('bulk-result');
  btn.disabled = true;
  btn.textContent = 'Skipping…';

  try {
    const res = await fetch('/api/v1/mapping/bulk-skip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `bulk skip failed (HTTP ${res.status})`);

    data.skipped.forEach((mbId) => {
      document.querySelector(`#results-container .result-card[data-mb-id="${CSS.escape(mbId)}"]`)?.remove();
      selectedUnmapped.delete(mbId);
    });
    updateUnmappedCount();
    updateBulkToolbar();
    await refreshSkipped();

    resultEl.innerHTML = data.failed.length
      ? `<span class="badge badge-yellow">partial</span> <span>${data.skipped.length} skipped, ${data.failed.length} failed</span>`
      : `<span class="badge badge-green">done</span> <span>${data.skipped.length} album(s) skipped</span>`;
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Skip Selected';
  }
}

async function handleBulkExclude() {
  const artists = [...new Set([...selectedUnmapped.values()].map((v) => v.artist).filter(Boolean))];
  if (artists.length === 0) return;
  if (!confirm(`Exclude ${artists.length} artist(s)? Their albums will drop out of Unmapped and be skipped during bulk enrichment.`)) return;

  const btn = document.getElementById('bulk-exclude-btn');
  const resultEl = document.getElementById('bulk-result');
  btn.disabled = true;
  btn.textContent = 'Excluding…';

  try {
    const res = await fetch('/api/v1/mapping/bulk-exclude-artists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artists }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `bulk exclude failed (HTTP ${res.status})`);

    data.added.forEach(addExcludedChip);
    resultEl.innerHTML =
      `<span class="badge badge-green">done</span> ` +
      `<span>${data.added.length} artist${data.added.length === 1 ? '' : 's'} excluded` +
      (data.already_excluded.length ? `, ${data.already_excluded.length} already excluded` : '') +
      `</span>`;

    // Excluded artists' albums disappear from Unmapped — reload it.
    // This also clears selectedUnmapped via renderResults(), so no need
    // to do it explicitly here.
    await loadUnmapped(document.getElementById('filter-artist')?.value.trim() || '');
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Exclude Artist(s)';
  }
}

// ── Excluded artists ─────────────────────────────────────────────────────
function initExcludedChip(btn) {
  btn.addEventListener('click', () => removeExcludedArtist(btn.closest('.chip')));
}

async function handleExcludeSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('excluded-input');
  const resultEl = document.getElementById('excluded-result');
  const artist = input.value.trim();
  if (!artist) return;

  const submitBtn = e.target.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  resultEl.innerHTML = '';

  try {
    const res = await fetch('/api/v1/mapping/excluded-artists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `add failed (HTTP ${res.status})`);

    if (data.changed) {
      addExcludedChip(data.artist);
      input.value = '';
    } else {
      resultEl.innerHTML = `<span class="badge badge-yellow">already excluded</span>`;
    }
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    submitBtn.disabled = false;
  }
}

function addExcludedChip(artist) {
  const list = document.getElementById('excluded-list');
  list.querySelector('.chip-empty')?.remove();

  const chip = document.createElement('span');
  chip.className = 'chip';
  chip.dataset.artist = artist;
  chip.innerHTML =
    `${escapeHtml(artist)} ` +
    `<button class="chip-remove js-remove-excluded" type="button" title="Un-exclude" aria-label="Un-exclude ${escapeHtml(artist)}">×</button>`;
  list.appendChild(chip);
  initExcludedChip(chip.querySelector('.js-remove-excluded'));
  updateExcludedCount();
}

async function removeExcludedArtist(chip) {
  const artist = chip.dataset.artist;
  const btn = chip.querySelector('.js-remove-excluded');
  btn.disabled = true;

  try {
    const res = await fetch(`/api/v1/mapping/excluded-artists/${encodeURIComponent(artist)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`remove failed (HTTP ${res.status})`);
    chip.remove();
    updateExcludedCount();
    const list = document.getElementById('excluded-list');
    if (!list.querySelector('.chip')) {
      list.innerHTML = `<span class="chip-empty">No artists excluded.</span>`;
    }
    // The artist's albums may now belong in "unmapped" — refresh it.
    await loadUnmapped(document.getElementById('filter-artist')?.value.trim() || '');
  } catch (err) {
    btn.disabled = false;
    document.getElementById('excluded-result').innerHTML =
      `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  }
}

function updateExcludedCount() {
  const remaining = document.querySelectorAll('#excluded-list .chip').length;
  document.querySelectorAll('.js-excluded-count').forEach((el) => (el.textContent = remaining));
}

// ── Skipped albums ────────────────────────────────────────────────────────
function initSkippedRow(row) {
  row.querySelector('.js-restore')?.addEventListener('click', () => restoreSkipped(row));
}

async function restoreSkipped(row) {
  const btn = row.querySelector('.js-restore');
  btn.disabled = true;
  btn.textContent = 'Restoring…';

  try {
    const res = await fetch(`/api/v1/mapping/${encodeURIComponent(row.dataset.mbId)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`restore failed (HTTP ${res.status})`);
    row.remove();
    updateSkippedCount();
    // The album is unmapped again now — refresh that list too.
    await loadUnmapped(document.getElementById('filter-artist')?.value.trim() || '');
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Restore';
  }
}

function updateSkippedCount() {
  const remaining = document.querySelectorAll('#skipped-container .result-card').length;
  document.querySelectorAll('.js-skipped-count').forEach((el) => (el.textContent = remaining));
  if (remaining === 0) {
    document.getElementById('skipped-container').innerHTML =
      `<div class="empty"><div class="empty-icon">–</div>No albums marked as skipped.</div>`;
  }
}

// ── Backup & restore ─────────────────────────────────────────────────────
async function handleImportSubmit(e) {
  e.preventDefault();
  const fileInput = document.getElementById('import-file');
  const mode = document.getElementById('import-mode').value;
  const resultEl = document.getElementById('import-result');
  const submitBtn = e.target.querySelector('button[type="submit"]');
  const file = fileInput.files[0];
  if (!file) return;

  if (mode === 'replace' &&
      !confirm('This replaces the entire mapping with the contents of the ' +
               'file — any entry not in it will be removed. Continue?')) {
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'Importing…';
  resultEl.innerHTML = '';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`/api/v1/mapping/import?mode=${encodeURIComponent(mode)}`, {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `import failed (HTTP ${res.status})`);

    resultEl.innerHTML =
      `<span class="badge badge-green">done</span> ` +
      `<span>+${data.added} added, ${data.updated} updated` +
      (data.mode === 'replace' ? `, ${data.removed} removed` : '') +
      (data.skipped_invalid ? `, ${data.skipped_invalid} skipped (invalid)` : '') +
      ` — ${data.total_after} total</span>`;

    e.target.reset();
    await loadUnmapped(document.getElementById('filter-artist')?.value.trim() || '');
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = '⇧ Import';
  }
}

async function loadUnmapped(artist) {
  const container = document.getElementById('results-container');
  const params = new URLSearchParams();
  if (artist) params.set('artist', artist);

  try {
    const res = await fetch(`/api/v1/mapping/unmapped?${params}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const rows = await res.json();
    renderResults(rows);
  } catch (err) {
    container.innerHTML = `<div class="empty"><div class="empty-icon">⚠</div>${escapeHtml(err.message)}</div>`;
  }
}

function renderResults(rows) {
  const container = document.getElementById('results-container');
  document.querySelectorAll('.js-unmapped-count').forEach((el) => (el.textContent = rows.length));
  selectedUnmapped.clear();
  updateBulkToolbar();

  if (rows.length === 0) {
    container.innerHTML =
      `<div class="empty"><div class="empty-icon">✓</div>` +
      `Nothing unmapped — every album has a VGMDB association.</div>`;
    return;
  }

  container.innerHTML = `<div class="results">${rows.map(renderRow).join('')}</div>`;
  container.querySelectorAll('.result-card[data-mb-id]').forEach(initUnmappedRow);
}

function renderRow(item) {
  const disabled = item.mb_release_id ? '' : 'disabled title="No MusicBrainz release id — can\'t be skipped"';
  return `
    <div class="result-card"
         data-mb-id="${escapeHtml(item.mb_release_id || '')}"
         data-artist="${escapeHtml(item.artist)}"
         data-album="${escapeHtml(item.album)}"
         data-folder="${escapeHtml(item.folder)}">
      <label class="row-select">
        <input type="checkbox" class="js-row-select" ${disabled}>
      </label>
      <div class="info">
        <div class="info-title">${escapeHtml(item.artist)} — ${escapeHtml(item.album)}</div>
        <div class="info-meta js-meta">
          <span class="badge badge-yellow">unmapped</span>
          <span>mb: ${escapeHtml(item.mb_release_id || 'none')}</span>
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-ghost btn-sm js-search" type="button">Search VGMDB</button>
        <button class="btn btn-danger-ghost btn-sm js-skip" type="button">Skip</button>
      </div>
    </div>
  `;
}

function initUnmappedRow(row) {
  const ctx = {
    mbId: row.dataset.mbId,
    artist: row.dataset.artist,
    album: row.dataset.album,
    folder: row.dataset.folder,
  };

  row.querySelector('.js-search')?.addEventListener('click', () => runSearch(row, ctx));
  row.querySelector('.js-skip')?.addEventListener('click', () => setMapping(row, { ...ctx, vgmdbId: 'skip' }));
  row.querySelector('.js-row-select')?.addEventListener('change', (e) => toggleRowSelection(row, e.target.checked));
}

async function runSearch(row, ctx) {
  const searchBtn = row.querySelector('.js-search');
  const metaEl = row.querySelector('.js-meta');

  searchBtn.disabled = true;
  searchBtn.textContent = 'Searching…';

  try {
    const res = await fetch('/api/v1/mapping/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mb_release_id: ctx.mbId || null,
        album: ctx.album,
        artist: ctx.artist,
        folder: ctx.folder || null,
      }),
    });
    if (!res.ok) throw new Error(`search failed (HTTP ${res.status})`);
    const data = await res.json();
    renderSearchResult(row, data, ctx);
  } catch (err) {
    metaEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
    searchBtn.disabled = false;
    searchBtn.textContent = 'Search VGMDB';
  }
}

function renderSearchResult(row, data, ctx) {
  const metaEl = row.querySelector('.js-meta');
  const actionsEl = row.querySelector('.row-actions');

  // A direct MB→VGMDB relationship is the strongest signal; a "suggested"
  // hint from the catalog/barcode/title pipeline is next-best. Otherwise
  // fall back to showing how many raw hints came back so the person knows
  // there's something to look through instead of a flat "no match".
  const best = data.suggested
    ? data.suggested
    : data.mb_vgmdb_id
      ? { vgmdb_id: data.mb_vgmdb_id, title: '(direct MusicBrainz → VGMDB link)' }
      : null;

  const hintCount = data.catalog_hints.length + data.barcode_hints.length + data.title_hints.length;

  if (best) {
    metaEl.innerHTML =
      `<span class="badge badge-blue">match found</span> ` +
      `<span>vgmdb:${escapeHtml(best.vgmdb_id)} — "${escapeHtml(best.title)}"</span>`;
  } else if (hintCount > 0) {
    metaEl.innerHTML =
      `<span class="badge badge-purple">${hintCount} hint${hintCount === 1 ? '' : 's'}</span> ` +
      `<span>no auto match — enter a vgmdb id manually</span>`;
  } else {
    metaEl.innerHTML = `<span class="badge badge-yellow">no match</span>`;
  }

  actionsEl.innerHTML = `
    <input type="text" class="js-vgmdb-input" placeholder="vgmdb id" value="${best ? escapeHtml(best.vgmdb_id) : ''}">
    <button class="btn btn-success btn-sm js-set" type="button">Set</button>
    <button class="btn btn-danger-ghost btn-sm js-skip" type="button">Skip</button>
  `;

  actionsEl.querySelector('.js-set').addEventListener('click', () => {
    const value = actionsEl.querySelector('.js-vgmdb-input').value.trim();
    if (!value) return;
    setMapping(row, { ...ctx, vgmdbId: value });
  });
  actionsEl.querySelector('.js-skip').addEventListener('click', () => setMapping(row, { ...ctx, vgmdbId: 'skip' }));
}

async function setMapping(row, ctx) {
  const actionsEl = row.querySelector('.row-actions');
  actionsEl.querySelectorAll('button').forEach((b) => (b.disabled = true));

  try {
    const res = await fetch(`/api/v1/mapping/${encodeURIComponent(ctx.mbId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        vgmdb_id: ctx.vgmdbId,
        artist: ctx.artist,
        album: ctx.album,
        folder: ctx.folder,
        source: 'manual',
      }),
    });
    if (!res.ok) throw new Error(`set failed (HTTP ${res.status})`);
    row.remove();
    selectedUnmapped.delete(ctx.mbId);
    updateUnmappedCount();
    updateBulkToolbar();
    if (ctx.vgmdbId === 'skip') await refreshSkipped();
  } catch (err) {
    actionsEl.querySelectorAll('button').forEach((b) => (b.disabled = false));
    row.querySelector('.js-meta').innerHTML =
      `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  }
}

function updateUnmappedCount() {
  const remaining = document.querySelectorAll('#results-container .result-card').length;
  document.querySelectorAll('.js-unmapped-count').forEach((el) => (el.textContent = remaining));

  if (remaining === 0) {
    document.getElementById('results-container').innerHTML =
      `<div class="empty"><div class="empty-icon">✓</div>` +
      `Nothing unmapped — every album has a VGMDB association.</div>`;
  }
}

async function refreshSkipped() {
  const container = document.getElementById('skipped-container');
  try {
    const res = await fetch('/api/v1/mapping/skipped');
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const rows = await res.json();
    document.querySelectorAll('.js-skipped-count').forEach((el) => (el.textContent = rows.length));

    if (rows.length === 0) {
      container.innerHTML = `<div class="empty"><div class="empty-icon">–</div>No albums marked as skipped.</div>`;
      return;
    }
    container.innerHTML = `<div class="results">${rows.map(renderSkippedRow).join('')}</div>`;
    container.querySelectorAll('.result-card[data-mb-id]').forEach(initSkippedRow);
  } catch {
    // Non-fatal — the Skipped section just won't reflect this change until
    // the next full page load.
  }
}

function renderSkippedRow(item) {
  return `
    <div class="result-card"
         data-mb-id="${escapeHtml(item.mb_release_id)}"
         data-artist="${escapeHtml(item.artist)}"
         data-album="${escapeHtml(item.album)}">
      <div class="info">
        <div class="info-title">${escapeHtml(item.artist)} — ${escapeHtml(item.album)}</div>
        <div class="info-meta">
          <span class="badge badge-purple">skipped</span>
          <span>mb: ${escapeHtml(item.mb_release_id)}</span>
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-ghost btn-sm js-restore" type="button">Restore</button>
      </div>
    </div>
  `;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
