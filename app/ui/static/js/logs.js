/**
 * Logs page — filters (category/level/artist) all re-query
 * GET /api/v1/logs. Artist is debounced since it's free text; category
 * and level re-fetch immediately since they're selects, not typing.
 */

const DEBOUNCE_MS = 350;
let debounceTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('filter-category').addEventListener('change', loadLogs);
  document.getElementById('filter-level').addEventListener('change', loadLogs);
  document.getElementById('refresh-btn').addEventListener('click', loadLogs);

  document.getElementById('filter-artist').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(loadLogs, DEBOUNCE_MS);
  });
});

async function loadLogs() {
  const logView = document.getElementById('log-view');
  const category = document.getElementById('filter-category').value;
  const level = document.getElementById('filter-level').value;
  const artist = document.getElementById('filter-artist').value.trim();

  const params = new URLSearchParams({ limit: 100 });
  if (category) params.set('category', category);
  if (level) params.set('level', level);
  if (artist) params.set('artist', artist);

  try {
    const res = await fetch(`/api/v1/logs/?${params}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const rows = await res.json();
    renderLogs(rows);
  } catch (err) {
    logView.innerHTML = `<div class="empty"><div class="empty-icon">⚠</div>${escapeHtml(err.message)}</div>`;
  }
}

function renderLogs(rows) {
  const logView = document.getElementById('log-view');
  document.getElementById('log-count').textContent = rows.length;

  if (rows.length === 0) {
    logView.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No activity matches these filters.</div>`;
    return;
  }

  logView.innerHTML = rows.map(renderLogLine).join('');
}

function renderLogLine(item) {
  const levelClass = item.level === 'error' ? 'lerr' : item.level === 'warning' ? 'lwarn' : 'linfo';
  const who = item.artist ? `${escapeHtml(item.artist)}${item.album ? ' — ' + escapeHtml(item.album) : ''}: ` : '';
  return `
    <div class="log-line">
      <span class="lt">${escapeHtml(item.ts)}</span>
      <span class="${levelClass}">[${escapeHtml(item.category)}] ${who}${escapeHtml(item.message)}</span>
    </div>
  `;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
