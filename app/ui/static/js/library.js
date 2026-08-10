/**
 * Library page — browse-only, no writes happen from this page.
 *
 * Two independent axes on top of the existing "view" filter (all /
 * unmapped / enriched / skipped):
 *
 *   - Layout (list / grid) — a pure CSS toggle on the already-rendered
 *     `.result-card` markup (a `.grid-mode` class on the container), so
 *     switching it never re-fetches anything. Persisted to localStorage.
 *   - Group by artist — bucket results under artist headers instead of a
 *     flat list. This DOES change the data shape (GET
 *     /api/v1/library/albums/grouped instead of /albums, unpaginated), so
 *     it gets its own render path and hides pagination while active.
 *     Persisted to localStorage.
 *
 * "Skipped" stays structurally different — GET /api/v1/library/skipped
 * returns a flat, unpaginated list with its own fields
 * (match_percentage/threshold/skipped_reason) — so it disables the
 * folder/source filters and the group-by toggle, same as it already
 * disabled the artist filter.
 *
 * Initial view/artist/folder/source come from the URL so links from the
 * Dashboard's stat cards land pre-filtered, matching what the server
 * already rendered — this only re-fetches on top of that if the person
 * then changes a filter, or if a saved "group by artist" preference means
 * the server's flat render needs replacing with the grouped one.
 */

const DEBOUNCE_MS = 350;
const urlParams = new URLSearchParams(window.location.search);
const state = {
  artist: urlParams.get('artist') || '',
  folder: urlParams.get('folder') || '',
  view: urlParams.get('view') || 'all',
  source: urlParams.get('source') || '',
  page: 1,
  limit: 50,
  groupByArtist: localStorage.getItem('library_group_by_artist') === '1',
  layout: localStorage.getItem('library_layout') || 'list',
};
let debounceTimer = null;

const VIEW_LABELS = {
  all: 'Albums',
  unmapped: 'Albums',
  enriched: 'Albums',
  skipped: 'Skipped (low-confidence matches)',
};

document.addEventListener('DOMContentLoaded', () => {
  applyLayout();
  document.getElementById('toggle-group').checked = state.groupByArtist;

  document.getElementById('filter-artist').addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      state.artist = e.target.value.trim();
      state.page = 1;
      loadPage();
    }, DEBOUNCE_MS);
  });

  document.getElementById('filter-folder').addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      state.folder = e.target.value.trim();
      state.page = 1;
      loadPage();
    }, DEBOUNCE_MS);
  });

  document.getElementById('filter-source').addEventListener('change', (e) => {
    state.source = e.target.value;
    state.page = 1;
    loadPage();
  });

  document.getElementById('filter-view').addEventListener('change', (e) => {
    state.view = e.target.value;
    state.page = 1;

    const isSkipped = state.view === 'skipped';
    const folderInput = document.getElementById('filter-folder');
    const artistInput = document.getElementById('filter-artist');
    const sourceSelect = document.getElementById('filter-source');
    const groupToggle = document.getElementById('toggle-group');

    artistInput.disabled = isSkipped;
    artistInput.title = isSkipped ? 'Not filterable in the Skipped view' : '';
    folderInput.disabled = isSkipped;
    folderInput.title = isSkipped ? 'Not filterable in the Skipped view' : '';
    sourceSelect.disabled = isSkipped;
    groupToggle.disabled = isSkipped;

    document.getElementById('section-label-text').textContent = VIEW_LABELS[state.view];
    loadPage();
  });

  document.getElementById('toggle-group').addEventListener('change', (e) => {
    state.groupByArtist = e.target.checked;
    localStorage.setItem('library_group_by_artist', state.groupByArtist ? '1' : '0');
    loadPage();
  });

  document.getElementById('layout-list').addEventListener('click', () => setLayout('list'));
  document.getElementById('layout-grid').addEventListener('click', () => setLayout('grid'));

  document.getElementById('prev-page').addEventListener('click', () => {
    if (state.page > 1) {
      state.page -= 1;
      loadPage();
    }
  });

  document.getElementById('next-page').addEventListener('click', () => {
    state.page += 1;
    loadPage();
  });

  // The server always renders the flat/paginated view. If a saved
  // preference wants grouping, replace it now — layout doesn't need a
  // reload since it's CSS-only (see applyLayout()).
  if (state.groupByArtist && state.view !== 'skipped') loadPage();
});

// ── Layout (list / grid) — CSS-only, no fetch ────────────────────────────
function setLayout(layout) {
  state.layout = layout;
  localStorage.setItem('library_layout', layout);
  applyLayout();
}

function applyLayout() {
  const listEl = document.getElementById('album-list');
  listEl.classList.toggle('grid-mode', state.layout === 'grid');
  document.getElementById('layout-list').classList.toggle('active', state.layout === 'list');
  document.getElementById('layout-grid').classList.toggle('active', state.layout === 'grid');
}

// ── Data loading ──────────────────────────────────────────────────────────
async function loadPage() {
  if (state.view === 'skipped') return loadSkipped();
  if (state.groupByArtist) return loadGrouped();

  const listEl = document.getElementById('album-list');
  document.getElementById('pagination').style.display = '';
  const qs = new URLSearchParams({
    unmapped: state.view === 'unmapped',
    enriched: state.view === 'enriched',
    page: state.page,
    limit: state.limit,
  });
  if (state.artist) qs.set('artist', state.artist);
  if (state.folder) qs.set('folder', state.folder);
  if (state.source) qs.set('source', state.source);

  try {
    const res = await fetch(`/api/v1/library/albums?${qs}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const data = await res.json();
    renderAlbums(data);
    renderPagination(data);
  } catch (err) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">⚠</div>${escapeHtml(err.message)}</div>`;
  }
}

async function loadGrouped() {
  const listEl = document.getElementById('album-list');
  document.getElementById('pagination').style.display = 'none';
  const qs = new URLSearchParams({
    unmapped: state.view === 'unmapped',
    enriched: state.view === 'enriched',
  });
  if (state.artist) qs.set('artist', state.artist);
  if (state.folder) qs.set('folder', state.folder);
  if (state.source) qs.set('source', state.source);

  try {
    const res = await fetch(`/api/v1/library/albums/grouped?${qs}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const data = await res.json();
    renderGrouped(data);
  } catch (err) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">⚠</div>${escapeHtml(err.message)}</div>`;
  }
}

async function loadSkipped() {
  const listEl = document.getElementById('album-list');
  document.getElementById('pagination').style.display = 'none';

  try {
    const res = await fetch('/api/v1/library/skipped');
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const rows = await res.json();
    document.getElementById('lib-total').textContent = rows.length;

    if (rows.length === 0) {
      listEl.innerHTML =
        `<div class="empty"><div class="empty-icon">✓</div>` +
        `Nothing skipped — every confident VGMDB match has been tagged.</div>`;
      return;
    }
    listEl.innerHTML = rows.map(renderSkippedRow).join('');
  } catch (err) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">⚠</div>${escapeHtml(err.message)}</div>`;
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────
function renderAlbums(data) {
  const listEl = document.getElementById('album-list');
  document.getElementById('lib-total').textContent = data.total;

  if (data.albums.length === 0) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No albums match these filters.</div>`;
    return;
  }

  listEl.innerHTML = data.albums.map((a) => renderAlbumRow(a)).join('');
}

function renderGrouped(data) {
  const listEl = document.getElementById('album-list');
  document.getElementById('lib-total').textContent = data.total;

  if (data.groups.length === 0) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No albums match these filters.</div>`;
    return;
  }

  const truncatedNote = data.truncated
    ? `<div class="group-truncated">Showing the first ${data.total_artists} artists — narrow the filters to see the rest (${data.total} albums match in total).</div>`
    : '';

  listEl.innerHTML =
    truncatedNote +
    data.groups.map((g) => `
      <div class="artist-group">
        <div class="artist-group-header">${escapeHtml(g.artist)} <span class="artist-group-count">${g.count}</span></div>
        <div class="artist-group-albums">
          ${g.albums.map((a) => renderAlbumRow(a, { hideArtist: true })).join('')}
        </div>
      </div>
    `).join('');
}

function renderAlbumRow(a, { hideArtist = false } = {}) {
  let badgeClass = 'badge-yellow';
  let badgeText = 'unmapped';
  if (a.enriched) {
    badgeClass = 'badge-green';
    badgeText = 'enriched';
  } else if (a.mapped) {
    badgeClass = 'badge-blue';
    badgeText = 'mapped';
  }
  const title = hideArtist ? escapeHtml(a.album) : `${escapeHtml(a.artist)} — ${escapeHtml(a.album)}`;
  return `
    <div class="result-card">
      <div class="info">
        <div class="info-title">${title}</div>
        <div class="info-meta">
          <span class="badge ${badgeClass}">${badgeText}</span>
          ${a.mapping_source ? `<span class="badge badge-source">${escapeHtml(a.mapping_source)}</span>` : ''}
          <span>${escapeHtml(a.folder)}</span>
        </div>
      </div>
    </div>
  `;
}

function renderSkippedRow(s) {
  return `
    <div class="result-card">
      <div class="info">
        <div class="info-title">${escapeHtml(s.artist)} — ${escapeHtml(s.album)}</div>
        <div class="info-meta">
          <span class="badge badge-purple">skipped: ${escapeHtml(s.skipped_reason)}</span>
          <span>match ${escapeHtml(s.match_percentage)} (threshold ${escapeHtml(s.threshold)})</span>
          <span>vgmdb:${escapeHtml(s.vgmdb_id)}</span>
        </div>
      </div>
    </div>
  `;
}

function renderPagination(data) {
  const pagination = document.getElementById('pagination');
  const totalPages = data.total ? Math.ceil(data.total / data.limit) : 1;

  pagination.style.display = data.total <= data.limit ? 'none' : 'flex';
  document.getElementById('page-info').textContent = `Page ${data.page} of ${totalPages}`;
  document.getElementById('prev-page').disabled = data.page <= 1;
  document.getElementById('next-page').disabled = data.page * data.limit >= data.total;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
