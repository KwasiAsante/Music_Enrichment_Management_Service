/**
 * Dashboard quick actions. Scan is synchronous (POST returns the result
 * directly); Enrichment is a background job, same poll pattern as
 * enrich.js but condensed into a single status line instead of a full
 * progress panel — this page just needs "is it done and what happened",
 * not a live log.
 *
 * "also remove empty folders" is unchecked by default and maps to the
 * scan's ``cleanup`` flag, which deletes empty/audio-less folders — the
 * one destructive option this page exposes, so it stays opt-in per click
 * rather than remembered between visits.
 *
 * Both flows finish by refreshing stats (GET /api/v1/library/stats) and
 * recent activity (GET /api/v1/logs/?limit=15 — the same unfiltered,
 * 15-row query the page renders server-side on load) so the dashboard
 * reflects the result without a page reload.
 */

const POLL_INTERVAL_MS = 1500;

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('scan-btn').addEventListener('click', runScan);
  document.getElementById('fix-paths-btn').addEventListener('click', runFixPaths);
  document.getElementById('enrich-btn').addEventListener('click', runEnrichment);
  document.getElementById('activity-filter').addEventListener('change', refreshActivity);
});

function setStatus(text, cls) {
  const el = document.getElementById('dash-status');
  el.style.display = 'block';
  el.innerHTML = `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
}

function disableButtons(disabled) {
  document.getElementById('scan-btn').disabled = disabled;
  document.getElementById('fix-paths-btn').disabled = disabled;
  document.getElementById('enrich-btn').disabled = disabled;
}

// ── Scan ─────────────────────────────────────────────────────────────────────
async function runScan() {
  disableButtons(true);
  const cleanup = document.getElementById('scan-cleanup').checked;
  setStatus(cleanup ? 'Scanning library (with cleanup)…' : 'Scanning library…', 'badge-blue');

  try {
    const res = await fetch('/api/v1/library/scan?dry_run=false', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cleanup }),
    });
    if (!res.ok) throw new Error(`scan failed (HTTP ${res.status})`);
    const data = await res.json();

    let summary = `Scan complete — ${data.total} albums, ${data.new} new, ${data.without_mb_id} without MB id`;
    if (data.cleanup) {
      summary += ` · cleanup removed ${data.cleanup.album_folders_removed} album folder(s), ${data.cleanup.artist_folders_removed} artist folder(s)`;
    }
    const hasErrors = (data.errors && data.errors.length) || (data.cleanup && data.cleanup.errors.length);
    setStatus(summary, hasErrors ? 'badge-yellow' : 'badge-green');
    await Promise.all([refreshStats(), refreshActivity()]);
  } catch (err) {
    setStatus(err.message, 'badge-red');
  } finally {
    disableButtons(false);
  }
}

// ── Fix Artist Paths ─────────────────────────────────────────────────────────
async function runFixPaths() {
  disableButtons(true);
  setStatus('Fixing non-Latin artist paths…', 'badge-blue');

  try {
    const res = await fetch('/api/v1/artist/fix-all-paths?dry_run=false', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (!res.ok) throw new Error(`fix-all-paths failed (HTTP ${res.status})`);
    const data = await res.json();

    const summary =
      `Fix Artist Paths complete — ${data.fixed.length} fixed, ` +
      `${data.not_found.length} not found, ${data.unchanged.length} unchanged`;
    const cls = data.error || data.not_found.length ? 'badge-yellow' : 'badge-green';
    setStatus(data.error ? `${summary} — ${data.error}` : summary, cls);
    await refreshActivity();
  } catch (err) {
    setStatus(err.message, 'badge-red');
  } finally {
    disableButtons(false);
  }
}

// ── Enrichment ───────────────────────────────────────────────────────────────
async function runEnrichment() {
  disableButtons(true);
  setStatus('Starting enrichment…', 'badge-blue');

  try {
    const res = await fetch('/api/v1/enrich/run?dry_run=false', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist: [], album: [], redo: [], redo_skipped: false }),
    });
    if (!res.ok) throw new Error(`failed to start (HTTP ${res.status})`);
    const { job_id } = await res.json();
    await pollEnrichJob(job_id);
  } catch (err) {
    setStatus(err.message, 'badge-red');
    disableButtons(false);
  }
}

function pollEnrichJob(jobId) {
  return new Promise((resolve) => {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/enrich/jobs/${encodeURIComponent(jobId)}`);
        if (!res.ok) throw new Error(`job lookup failed (HTTP ${res.status})`);
        const job = await res.json();

        if (job.status === 'success' || job.status === 'failed') {
          clearInterval(timer);
          const r = job.result || {};
          const summary =
            job.status === 'success'
              ? `Enrichment complete — ${r.enriched?.length ?? 0} enriched, ` +
                `${r.skipped?.length ?? 0} skipped, ${r.failed?.length ?? 0} failed`
              : 'Enrichment failed — check Logs for details';
          setStatus(summary, job.status === 'success' ? 'badge-green' : 'badge-red');
          await Promise.all([refreshStats(), refreshActivity()]);
          disableButtons(false);
          resolve();
          return;
        }

        setStatus(`Enriching… ${job.progress_current || 0}/${job.progress_total || 0}`, 'badge-blue');
      } catch (err) {
        clearInterval(timer);
        setStatus(err.message, 'badge-red');
        disableButtons(false);
        resolve();
      }
    }, POLL_INTERVAL_MS);
  });
}

// ── shared refreshers ────────────────────────────────────────────────────────
async function refreshStats() {
  const res = await fetch('/api/v1/library/stats');
  if (!res.ok) return;
  const s = await res.json();
  document.getElementById('stat-total').textContent = s.total;
  document.getElementById('stat-enriched').textContent = s.enriched;
  document.getElementById('stat-unmapped').textContent = s.unmapped;
  document.getElementById('stat-skipped').textContent = s.skipped;
  document.getElementById('dash-total').textContent = s.total;
}

async function refreshActivity() {
  const category = document.getElementById('activity-filter').value;
  const params = new URLSearchParams({ limit: 15 });
  if (category) params.set('category', category);

  const res = await fetch(`/api/v1/logs/?${params}`);
  if (!res.ok) return;
  const rows = await res.json();
  const list = document.getElementById('recent-activity-list');

  if (rows.length === 0) {
    list.innerHTML =
      `<div class="empty"><div class="empty-icon">🗒</div>` +
      `${category ? 'No activity in this category yet.' : 'No activity yet — runs will show up here once a scan or enrichment happens.'}</div>`;
    return;
  }

  list.innerHTML = rows.map(renderActivityRow).join('');
}

function renderActivityRow(item) {
  const badgeClass = item.level === 'error' ? 'badge-red' : item.level === 'warning' ? 'badge-yellow' : 'badge-green';
  const title = item.artist ? `${item.artist}${item.album ? ' — ' + item.album : ''}` : item.message;
  return `
    <div class="result-card">
      <div class="info">
        <div class="info-title">${escapeHtml(title)}</div>
        <div class="info-meta">
          <span class="badge ${badgeClass}">${escapeHtml(item.category)}</span>
          ${item.artist ? `<span>${escapeHtml(item.message)}</span>` : ''}
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
