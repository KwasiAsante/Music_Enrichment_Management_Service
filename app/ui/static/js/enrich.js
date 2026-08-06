/**
 * Enrich page — start a bulk run via POST /api/v1/enrich/run, then poll
 * GET /api/v1/enrich/jobs/{job_id} until it finishes. On completion,
 * refetches /api/v1/enrich/log so "Recent Enrich Activity" reflects the
 * run without a full page reload.
 */

const POLL_INTERVAL_MS = 1500;
let pollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('run-btn')?.addEventListener('click', startRun);
});

function splitFilter(value) {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

async function startRun() {
  const runBtn = document.getElementById('run-btn');
  const artist = splitFilter(document.getElementById('filter-artist').value);
  const album = splitFilter(document.getElementById('filter-album').value);
  const redo = splitFilter(document.getElementById('filter-redo').value);
  const redoSkipped = document.getElementById('redo-skipped').checked;
  const dryRun = document.getElementById('dry-run').checked;

  runBtn.disabled = true;
  runBtn.textContent = 'Starting…';

  try {
    const res = await fetch(`/api/v1/enrich/run?dry_run=${dryRun}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist, album, redo, redo_skipped: redoSkipped }),
    });
    if (!res.ok) throw new Error(`failed to start run (HTTP ${res.status})`);
    const data = await res.json();

    showRunStatus();
    setBadge('queued', 'badge-blue');
    pollJob(data.job_id);
  } catch (err) {
    setBadge(`error: ${err.message}`, 'badge-red');
    showRunStatus();
  } finally {
    runBtn.disabled = false;
    runBtn.textContent = 'Start Enrichment Run';
  }
}

function showRunStatus() {
  document.getElementById('run-status').style.display = 'block';
}

function setBadge(text, cls) {
  const badge = document.getElementById('run-badge');
  badge.className = `badge ${cls}`;
  badge.textContent = text;
}

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);

  const check = async () => {
    try {
      const res = await fetch(`/api/v1/enrich/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) throw new Error(`job lookup failed (HTTP ${res.status})`);
      const job = await res.json();
      renderJob(job);

      if (job.status === 'success' || job.status === 'failed') {
        clearInterval(pollTimer);
        pollTimer = null;
        refreshRecentActivity();
      }
    } catch (err) {
      setBadge(`error: ${err.message}`, 'badge-red');
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  check();
  pollTimer = setInterval(check, POLL_INTERVAL_MS);
}

function renderJob(job) {
  const badgeClass =
    job.status === 'success' ? 'badge-green' : job.status === 'failed' ? 'badge-red' : 'badge-blue';
  setBadge(job.status, badgeClass);

  const total = job.progress_total || 0;
  const current = job.progress_current || 0;
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;

  document.getElementById('run-progress-text').textContent = `${current} / ${total}`;
  document.getElementById('run-progress-fill').style.width = `${pct}%`;

  const logEl = document.getElementById('run-log');
  const lines = (job.log || '').split('\n').filter(Boolean);
  logEl.innerHTML = lines
    .map((line) => {
      const cls = /fail|crash|error/i.test(line) ? 'lerr' : 'linfo';
      return `<div class="log-line"><span class="${cls}">${escapeHtml(line)}</span></div>`;
    })
    .join('');
  logEl.scrollTop = logEl.scrollHeight;
}

async function refreshRecentActivity() {
  try {
    const res = await fetch('/api/v1/enrich/log?limit=20');
    if (!res.ok) return;
    const rows = await res.json();
    const list = document.getElementById('recent-activity-list');

    if (rows.length === 0) {
      list.innerHTML = `<div class="empty"><div class="empty-icon">🗒</div>No enrichment activity yet — run one above to see it here.</div>`;
      return;
    }

    list.innerHTML = rows.map(renderActivityRow).join('');
  } catch {
    // Non-fatal — the live job status already told the user what happened.
  }
}

function renderActivityRow(item) {
  const badgeClass = item.level === 'error' ? 'badge-red' : item.level === 'warning' ? 'badge-yellow' : 'badge-green';
  const title = item.artist ? `${item.artist}${item.album ? ' — ' + item.album : ''}` : 'enrichment';
  return `
    <div class="result-card">
      <div class="info">
        <div class="info-title">${escapeHtml(title)}</div>
        <div class="info-meta">
          <span class="badge ${badgeClass}">${escapeHtml(item.level)}</span>
          <span>${escapeHtml(item.message)}</span>
        </div>
      </div>
      <div class="info-meta">${escapeHtml(item.ts)}</div>
    </div>
  `;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
