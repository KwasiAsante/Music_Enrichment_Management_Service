/**
 * Library page — browse-only, no writes happen from this page.
 *
 * Two independent axes on top of the existing "view" filter (all /
 * unmapped / enriched / skipped):
 *
 *   - Layout (list / grid) — re-renders the already-fetched data (see
 *     `lastData`/`lastKind` below) with or without cover-art thumbnails;
 *     no network request for the album list itself. Persisted to
 *     localStorage. Grid mode's `<img>` tags each pull one image from
 *     GET /api/v1/library/art?folder=... (a folder cover file, or
 *     whatever's embedded in the first audio file); browsers cache those
 *     for a day (see the endpoint's Cache-Control) and lazy-load them, so
 *     switching to grid only fetches thumbnails actually scrolled into
 *     view. Albums with no art just show a plain placeholder — a missing
 *     thumbnail is expected, not an error.
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
// The last successfully-loaded data + which render path it came from, so
// the layout toggle can re-render with/without art thumbnails without a
// network round trip. 'kind' is null until the first load — the server's
// initial render doesn't populate this, so the very first layout toggle
// on a fresh page load falls back to loadPage() (see setLayout()).
let lastData = null;
let lastKind = null; // 'albums' | 'grouped' | 'skipped'
// Which artist groups are collapsed, by artist name. In-memory only —
// deliberately not persisted, so it starts fresh each page load/reload
// rather than accumulating stale entries as the artist list changes with
// filters.
const collapsedArtists = new Set();

const VIEW_LABELS = {
  all: 'Albums',
  unmapped: 'Albums',
  enriched: 'Albums',
  skipped: 'Skipped (low-confidence matches)',
};

document.addEventListener('DOMContentLoaded', () => {
  applyLayoutButtons();
  document.getElementById('toggle-group').checked = state.groupByArtist;
  updateGroupControlsVisibility();

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
    updateGroupControlsVisibility();
    loadPage();
  });

  document.getElementById('toggle-group').addEventListener('change', (e) => {
    state.groupByArtist = e.target.checked;
    localStorage.setItem('library_group_by_artist', state.groupByArtist ? '1' : '0');
    updateGroupControlsVisibility();
    loadPage();
  });

  document.getElementById('layout-list').addEventListener('click', () => setLayout('list'));
  document.getElementById('layout-grid').addEventListener('click', () => setLayout('grid'));

  document.getElementById('collapse-all').addEventListener('click', () => {
    document.querySelectorAll('#album-list .artist-group').forEach((g) => {
      g.classList.add('collapsed');
      collapsedArtists.add(g.dataset.artist);
    });
  });

  document.getElementById('expand-all').addEventListener('click', () => {
    document.querySelectorAll('#album-list .artist-group').forEach((g) => g.classList.remove('collapsed'));
    collapsedArtists.clear();
  });

  // Delegated on the (stable) container rather than per-header, so it
  // keeps working across re-renders (filter changes, layout toggles)
  // without needing to re-attach listeners each time.
  document.getElementById('album-list').addEventListener('click', (e) => {
    const header = e.target.closest('.artist-group-header');
    if (!header) return;
    toggleGroup(header.closest('.artist-group'));
  });

  document.getElementById('album-list').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const header = e.target.closest('.artist-group-header');
    if (!header) return;
    e.preventDefault();
    toggleGroup(header.closest('.artist-group'));
  });

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

  // The server always renders the flat/paginated view without art
  // markup. If a saved preference wants grouping and/or grid art, do one
  // real load now so lastData gets populated and rendering catches up.
  if ((state.groupByArtist || state.layout === 'grid') && state.view !== 'skipped') loadPage();
});

// ── Layout (list / grid) ──────────────────────────────────────────────────
function setLayout(layout) {
  state.layout = layout;
  localStorage.setItem('library_layout', layout);
  applyLayoutButtons();

  // No client-side fetch has happened yet (still showing the server's
  // initial render) — we don't have folder paths to build art URLs from,
  // so do one real load. After that, toggling just re-renders lastData.
  if (lastData === null) {
    loadPage();
    return;
  }
  if (lastKind === 'albums') {
    renderAlbums(lastData);
  } else if (lastKind === 'grouped') {
    renderGrouped(lastData);
  }
  // 'skipped' rows never show art either way — nothing to re-render.
}

function applyLayoutButtons() {
  const listEl = document.getElementById('album-list');
  listEl.classList.toggle('grid-mode', state.layout === 'grid');
  document.getElementById('layout-list').classList.toggle('active', state.layout === 'list');
  document.getElementById('layout-grid').classList.toggle('active', state.layout === 'grid');
}

// ── Group-by-artist: collapse/expand ─────────────────────────────────────
function updateGroupControlsVisibility() {
  const show = state.groupByArtist && state.view !== 'skipped';
  document.getElementById('group-collapse-controls').style.display = show ? 'flex' : 'none';
}

function toggleGroup(groupEl) {
  const artist = groupEl.dataset.artist;
  const collapsed = groupEl.classList.toggle('collapsed');
  groupEl.querySelector('.artist-group-header').setAttribute('aria-expanded', String(!collapsed));
  if (collapsed) {
    collapsedArtists.add(artist);
  } else {
    collapsedArtists.delete(artist);
  }
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
    lastData = data;
    lastKind = 'albums';
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
    lastData = data;
    lastKind = 'grouped';
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
    lastData = rows;
    lastKind = 'skipped';
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
  updateGroupControlsVisibility();

  if (data.groups.length === 0) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No albums match these filters.</div>`;
    return;
  }

  const truncatedNote = data.truncated
    ? `<div class="group-truncated">Showing the first ${data.total_artists} artists — narrow the filters to see the rest (${data.total} albums match in total).</div>`
    : '';

  listEl.innerHTML =
    truncatedNote +
    data.groups.map((g) => {
      const collapsed = collapsedArtists.has(g.artist);
      return `
        <div class="artist-group${collapsed ? ' collapsed' : ''}" data-artist="${escapeAttr(g.artist)}">
          <div class="artist-group-header" role="button" tabindex="0" aria-expanded="${!collapsed}">
            <span class="artist-group-chevron">▾</span>
            ${escapeHtml(g.artist)}
            <span class="artist-group-count">${g.count}</span>
          </div>
          <div class="artist-group-albums">
            ${g.albums.map((a) => renderAlbumRow(a, { hideArtist: true })).join('')}
          </div>
        </div>
      `;
    }).join('');
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
  // Art only in grid mode — list mode never requests thumbnails at all.
  const art = state.layout === 'grid' && a.folder
    ? `<div class="album-art-wrap">` +
      `<img class="album-art" src="/api/v1/library/art?folder=${encodeURIComponent(a.folder)}" ` +
      `alt="" loading="lazy" decoding="async" onerror="this.remove()">` +
      `</div>`
    : '';
  return `
    <div class="result-card">
      ${art}
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

function escapeAttr(str) {
  return escapeHtml(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
