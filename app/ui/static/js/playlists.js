/**
 * Playlists page — POST /api/v1/playlist/convert with the uploaded file,
 * render the per-entry results, and hand the converted playlist back to
 * the browser as a download. Nothing is persisted server-side (see that
 * endpoint's docstring), so the download itself is built client-side from
 * the response body — there's no server path to link to.
 *
 * Manual matching: any row's "Fix" button opens an inline panel to search
 * for an album (GET /library/albums?folder=, reused as-is — it's already
 * a substring search over artist+album) and pick the track by hand
 * (GET /playlist/album-tracks, which also returns a best-effort
 * suggested_path once an album's picked). Picking a track updates
 * lastResult.entries in place; the downloadable playlist text is always
 * rebuilt from that array (see rebuildPlaylistText) rather than trusting
 * the original convert response, so manual fixes are reflected without a
 * second server round trip.
 */

let lastResult = null;
let lastFilename = 'playlist.m3u';
let albumSearchDebounce = null;
const ALBUM_SEARCH_DEBOUNCE_MS = 350;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('convert-form')?.addEventListener('submit', handleConvertSubmit);
  document.getElementById('download-btn')?.addEventListener('click', handleDownload);
});

const METHOD_BADGE = {
  unchanged: 'badge-green',
  renumbered: 'badge-green',
  relocated: 'badge-blue',
  fuzzy: 'badge-yellow',
  manual: 'badge-purple',
  unmatched: 'badge-red',
};

async function handleConvertSubmit(e) {
  e.preventDefault();
  const fileInput = document.getElementById('playlist-file');
  const resultEl = document.getElementById('convert-result');
  const submitBtn = document.getElementById('convert-btn');
  const downloadBtn = document.getElementById('download-btn');
  const file = fileInput.files[0];
  if (!file) return;

  submitBtn.disabled = true;
  submitBtn.textContent = 'Converting…';
  resultEl.innerHTML = '';
  downloadBtn.style.display = 'none';
  document.getElementById('entries-section').style.display = 'none';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/v1/playlist/convert', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `convert failed (HTTP ${res.status})`);

    lastResult = data;
    lastFilename = `converted-${data.filename}`;

    renderSummary();
    renderEntries();
    downloadBtn.style.display = data.total > 0 ? '' : 'none';
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Convert';
  }
}

function renderSummary() {
  const resultEl = document.getElementById('convert-result');
  const badgeClass = lastResult.unmatched > 0 ? 'badge-yellow' : 'badge-green';
  resultEl.innerHTML =
    `<span class="badge ${badgeClass}">done</span> ` +
    `<span>${lastResult.matched}/${lastResult.total} matched` +
    (lastResult.unmatched ? `, ${lastResult.unmatched} unmatched` : '') +
    `</span>`;
}

function renderEntries() {
  const section = document.getElementById('entries-section');
  const countEl = document.getElementById('entries-count');
  const container = document.getElementById('entries-results');
  const entries = lastResult.entries;

  countEl.textContent = entries.length;
  container.innerHTML = entries.length
    ? entries.map((entry, i) => renderEntryCard(entry, i)).join('')
    : `<div class="empty"><div class="empty-icon">🎵</div>No entries found in this playlist.</div>`;
  section.style.display = entries.length ? 'block' : 'none';

  container.querySelectorAll('.js-fix').forEach((btn) => {
    btn.addEventListener('click', () => toggleMatchPanel(Number(btn.closest('.result-card').dataset.index)));
  });
}

function basenameOf(path) {
  return path.split('/').pop().split('\\').pop();
}

function renderEntryCard(entry, index) {
  const badgeClass = METHOD_BADGE[entry.method] || 'badge-source';
  const target = entry.resolved_path
    ? escapeHtml(entry.resolved_path)
    : `<span class="lerr">unmatched</span>`;
  return `
    <div class="result-card" data-index="${index}">
      <div class="info">
        <div class="info-title">${escapeHtml(basenameOf(entry.original_path))}</div>
        <div class="info-meta">
          <span class="badge ${badgeClass}">${escapeHtml(entry.method)}</span>
          <span>${escapeHtml(entry.original_path)} → ${target}</span>
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-ghost btn-sm js-fix" type="button">Fix</button>
      </div>
    </div>
  `;
}

// ── Manual match panel ──────────────────────────────────────────────────
function toggleMatchPanel(index) {
  const card = document.querySelector(`.result-card[data-index="${index}"]`);
  const existing = card.querySelector('.match-panel');
  if (existing) {
    existing.remove();
    card.classList.remove('match-open');
    return;
  }

  // Only one panel open at a time — keeps the page from getting cluttered.
  document.querySelectorAll('.match-panel').forEach((p) => p.remove());
  document.querySelectorAll('.result-card.match-open').forEach((c) => c.classList.remove('match-open'));

  card.classList.add('match-open');
  card.insertAdjacentHTML('beforeend', `
    <div class="match-panel">
      <input type="text" class="js-album-search" placeholder="Search album by artist or album name…">
      <div class="match-list js-album-results"></div>
    </div>
  `);
  const panel = card.querySelector('.match-panel');
  const searchInput = panel.querySelector('.js-album-search');
  searchInput.addEventListener('input', () => {
    clearTimeout(albumSearchDebounce);
    const q = searchInput.value.trim();
    albumSearchDebounce = setTimeout(() => searchAlbums(index, q), ALBUM_SEARCH_DEBOUNCE_MS);
  });
  searchInput.focus();
}

async function searchAlbums(index, query) {
  const card = document.querySelector(`.result-card[data-index="${index}"]`);
  const resultsEl = card?.querySelector('.js-album-results');
  if (!resultsEl) return;

  if (!query) {
    resultsEl.innerHTML = '';
    return;
  }

  resultsEl.innerHTML = `<div class="match-hint">Searching…</div>`;
  try {
    const res = await fetch(`/api/v1/library/albums?folder=${encodeURIComponent(query)}&limit=15`);
    if (!res.ok) throw new Error(`album search failed (HTTP ${res.status})`);
    const data = await res.json();

    if (data.albums.length === 0) {
      resultsEl.innerHTML = `<div class="match-hint">No albums match "${escapeHtml(query)}".</div>`;
      return;
    }

    resultsEl.innerHTML = data.albums.map((a) => `
      <button type="button" class="match-item js-album-pick" data-folder="${escapeHtml(a.folder)}">
        <span>${escapeHtml(a.artist)} — ${escapeHtml(a.album)}</span>
      </button>
    `).join('');
    resultsEl.querySelectorAll('.js-album-pick').forEach((btn) => {
      btn.addEventListener('click', () => pickAlbum(index, btn.dataset.folder));
    });
  } catch (err) {
    resultsEl.innerHTML = `<div class="match-hint">${escapeHtml(err.message)}</div>`;
  }
}

async function pickAlbum(index, folder) {
  const card = document.querySelector(`.result-card[data-index="${index}"]`);
  const resultsEl = card?.querySelector('.js-album-results');
  if (!resultsEl) return;

  const entry = lastResult.entries[index];
  resultsEl.innerHTML = `<div class="match-hint">Loading tracks in "${escapeHtml(folder)}"…</div>`;

  try {
    const params = new URLSearchParams({ folder, original_path: entry.original_path });
    const res = await fetch(`/api/v1/playlist/album-tracks?${params}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `could not list tracks (HTTP ${res.status})`);

    if (data.tracks.length === 0) {
      resultsEl.innerHTML = `<div class="match-hint">No audio files found in that folder.</div>`;
      return;
    }

    resultsEl.innerHTML =
      `<div class="match-hint">Pick the correct track in "${escapeHtml(folder)}":</div>` +
      data.tracks.map((t) => {
        const suggested = t.path === data.suggested_path;
        return `
          <button type="button" class="match-item js-track-pick${suggested ? ' match-item-suggested' : ''}" data-path="${escapeHtml(t.path)}">
            <span>${escapeHtml(t.relative_path)}</span>
            ${suggested ? '<span class="badge badge-green">suggested</span>' : ''}
          </button>
        `;
      }).join('');
    resultsEl.querySelectorAll('.js-track-pick').forEach((btn) => {
      btn.addEventListener('click', () => applyManualMatch(index, btn.dataset.path));
    });
  } catch (err) {
    resultsEl.innerHTML = `<div class="match-hint">${escapeHtml(err.message)}</div>`;
  }
}

function applyManualMatch(index, resolvedPath) {
  const entry = lastResult.entries[index];
  const wasMatched = Boolean(entry.resolved_path);
  entry.resolved_path = resolvedPath;
  entry.method = 'manual';
  if (!wasMatched) {
    lastResult.matched += 1;
    lastResult.unmatched -= 1;
  }

  document.querySelector(`.result-card[data-index="${index}"]`)?.classList.remove('match-open');
  renderSummary();
  renderEntries();
}

// ── Download (rebuilt from lastResult.entries, so manual fixes apply) ────
function rebuildPlaylistText() {
  if (!lastResult) return '';
  const lines = [];
  if (lastResult.playlist.startsWith('#EXTM3U')) lines.push('#EXTM3U');
  for (const entry of lastResult.entries) {
    if (entry.extinf) lines.push(entry.extinf);
    lines.push(
      entry.resolved_path
        ? `${lastResult.artist_root}/${entry.resolved_path}`
        : `# UNMATCHED: ${entry.original_path}`,
    );
  }
  return lines.join('\n') + '\n';
}

function handleDownload() {
  if (!lastResult) return;
  const blob = new Blob([rebuildPlaylistText()], { type: 'audio/x-mpegurl' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = lastFilename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
