/**
 * Mappings page — search/set/skip/restore/exclude against
 * /api/v1/mapping/*, plus an artist filter (debounced) against
 * GET /api/v1/mapping/unmapped.
 *
 * Three independent lists share this file, each scoped to its own
 * container so their identical `.result-card` markup never gets
 * double-counted or cross-wired:
 *   #results-container  — unmapped albums (search / set / skip)
 *   #skipped-container   — albums marked vgmdb_id="skip" (restore)
 *   #excluded-list        — excluded-artist chips (remove)
 *
 * One initUnmappedRow() call per unmapped `.result-card`. Search replaces
 * that row's action buttons with a result (badge + input + Set/Skip);
 * Set/Skip both funnel into setMapping(), which PUTs the mapping and
 * removes the row on success (and, since that album now exists in
 * vgmdb_mapping.json, refreshes the Skipped list too when it was a skip).
 */

const DEBOUNCE_MS = 350;
let debounceTimer = null;

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
});

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
  return `
    <div class="result-card"
         data-mb-id="${escapeHtml(item.mb_release_id || '')}"
         data-artist="${escapeHtml(item.artist)}"
         data-album="${escapeHtml(item.album)}"
         data-folder="${escapeHtml(item.folder)}">
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
    updateUnmappedCount();
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
