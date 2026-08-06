/**
 * Library page — browse-only, no writes happen from this page.
 *
 * "View" (all / unmapped / enriched / skipped) plus an artist filter
 * (debounced) re-query the appropriate endpoint. Skipped is structurally
 * different — GET /api/v1/library/skipped returns a flat, unpaginated list
 * with its own fields (match_percentage/threshold/skipped_reason), not an
 * AlbumsPage — so it gets its own render path and disables the artist
 * filter / hides pagination while active.
 *
 * Initial view/artist come from the URL (?view=&artist=) so links from the
 * Dashboard's stat cards land pre-filtered, matching what the server
 * already rendered — this only re-fetches on top of that if the person
 * then changes a filter.
 */

const DEBOUNCE_MS = 350;
const urlParams = new URLSearchParams(window.location.search);
const state = {
  artist: urlParams.get('artist') || '',
  view: urlParams.get('view') || 'all',
  page: 1,
  limit: 50,
};
let debounceTimer = null;

const VIEW_LABELS = {
  all: 'Albums',
  unmapped: 'Albums',
  enriched: 'Albums',
  skipped: 'Skipped (low-confidence matches)',
};

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('filter-artist').addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      state.artist = e.target.value.trim();
      state.page = 1;
      loadPage();
    }, DEBOUNCE_MS);
  });

  document.getElementById('filter-view').addEventListener('change', (e) => {
    state.view = e.target.value;
    state.page = 1;

    const artistInput = document.getElementById('filter-artist');
    artistInput.disabled = state.view === 'skipped';
    artistInput.title = state.view === 'skipped' ? 'Not filterable in the Skipped view' : '';

    document.getElementById('section-label-text').textContent = VIEW_LABELS[state.view];
    loadPage();
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
});

async function loadPage() {
  if (state.view === 'skipped') return loadSkipped();

  const listEl = document.getElementById('album-list');
  const qs = new URLSearchParams({
    unmapped: state.view === 'unmapped',
    enriched: state.view === 'enriched',
    page: state.page,
    limit: state.limit,
  });
  if (state.artist) qs.set('artist', state.artist);

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

function renderAlbums(data) {
  const listEl = document.getElementById('album-list');
  document.getElementById('lib-total').textContent = data.total;

  if (data.albums.length === 0) {
    listEl.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No albums match these filters.</div>`;
    return;
  }

  listEl.innerHTML = data.albums.map(renderAlbumRow).join('');
}

function renderAlbumRow(a) {
  let badgeClass = 'badge-yellow';
  let badgeText = 'unmapped';
  if (a.enriched) {
    badgeClass = 'badge-green';
    badgeText = 'enriched';
  } else if (a.mapped) {
    badgeClass = 'badge-blue';
    badgeText = 'mapped';
  }
  return `
    <div class="result-card">
      <div class="info">
        <div class="info-title">${escapeHtml(a.artist)} — ${escapeHtml(a.album)}</div>
        <div class="info-meta">
          <span class="badge ${badgeClass}">${badgeText}</span>
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
