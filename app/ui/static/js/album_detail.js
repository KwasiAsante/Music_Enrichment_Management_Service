/**
 * Album detail page. Almost everything is server-rendered (see
 * templates/album_detail.html) — the only interactivity here is:
 *
 *   - Flip the cover between front/back (only present when both exist).
 *   - Fall back to a plain placeholder if the art request 404s after the
 *     page already rendered (e.g. the file was removed moments ago) —
 *     same "missing thumbnail is expected, not an error" treatment the
 *     Library grid view already gives cover art.
 *   - Re-enrich just this album via POST /api/v1/enrich/run, scoped with
 *     `artist`+`album`+`redo` filters the same way Library's bulk
 *     "Re-enrich Selected" button is (see library.js's
 *     handleBulkReenrich()) — `artist` here is the top-level folder
 *     segment (what run_bulk() actually groups/matches artists by, per
 *     app/core/beets_enricher.py's `by_artist`), not the tag-derived
 *     artist name, so it stays correct even when those two differ.
 */

const REENRICH_POLL_MS = 1500;

document.addEventListener('DOMContentLoaded', () => {
  const img = document.getElementById('album-art-img');
  if (img) {
    img.addEventListener('error', () => {
      const placeholder = document.createElement('div');
      placeholder.className = 'album-art-placeholder';
      placeholder.textContent = '♪';
      img.replaceWith(placeholder);
      document.getElementById('album-art-flip')?.remove();
    }, { once: true });
  }

  const flipBtn = document.getElementById('album-art-flip');
  if (flipBtn && img) {
    flipBtn.addEventListener('click', () => {
      const showing = flipBtn.dataset.showing === 'front' ? 'back' : 'front';
      flipBtn.dataset.showing = showing;
      img.src = `${window.APP_URL_BASE}/api/v1/library/art?folder=${encodeURIComponent(flipBtn.dataset.folder)}&side=${showing}`;
      img.dataset.side = showing;
      flipBtn.textContent = showing === 'front' ? '⟳ Show back cover' : '⟳ Show front cover';
    });
  }

  document.getElementById('reenrich-btn')?.addEventListener('click', handleReenrich);
});

async function handleReenrich() {
  const btn = document.getElementById('reenrich-btn');
  const statusEl = document.getElementById('reenrich-status');
  const { folder, album } = btn.dataset;
  const artistFolder = folder.split('/')[0];

  if (!confirm(`Re-enrich "${album}"? This clears it from the enriched log and re-runs enrichment for just this album.`)) return;

  btn.disabled = true;
  btn.textContent = 'Starting…';
  statusEl.innerHTML = '';

  try {
    const res = await fetch(`${window.APP_URL_BASE}/api/v1/enrich/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist: [artistFolder], album: [album], redo: [album], redo_skipped: false }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `request failed (HTTP ${res.status})`);

    btn.textContent = 'Re-enriching…';
    pollReenrichJob(data.job_id);
  } catch (err) {
    statusEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
    btn.disabled = false;
    btn.textContent = '⟳ Re-enrich';
  }
}

function pollReenrichJob(jobId) {
  const btn = document.getElementById('reenrich-btn');
  const statusEl = document.getElementById('reenrich-status');

  const check = async () => {
    try {
      const res = await fetch(`${window.APP_URL_BASE}/api/v1/enrich/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) throw new Error(`job lookup failed (HTTP ${res.status})`);
      const job = await res.json();
      if (job.status !== 'success' && job.status !== 'failed') return;

      clearInterval(timer);
      if (job.status === 'failed') {
        statusEl.innerHTML = `<span class="badge badge-red">failed</span> <span>Check the <a href="${window.APP_URL_BASE}/enrich">Enrich page</a> for details.</span>`;
        btn.disabled = false;
        btn.textContent = '⟳ Re-enrich';
        return;
      }

      const enrichedCount = (job.result?.enriched || []).length;
      if (enrichedCount > 0) {
        statusEl.innerHTML = `<span class="badge badge-green">done</span> <span>Reloading…</span>`;
        window.location.reload();
      } else {
        statusEl.innerHTML = `<span class="badge badge-yellow">no change</span> <span>Nothing was re-enriched — check the <a href="${window.APP_URL_BASE}/enrich">Enrich page</a> log for why.</span>`;
        btn.disabled = false;
        btn.textContent = '⟳ Re-enrich';
      }
    } catch (err) {
      clearInterval(timer);
      statusEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
      btn.disabled = false;
      btn.textContent = '⟳ Re-enrich';
    }
  };

  const timer = setInterval(check, REENRICH_POLL_MS);
  check();
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
