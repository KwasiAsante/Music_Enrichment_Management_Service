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
 *   - View/edit/lock this album's tags via /api/v1/tags/* — see
 *     app/core/tag_editor.py and app/core/tag_locks.py. Unlike Field
 *     Overrides (which only takes effect on the next enrichment), a save
 *     here writes the files right away.
 *   - Two extra checkboxes (#lock-track-titles/#lock-track-artist) toggle
 *     the separate per-track lock pair via /api/v1/tags/track-locks —
 *     each track keeps its own title/artist value, unlike the field-row
 *     locks above which share one value across the whole album.
 */

const REENRICH_POLL_MS = 1500;

// Display order/labels for the standard field set app/core/tag_fields.py
// defines — anything not in this map (a custom tag) sorts after these,
// in whatever order the API returned it.
const TAG_FIELD_LABELS = {
  artist: 'Artist / Album Artist', album: 'Album', date: 'Date / Year',
  genre: 'Genre', composer: 'Composer', performer: 'Performer',
  arranger: 'Arranger', lyricist: 'Lyricist', label: 'Label', catalog: 'Catalog Number',
};
const STANDARD_FIELD_ORDER = Object.keys(TAG_FIELD_LABELS);

// Section order/labels for app/core/tag_fields.py's classify_tag_source()
// groups — 'standard' has no header (it's always first and self-evident);
// the rest only render a header when at least one field falls in it.
const TAG_SOURCE_SECTIONS = [
  { source: 'standard', label: null },
  { source: 'musicbrainz', label: 'MusicBrainz Tags' },
  { source: 'vgmdb', label: 'VGMDB Tags' },
  { source: 'custom', label: 'Custom Tags' },
];
const TAG_SOURCE_BADGES = {
  musicbrainz: '<span class="badge badge-source">musicbrainz</span>',
  vgmdb: '<span class="badge badge-purple">vgmdb</span>',
  custom: '<span class="badge badge-source">custom</span>',
};

let currentTagFolder = null;

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

  document.getElementById('edit-tags-btn')?.addEventListener('click', (e) => {
    openTagEditor(e.currentTarget.dataset.folder);
  });
  document.getElementById('cancel-tags-btn')?.addEventListener('click', () => {
    document.getElementById('tag-editor-section').style.display = 'none';
  });
  document.getElementById('add-tag-row-btn')?.addEventListener('click', addCustomTagRow);
  document.getElementById('save-tags-btn')?.addEventListener('click', saveTags);
});

async function handleReenrich() {
  const btn = document.getElementById('reenrich-btn');
  const statusEl = document.getElementById('reenrich-status');
  const { folder, album } = btn.dataset;
  // `folder` comes straight from album_list.json's "folder" value, which
  // is str(Path.relative_to(...)) on the server — backslash-separated on
  // a Windows bare-metal run, forward-slash in Docker/Linux. Split on
  // either so this doesn't silently degrade to "whole folder string as
  // the artist filter" (and therefore "no album matches") on Windows.
  const artistFolder = folder.split(/[/\\]/)[0];

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
        // run_bulk() sorts a non-enriched album into exactly one of these —
        // surface its actual reason instead of sending the user log-diving
        // for something the response already told us.
        const detail = (job.result?.failed || [])[0] || (job.result?.skipped || [])[0]
          || (job.result?.no_map || [])[0];
        const reasonHtml = detail?.reason
          ? `<span>${escapeHtml(detail.reason)}</span>`
          : `<span>Check the <a href="${window.APP_URL_BASE}/enrich">Enrich page</a> log for why.</span>`;
        statusEl.innerHTML = `<span class="badge badge-yellow">no change</span> ${reasonHtml}`;
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

// ── Tag editor ────────────────────────────────────────────────────────────
async function openTagEditor(folder) {
  const section = document.getElementById('tag-editor-section');
  const fieldsEl = document.getElementById('tag-editor-fields');
  const warningsEl = document.getElementById('tag-editor-warnings');
  const resultEl = document.getElementById('tag-editor-result');

  const opening = section.style.display === 'none';
  if (!opening) {
    section.style.display = 'none';
    return;
  }

  currentTagFolder = folder;
  section.style.display = 'block';
  fieldsEl.innerHTML = '';
  warningsEl.innerHTML = '';
  resultEl.innerHTML = '<span class="badge badge-source">loading…</span>';
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });

  try {
    const res = await fetch(`${window.APP_URL_BASE}/api/v1/tags/album?folder=${encodeURIComponent(folder)}`);
    if (!res.ok) throw new Error(`load failed (HTTP ${res.status})`);
    const data = await res.json();
    resultEl.innerHTML = '';
    renderTagEditor(data);
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
  }
}

function renderTagEditor(data) {
  const trackLocks = data.track_locks || {};
  document.getElementById('lock-track-titles').checked = !!trackLocks.title;
  document.getElementById('lock-track-artist').checked = !!trackLocks.artist;

  document.getElementById('tag-editor-warnings').innerHTML = (data.warnings || [])
    .map((w) => `<div class="album-warning">⚠ ${escapeHtml(w)}</div>`)
    .join('');

  const groups = { standard: [], musicbrainz: [], vgmdb: [], custom: [] };
  (data.fields || []).forEach((f) => (groups[f.source] || groups.custom).push(f));
  groups.standard.sort((a, b) => STANDARD_FIELD_ORDER.indexOf(a.name) - STANDARD_FIELD_ORDER.indexOf(b.name));

  const fieldsEl = document.getElementById('tag-editor-fields');
  fieldsEl.innerHTML = TAG_SOURCE_SECTIONS
    .filter((s) => groups[s.source].length > 0)
    .map((s) => {
      const header = s.label ? `<div class="album-section-label" style="margin-top:18px;">${s.label}</div>` : '';
      return header + groups[s.source].map(renderTagFieldRow).join('');
    })
    .join('');
  wireRemoveButtons(fieldsEl);
}

function renderTagFieldRow(f) {
  const label = TAG_FIELD_LABELS[f.name] || f.name;
  const sourceBadge = f.source === 'standard' ? '' : ` ${TAG_SOURCE_BADGES[f.source] || ''}`;
  const removeBtn = f.source === 'standard' ? '' :
    '<button type="button" class="btn btn-danger-ghost btn-sm js-remove-tag" style="margin-top:6px;">Remove</button>';
  return `
    <div class="settings-row" data-field="${escapeHtml(f.name)}">
      <div class="settings-row-label">
        <label>${escapeHtml(label)}${sourceBadge}</label>
      </div>
      <div class="settings-row-input">
        <input type="text" class="js-tag-value" value="${escapeHtml(f.value || '')}">
        <label class="toggle-check" style="margin-top:6px;">
          <input type="checkbox" class="js-tag-lock" ${f.locked ? 'checked' : ''}>
          Lock against enrichment
        </label>
        ${removeBtn}
      </div>
    </div>
  `;
}

function wireRemoveButtons(container) {
  container.querySelectorAll('.js-remove-tag').forEach((btn) => {
    btn.addEventListener('click', () => removeTagRow(btn.closest('.settings-row')));
  });
}

function removeTagRow(row) {
  if (!row) return;
  // Clearing the value (rather than deleting the DOM row) is what makes
  // Save actually delete the tag — see PUT /api/v1/tags/album's "empty
  // value deletes the tag" convention.
  row.querySelector('.js-tag-value').value = '';
  row.querySelector('.js-tag-lock').checked = false;
  row.style.opacity = '0.5';
  row.querySelector('.js-remove-tag')?.remove();
}

function addCustomTagRow() {
  const nameInput = document.getElementById('new-tag-name');
  const valueInput = document.getElementById('new-tag-value');
  const name = nameInput.value.trim();
  const value = valueInput.value.trim();
  if (!name) return;

  const fieldsEl = document.getElementById('tag-editor-fields');
  const exists = [...fieldsEl.querySelectorAll('.settings-row[data-field]')]
    .some((row) => row.dataset.field.toLowerCase() === name.toLowerCase());
  if (exists) {
    document.getElementById('tag-editor-result').innerHTML =
      `<span class="badge badge-yellow">already listed</span> <span>"${escapeHtml(name)}" is already below — edit it there instead.</span>`;
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.innerHTML = renderTagFieldRow({ name, value, locked: false, source: 'custom' }).trim();
  const row = wrapper.firstElementChild;
  fieldsEl.appendChild(row);
  wireRemoveButtons(fieldsEl);

  nameInput.value = '';
  valueInput.value = '';
}

async function saveTags() {
  const folder = currentTagFolder;
  if (!folder) return;

  const fieldsEl = document.getElementById('tag-editor-fields');
  const resultEl = document.getElementById('tag-editor-result');
  const btn = document.getElementById('save-tags-btn');

  const fields = {};
  const locks = [];
  fieldsEl.querySelectorAll('.settings-row[data-field]').forEach((row) => {
    const name = row.dataset.field;
    fields[name] = row.querySelector('.js-tag-value').value.trim();
    locks.push({ field: name, locked: row.querySelector('.js-tag-lock').checked });
  });

  btn.disabled = true;
  btn.textContent = 'Saving…';
  resultEl.innerHTML = '';

  try {
    const res = await fetch(`${window.APP_URL_BASE}/api/v1/tags/album?folder=${encodeURIComponent(folder)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `save failed (HTTP ${res.status})`);

    for (const { field, locked } of locks) {
      const lockRes = await fetch(`${window.APP_URL_BASE}/api/v1/tags/locks?folder=${encodeURIComponent(folder)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ field, locked }),
      });
      if (!lockRes.ok) throw new Error(`could not update lock for "${field}" (HTTP ${lockRes.status})`);
    }

    const trackLocks = [
      { field: 'title', locked: document.getElementById('lock-track-titles').checked },
      { field: 'artist', locked: document.getElementById('lock-track-artist').checked },
    ];
    for (const { field, locked } of trackLocks) {
      const lockRes = await fetch(`${window.APP_URL_BASE}/api/v1/tags/track-locks?folder=${encodeURIComponent(folder)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ field, locked }),
      });
      if (!lockRes.ok) throw new Error(`could not update track lock for "${field}" (HTTP ${lockRes.status})`);
    }

    resultEl.innerHTML = `<span class="badge badge-green">saved</span> <span>Reloading…</span>`;
    window.location.reload();
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
    btn.disabled = false;
    btn.textContent = 'Save Tags';
  }
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
