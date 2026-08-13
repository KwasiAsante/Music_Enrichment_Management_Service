/**
 * Logs page — filters re-query GET /api/v1/logs with source, feature,
 * minimum level, and artist (activity rows only). Artist is debounced.
 */

const DEBOUNCE_MS = 350;
let debounceTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('filter-source').addEventListener('change', loadLogs);
  document.getElementById('filter-feature').addEventListener('change', loadLogs);
  document.getElementById('filter-level').addEventListener('change', loadLogs);
  document.getElementById('refresh-btn').addEventListener('click', loadLogs);

  document.getElementById('filter-artist').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(loadLogs, DEBOUNCE_MS);
  });
});

async function loadLogs() {
  const logView = document.getElementById('log-view');
  const source = document.getElementById('filter-source').value;
  const feature = document.getElementById('filter-feature').value;
  const level = document.getElementById('filter-level').value;
  const artist = document.getElementById('filter-artist').value.trim();

  const params = new URLSearchParams({ limit: 200, source });
  if (feature) params.set('feature', feature);
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
    logView.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No entries match these filters.</div>`;
    return;
  }

  logView.innerHTML = rows.map(renderLogLine).join('');
}

function levelClass(level) {
  if (level === 'error') return 'lerr';
  if (level === 'warning') return 'lwarn';
  if (level === 'debug') return 'ldebug';
  return 'linfo';
}

function renderLogLine(item) {
  const tag = item.feature || item.category;
  const diag = item.source === 'diagnostic' ? ' · diag' : '';
  const who = item.artist
    ? `${escapeHtml(item.artist)}${item.album ? ' — ' + escapeHtml(item.album) : ''}: `
    : '';
  return `
    <div class="log-line">
      <span class="lt">${escapeHtml(item.ts)}</span>
      <span class="${levelClass(item.level)}">[${escapeHtml(tag)}${diag}] ${who}${escapeHtml(item.message)}</span>
    </div>
  `;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
