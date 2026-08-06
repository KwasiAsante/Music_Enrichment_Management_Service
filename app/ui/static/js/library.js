/**
 * Library page — browse-only. Artist filter (debounced) and an "unmapped
 * only" checkbox both re-query GET /api/v1/library/albums; Prev/Next walk
 * pages. No writes happen from this page.
 */

const DEBOUNCE_MS = 350;
const state = { artist: '', unmapped: false, page: 1, limit: 50 };
let debounceTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('filter-artist').addEventListener('input', (e) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      state.artist = e.target.value.trim();
      state.page = 1;
      loadPage();
    }, DEBOUNCE_MS);
  });

  document.getElementById('filter-unmapped').addEventListener('change', (e) => {
    state.unmapped = e.target.checked;
    state.page = 1;
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
  const listEl = document.getElementById('album-list');
  const params = new URLSearchParams({
    unmapped: state.unmapped,
    page: state.page,
    limit: state.limit,
  });
  if (state.artist) params.set('artist', state.artist);

  try {
    const res = await fetch(`/api/v1/library/albums?${params}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const data = await res.json();
    renderAlbums(data);
    renderPagination(data);
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
  const badgeClass = a.mapped ? 'badge-green' : 'badge-yellow';
  const badgeText = a.mapped ? 'mapped' : 'unmapped';
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
