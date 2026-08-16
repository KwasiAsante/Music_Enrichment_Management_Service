/**
 * Music Search — MusicBrainz → Lidarr → Prowlarr/Nyaa → qBittorrent.
 *
 * Extracted from the original standalone music-search.html with minimal
 * changes: proxy paths fixed to the real /proxy/{lidarr,prowlarr,qbit}
 * routes, and every credential (Lidarr/Prowlarr API keys, qBittorrent
 * username/password) removed from this file entirely — they're injected
 * server-side per-request now (see app/api/proxy.py). Everything else —
 * MusicBrainz search, result rendering, pagination, the Lidarr add flow,
 * Prowlarr/Nyaa search, indexer filtering, magnet/download actions — is
 * unchanged from the working original.
 */

// ── HELPERS ───────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
// Proxy paths point at the real routes in app/api/proxy.py. Neither helper
// below reads a form field anymore — Lidarr/Prowlarr API keys are injected
// server-side on every proxied request now (see _inject_apikey in
// app/api/proxy.py), so the browser never needs to know them. The
// placeholder strings these return are harmless: the proxy strips whatever
// apikey value shows up in the query string and replaces it with the real
// one regardless of what's here.
const lidarrBase   = () => '/proxy/lidarr';
const lidarrKey    = () => 'server-managed';
const prowlarrBase = () => '/proxy/prowlarr';
const prowlarrKey  = () => 'server-managed';

// MusicBrainz's canonical "Various Artists" artist — every compilation in
// Lidarr shares this one artist record. Its discography is effectively
// unbounded, so a RefreshArtist call against it doesn't reliably get back
// every album we've already added; Lidarr treats whatever's missing from
// that response as removed and deletes it. Concretely: adding release B
// under Various Artists has been observed to silently delete release A
// (added moments earlier) when the metadata refresh drops it. We skip the
// refresh for this one artist and go straight to the direct-add fallback,
// which doesn't touch the rest of the artist's already-tracked albums.
const VARIOUS_ARTISTS_MBID = '89ad4ac3-39f7-470e-963a-56509c546377';

function ts() { return new Date().toLocaleTimeString('en', {hour12:false}); }

function log(logId, msg, type='info') {
  const el = $(logId);
  el.classList.add('visible');
  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = `<span class="lt">${ts()}</span><span class="l${type}">${msg}</span>`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function badge(text, color='blue') {
  return `<span class="badge badge-${color}">${text}</span>`;
}

function fmtSize(bytes) {
  if (!bytes) return '?';
  if (bytes > 1e9) return (bytes/1e9).toFixed(1) + ' GB';
  return Math.round(bytes/1e6) + ' MB';
}

async function fetchCover(mbid) {
  try {
    const r = await fetch(`https://coverartarchive.org/release/${mbid}`, { headers: { Accept: 'application/json' } });
    if (!r.ok) return null;
    const d = await r.json();
    const img = d.images?.find(i => i.front) || d.images?.[0];
    return img?.thumbnails?.small || img?.image || null;
  } catch { return null; }
}

// ── TABS ──────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    $('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── MB SEARCH ─────────────────────────────────────────────────────────────────
let allMbResults = [], activeType = 'all', selectedRg = null, selectedArtistInfo = null;
let mbPage = 0;
const MB_PAGE_SIZE = 8;
let prlPage = 0;
const PRL_PAGE_SIZE = 8;
let allPrlResults = [];

async function mbSearch() {
  const q = $('mb-input').value.trim();
  if (!q) return;
  const btn = $('mb-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Searching…';
  $('mb-results-wrap').style.display = 'none';
  $('type-tabs').style.display = 'none';
  $('sel-panel').classList.remove('visible');
  $('mb-log').classList.remove('visible'); $('mb-log').innerHTML = '';
  selectedRg = null;

  try {
    const url = `https://musicbrainz.org/ws/2/release-group?query=${encodeURIComponent(q)}&limit=25&fmt=json`;
    const r = await fetch(url, { headers: { 'User-Agent': 'LidarrAlbumSearch/1.0', Accept: 'application/json' } });
    const data = await r.json();
    allMbResults = data['release-groups'] || [];
    mbPage = 0;
    $('mb-label').textContent = `${allMbResults.length} results for "${q}"`;
    $('mb-results-wrap').style.display = 'block';
    $('type-tabs').style.display = 'flex';
    activeType = 'all';
    document.querySelectorAll('#type-tabs .type-tab').forEach(t => t.classList.toggle('active', t.dataset.type === 'all'));
    renderMbResults();
  } catch (e) {
    $('mb-results-wrap').style.display = 'block';
    $('mb-results').innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div>${e.message}</div>`;
  }
  btn.disabled = false; btn.innerHTML = 'Search';
}

function renderMbResults() {
  const list = $('mb-results');
  const filtered = activeType === 'all' ? allMbResults
    : allMbResults.filter(g => g.primaryType === activeType || (g.secondaryTypes||[]).includes(activeType));

  if (!filtered.length) {
    list.innerHTML = `<div class="empty"><div class="empty-icon">🔍</div>No results for this filter</div>`;
    $('mb-pagination').style.display = 'none';
    return;
  }

  const totalPages = Math.ceil(filtered.length / MB_PAGE_SIZE);
  if (mbPage >= totalPages) mbPage = totalPages - 1;
  if (mbPage < 0) mbPage = 0;
  const pageItems = filtered.slice(mbPage * MB_PAGE_SIZE, (mbPage + 1) * MB_PAGE_SIZE);
  const pag = $('mb-pagination');
  pag.style.display = totalPages > 1 ? 'flex' : 'none';
  $('mb-page-info').textContent = `Page ${mbPage + 1} of ${totalPages}`;
  $('mb-prev-btn').disabled = mbPage === 0;
  $('mb-next-btn').disabled = mbPage >= totalPages - 1;

  list.innerHTML = '';
  pageItems.forEach(rg => {
    const ac = rg['artist-credit']?.[0];
    const artistName = ac?.artist?.name || ac?.name || 'Unknown Artist';
    const artistMbid = ac?.artist?.id || '';
    const year = (rg['first-release-date'] || '').slice(0, 4);
    const t = rg.primaryType || '';
    const s = (rg.secondaryTypes || []).join(', ');
    const typeLabel = s ? `${t}/${s}` : t;
    const typeColor = {Album:'blue',Single:'purple',EP:'purple',Soundtrack:'green',Compilation:'yellow'}[t] || 'blue';

    const card = document.createElement('div');
    card.className = 'result-card';
    card.innerHTML = `
      <div class="cover" id="cv-${rg.id}">🎵</div>
      <div class="info">
        <div class="info-title">${rg.title}</div>
        <div class="info-meta">
          <span>👤 ${artistName}</span>
          ${year ? `<span>📅 ${year}</span>` : ''}
          ${rg.count ? `<span>📀 ${rg.count}</span>` : ''}
          ${typeLabel ? `<span class="badge badge-${typeColor}">${typeLabel}</span>` : ''}
          <a href="https://musicbrainz.org/release-group/${rg.id}" target="_blank"
            style="color:var(--accent);font-size:11px;text-decoration:none;margin-left:4px"
            onclick="event.stopPropagation()">↗ MB</a>
        </div>
      </div>`;

    card.addEventListener('click', () => selectRg(rg, artistName, artistMbid, card));
    list.appendChild(card);

    fetchCover(rg.id).then(url => {
      if (!url) return;
      const el = document.getElementById(`cv-${rg.id}`);
      if (el) el.innerHTML = `<img src="${url}" alt="cover" loading="lazy">`;
    });
  });
}

function selectRg(rg, artistName, artistMbid, cardEl) {
  document.querySelectorAll('.result-card.selected').forEach(c => c.classList.remove('selected'));
  cardEl.classList.add('selected');
  selectedRg = rg;
  selectedArtistInfo = { name: artistName, mbid: artistMbid };

  $('sel-title').textContent = rg.title;
  $('sel-artist').textContent = artistName;
  const year = (rg['first-release-date'] || '').slice(0, 4);
  const t = rg.primaryType || '';
  const s = (rg.secondaryTypes || []).join(', ');
  const typeLabel = s ? `${t}/${s}` : t;
  $('sel-badges').innerHTML = [
    typeLabel ? badge(typeLabel, 'blue') : '',
    year ? badge(year, 'purple') : '',
    badge(rg.id.slice(0,8) + '…', 'blue')
  ].filter(Boolean).join(' ');

  $('sel-cover').innerHTML = '🎵';
  fetchCover(rg.id).then(url => {
    if (url) $('sel-cover').innerHTML = `<img src="${url}" alt="cover">`;
  });

  $('sel-panel').classList.add('visible');
  $('add-status').textContent = '';
  $('search-prowlarr-btn').style.display = 'none';
  $('sel-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── ADD TO LIDARR ─────────────────────────────────────────────────────────────
async function addToLidarr() {
  if (!selectedRg || !selectedArtistInfo) return;

  const base  = lidarrBase(), key = lidarrKey();
  const qualityId  = parseInt($('quality-profile').value);
  const metadataId = parseInt($('metadata-profile').value);
  const rootFolder = $('root-folder').value;
  const logId = 'mb-log';

  if (!key) { log(logId, 'No Lidarr API key', 'err'); return; }

  const btn = $('add-lidarr-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Adding…';
  $('add-status').textContent = '';

  try {
    // Check if artist exists
    log(logId, `Checking for artist "${selectedArtistInfo.name}"…`);
    const existing = await fetch(`${base}/api/v1/artist?apikey=${key}`).then(r => r.json());
    let artist = existing.find(a => a.foreignArtistId === selectedArtistInfo.mbid);

    if (artist) {
      log(logId, `Artist already in Lidarr (id=${artist.id})`, 'ok');
    } else {
      log(logId, `Looking up artist in Lidarr…`);
      const lookup = await fetch(`${base}/api/v1/artist/lookup?term=lidarr:${selectedArtistInfo.mbid}&apikey=${key}`).then(r => r.json());
      if (!lookup?.length) throw new Error('Artist not found in Lidarr lookup');

      log(logId, `Adding artist "${selectedArtistInfo.name}"…`);
      const addRes = await fetch(`${base}/api/v1/artist?apikey=${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...lookup[0],
          qualityProfileId: qualityId,
          metadataProfileId: metadataId,
          rootFolderPath: rootFolder,
          monitored: false,
          monitorNewItems: 'none',
          addOptions: { monitor: 'none', searchForMissingAlbums: false }
        })
      });
      if (!addRes.ok) throw new Error(`Add artist failed: ${addRes.status}`);
      artist = await addRes.json();
      log(logId, `Artist added (id=${artist.id})`, 'ok');

      // Refresh and scan the newly added artist so metadata loads
      log(logId, `Refreshing artist metadata…`);
      await fetch(`${base}/api/v1/command?apikey=${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'RefreshArtist', artistId: artist.id })
      });

      // Wait for refresh to complete before looking for albums
      log(logId, `Waiting for metadata to load…`);
      await new Promise(r => setTimeout(r, 8000));
    }

    // Find album
    log(logId, `Finding album "${selectedRg.title}"…`);
    await new Promise(r => setTimeout(r, 3000));
    const albums = await fetch(`${base}/api/v1/album?artistId=${artist.id}&apikey=${key}`).then(r => r.json());
    let album = albums.find(a => a.foreignAlbumId === selectedRg.id)
      || albums.find(a => a.title.toLowerCase() === selectedRg.title.toLowerCase());

    if (!album) {
      if (selectedArtistInfo.mbid === VARIOUS_ARTISTS_MBID) {
        // See VARIOUS_ARTISTS_MBID above — refreshing this artist risks
        // deleting other already-tracked VA releases, so skip straight to
        // the direct-add fallback below instead of refreshing first.
        log(logId, `Various Artists — skipping metadata refresh (would risk other VA releases), adding directly…`);
      } else {
        // Force refresh artist metadata and retry once
        log(logId, `Album not found — forcing metadata refresh…`);
        await fetch(`${base}/api/v1/command?apikey=${key}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'RefreshArtist', artistId: artist.id })
        });

        // Wait for refresh to complete
        await new Promise(r => setTimeout(r, 5000));

        // Retry album lookup
        const albums2 = await fetch(`${base}/api/v1/album?artistId=${artist.id}&apikey=${key}`).then(r => r.json());
        album = albums2.find(a => a.foreignAlbumId === selectedRg.id)
          || albums2.find(a => a.title.toLowerCase() === selectedRg.title.toLowerCase());
      }

      if (!album) {
        // Last resort: add album directly via MB release group ID
        log(logId, `Still not found — adding album directly via MB ID…`);
        const addAlbumRes = await fetch(`${base}/api/v1/album?apikey=${key}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            foreignAlbumId: selectedRg.id,
            monitored: true,
            anyReleaseOk: true,
            artist: {
              foreignArtistId: selectedArtistInfo.mbid,
              qualityProfileId: qualityId,
              metadataProfileId: metadataId,
              rootFolderPath: rootFolder
            }
          })
        });
        const addAlbumData = await addAlbumRes.json();
        if (!Array.isArray(addAlbumData) && addAlbumData.id) {
          album = addAlbumData;
          log(logId, `Album added directly (id=${album.id})`, 'ok');
        } else {
          log(logId, `Could not add album — use "Search Nyaa" tab to find manually`, 'warn');
          $('prl-input').value = selectedRg.title;
          $('search-prowlarr-btn').style.display = 'inline-flex';
          btn.disabled = false; btn.innerHTML = '▶ Add to Lidarr';
          return;
        }
      } else {
        log(logId, `Found album after refresh (id=${album.id})`, 'ok');
      }
    }

    log(logId, `Found album (id=${album.id}), monitoring it…`);

    // Fetch full album with releases
    const albumFull = await fetch(`${base}/api/v1/album/${album.id}?apikey=${key}`).then(r => r.json());
    const officialRelease = albumFull.releases?.find(r => r.status === 'Official');
    if (officialRelease) {
      albumFull.releases = albumFull.releases.map(r => ({ ...r, monitored: r.id === officialRelease.id }));
    }

    // Monitor official release, fall back to any release
    const releaseToMonitor = albumFull.releases?.find(r => r.status === 'Official')
      || albumFull.releases?.[0];
    if (releaseToMonitor) {
      albumFull.releases = albumFull.releases.map(r => ({
        ...r, monitored: r.id === releaseToMonitor.id
      }));
    }

    const monBody = { ...albumFull, monitored: true, anyReleaseOk: true };
    const monRes = await fetch(`${base}/api/v1/album/${album.id}?apikey=${key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(monBody)
    });
    if (!monRes.ok) {
      const errTxt = await monRes.text();
      // If monitor fails, still try to trigger search
      log(logId, `Monitor returned ${monRes.status} — attempting search anyway`, 'warn');
    } else {
      log(logId, `Album monitored`, 'ok');
    }

    // Store album title for Prowlarr search
    window._lastAlbumTitle = selectedRg.title;
    window._lastAlbumId    = album.id;

    // Show prowlarr search button
    $('search-prowlarr-btn').style.display = 'inline-flex';
    $('add-status').textContent = '✓ Added to Lidarr';
    $('add-status').style.color = 'var(--success)';
    log(logId, `✓ Done! Now click "Search Nyaa" to find and download.`, 'ok');

  } catch (e) {
    log(logId, `Error: ${e.message}`, 'err');
    $('add-status').textContent = '✗ Failed';
    $('add-status').style.color = 'var(--danger)';
  }

  btn.disabled = false; btn.innerHTML = '▶ Add to Lidarr';
}

// ── PROWLARR SEARCH ───────────────────────────────────────────────────────────
async function prowlarrSearch() {
  const q = $('prl-input').value.trim();
  if (!q) return;

  const base = prowlarrBase(), key = prowlarrKey();
  const btn = $('prl-btn');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Searching indexers…';
  $('prl-results-wrap').style.display = 'none';
  $('prl-log').classList.remove('visible'); $('prl-log').innerHTML = '';

  try {
    log('prl-log', `Searching Prowlarr: "${q}"…`);
    const idxParam = getIndexerParams();
    const url = `${prowlarrBase()}/api/v1/search?query=${encodeURIComponent(q)}&type=search${idxParam ? '&' + idxParam : ''}&apikey=${key}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Prowlarr ${r.status}`);
    const results = await r.json();

    // Sort by seeders desc
    results.sort((a, b) => (b.seeders||0) - (a.seeders||0));
    allPrlResults = results;
    prlPage = 0;

    log('prl-log', `Found ${results.length} results across indexers`, 'ok');
    $('prl-label').textContent = `${results.length} results for "${q}"`;
    $('prl-results-wrap').style.display = 'block';
    renderProwlarrResults();

  } catch (e) {
    log('prl-log', `Error: ${e.message}`, 'err');
    $('prl-results-wrap').style.display = 'block';
    $('prl-results').innerHTML = `<div class="empty"><div class="empty-icon">⚠️</div>${e.message}</div>`;
  }

  btn.disabled = false; btn.innerHTML = 'Search Nyaa';
}

// Store results globally for action buttons
window._prlResultsMap = {};

function renderProwlarrResults() {
  const results = allPrlResults;
  const list = $('prl-results');
  if (!results.length) {
    list.innerHTML = `<div class="empty"><div class="empty-icon">🔍</div>No results found</div>`;
    $('prl-pagination').style.display = 'none';
    return;
  }

  const totalPages = Math.ceil(results.length / PRL_PAGE_SIZE);
  if (prlPage >= totalPages) prlPage = totalPages - 1;
  if (prlPage < 0) prlPage = 0;
  const pageItems = results.slice(prlPage * PRL_PAGE_SIZE, (prlPage + 1) * PRL_PAGE_SIZE);
  const pag = $('prl-pagination');
  pag.style.display = totalPages > 1 ? 'flex' : 'none';
  $('prl-page-info').textContent = `Page ${prlPage + 1} of ${totalPages}`;
  $('prl-prev-btn').disabled = prlPage === 0;
  $('prl-next-btn').disabled = prlPage >= totalPages - 1;

  // Re-key results so indices are stable across pages
  list.innerHTML = '';
  pageItems.forEach((r, i) => {
    const globalIdx = prlPage * PRL_PAGE_SIZE + i;
    window._prlResultsMap['prl_' + globalIdx] = r;
    const seeders = r.seeders || 0;
    const seederClass = seeders > 10 ? 'seeder-good' : seeders > 2 ? 'seeder-ok' : 'seeder-bad';
    const key = 'prl_' + globalIdx;

    const hasMagnet = !!r.magnetUrl;
    const hasDownload = !!r.downloadUrl;

    const card = document.createElement('div');
    card.className = 'prowlarr-result';
    card.style.flexDirection = 'column';
    card.style.alignItems = 'flex-start';
    card.style.gap = '10px';
    card.innerHTML = `
      <div class="p-info" style="width:100%">
        <div class="p-title">${r.title}</div>
        <div class="p-meta">
          <span class="${seederClass}">▲ ${seeders} seeders</span>
          <span>📦 ${fmtSize(r.size)}</span>
          <span>📡 ${r.indexer}</span>
          ${r.categories?.map(c => `<span class="badge badge-blue">${c.name}</span>`).join('') || ''}
          ${r.infoUrl ? `<a href="${r.infoUrl}" target="_blank"
            style="color:var(--accent);font-size:11px;text-decoration:none;margin-left:4px"
            onclick="event.stopPropagation()">↗ Source</a>` : ''}
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-success btn-sm" onclick="prowlarrDownload('${key}')">
          ⬇ Send to qBittorrent
        </button>
        ${hasMagnet ? `<button class="btn btn-ghost btn-sm" onclick="copyMagnet('${key}')">📋 Copy Magnet</button>` : ''}
        ${hasMagnet ? `<button class="btn btn-ghost btn-sm" onclick="openMagnet('${key}')">🔗 Open Magnet</button>` : ''}
        ${hasDownload && !hasMagnet ? `<button class="btn btn-ghost btn-sm" onclick="openDownload('${key}')">🔗 Open URL</button>` : ''}
      </div>`;
    list.appendChild(card);
  });
}

// ── DOWNLOAD ACTIONS ──────────────────────────────────────────────────────────

// Send to qBittorrent via Prowlarr's download endpoint
async function prowlarrDownload(key) {
  const r = window._prlResultsMap[key];
  if (!r) return;

  // qBittorrent URL and login are both handled server-side now (see
  // app/api/proxy.py) — the browser only ever supplies the save path,
  // which is a deployment preference, not a credential.
  const proxyQbit = '/proxy/qbit';
  const savepath = $('qbit-savepath').value;

  log('prl-log', `Sending "${r.title}" to qBittorrent…`);

  try {
    // Login to qBittorrent via proxy — the proxy ignores whatever body we
    // send here and always logs in with the server-configured account (or
    // stubs success when a server-side API key is configured instead).
    const loginRes = await fetch(`${proxyQbit}/api/v2/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'username=&password='
    });
    const loginTxt = await loginRes.text();
    // qBittorrent 5.2+ returns 204 with an empty body on success; older versions
    // return 200 with body "Ok.". Wrong credentials still return "Fails." (legacy)
    // or a non-2xx status (5.2+).
    const loginOk = loginRes.ok && (loginRes.status === 204 || loginTxt === 'Ok.');
    if (!loginOk) throw new Error(`qBittorrent login failed: ${loginTxt || loginRes.status}`);

    // Use guid as magnet if it starts with magnet:, otherwise use downloadUrl
    const magnet = r.guid?.startsWith('magnet:') ? r.guid : (r.downloadUrl || r.magnetUrl);
    if (!magnet) throw new Error('No magnet or download URL available');

    // Add to qBittorrent via proxy
    const addRes = await fetch(`${proxyQbit}/api/v2/torrents/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `urls=${encodeURIComponent(magnet)}&savepath=${encodeURIComponent(savepath)}&category=lidarr`
    });
    const addTxt = await addRes.text();
    const addContentType = addRes.headers.get('content-type') || '';
    if (addContentType.includes('application/json')) {
      // qBittorrent 5.2+ returns JSON with success/pending/failure counts.
      const data = JSON.parse(addTxt);
      const allFailed = data.failure_count > 0 && !data.success_count && !data.pending_count;
      if (addRes.status === 409 || allFailed) {
        throw new Error('Torrent already exists in qBittorrent, or save path is invalid');
      }
      if (!addRes.ok && addRes.status !== 202) {
        throw new Error(`Add torrent failed: ${addTxt}`);
      }
    } else {
      if (addTxt === 'Fails.') throw new Error('Torrent already exists in qBittorrent, or save path is invalid');
      if (addTxt !== 'Ok.') throw new Error(`Add torrent failed: ${addTxt}`);
    }

    log('prl-log', '✓ Added to qBittorrent!', 'ok');
    log('prl-log', `Category: lidarr (Lidarr will pick it up automatically)`, 'ok');
    log('prl-log', `Save path: ${savepath}`, 'ok');
  } catch (e) {
    log('prl-log', `Error: ${e.message}`, 'err');
  }
}

function copyMagnet(key) {
  const r = window._prlResultsMap[key];
  if (!r?.magnetUrl) return;
  navigator.clipboard.writeText(r.magnetUrl)
    .then(() => log('prl-log', `✓ Magnet link copied to clipboard`, 'ok'))
    .catch(() => {
      // Fallback for non-https
      const ta = document.createElement('textarea');
      ta.value = r.magnetUrl;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      log('prl-log', `✓ Magnet link copied`, 'ok');
    });
}

function openMagnet(key) {
  const r = window._prlResultsMap[key];
  if (!r?.magnetUrl) return;
  window.open(r.magnetUrl, '_blank');
  log('prl-log', `Opened magnet link — your torrent client should handle it`, 'info');
}

function openDownload(key) {
  const r = window._prlResultsMap[key];
  if (!r?.downloadUrl) return;
  window.open(r.downloadUrl, '_blank');
  log('prl-log', `Opened download URL`, 'info');
}

// ── INDEXER FILTER ───────────────────────────────────────────────────────────
// IDs of default selected indexers (Nyaa Music Lossless + Lossy)
const DEFAULT_INDEXER_NAMES = ['Nyaa.si - Music Lossless', 'Nyaa.si - Music Lossy'];
let selectedIndexerIds = new Set(); // empty = search all

async function loadIndexers() {
  try {
    const res = await fetch(
      `${prowlarrBase()}/api/v1/indexer?apikey=${prowlarrKey()}`
    );
    const indexers = await res.json();
    const LIDARR_CATS = new Set([3000, 3010, 3030, 3040, 3050, 3060]);

    // Only show indexers that support audio categories
    const enabled = indexers.filter(i => {
      if (!i.enable) return false;
      const allCatIds = new Set();
      for (const cat of i.capabilities?.categories || []) {
        allCatIds.add(cat.id);
        for (const sub of cat.subCategories || []) allCatIds.add(sub.id);
      }
      return [...allCatIds].some(id => LIDARR_CATS.has(id));
    });

    const container = document.getElementById('indexer-filter');
    container.innerHTML = '';

    enabled.forEach(idx => {
      const isDefault = DEFAULT_INDEXER_NAMES.includes(idx.name);
      if (isDefault) selectedIndexerIds.add(idx.id);

      const btn = document.createElement('button');
      btn.className = 'type-tab' + (isDefault ? ' active' : '');
      btn.dataset.indexerId = idx.id;
      btn.textContent = idx.name;
      btn.title = isDefault ? 'Selected by default' : 'Click to add';
      btn.addEventListener('click', () => {
        if (selectedIndexerIds.has(idx.id)) {
          selectedIndexerIds.delete(idx.id);
          btn.classList.remove('active');
        } else {
          selectedIndexerIds.add(idx.id);
          btn.classList.add('active');
        }
        // Update hint
        updateIndexerHint();
      });
      container.appendChild(btn);
    });

    updateIndexerHint();
  } catch(e) {
    console.error('Failed to load indexers:', e);
  }
}

function updateIndexerHint() {
  const hint = document.getElementById('indexer-hint');
  if (!hint) return;
  if (selectedIndexerIds.size === 0) {
    hint.textContent = 'Searching all indexers';
  } else {
    hint.textContent = `Searching ${selectedIndexerIds.size} indexer${selectedIndexerIds.size > 1 ? 's' : ''}`;
  }
}

function getIndexerParams() {
  if (selectedIndexerIds.size === 0) return '';
  return [...selectedIndexerIds].map(id => `indexerIds=${id}`).join('&');
}

// ── EVENT LISTENERS ───────────────────────────────────────────────────────────
$('mb-btn').addEventListener('click', mbSearch);
$('mb-input').addEventListener('keydown', e => { if (e.key === 'Enter') mbSearch(); });
$('add-lidarr-btn').addEventListener('click', addToLidarr);
$('prl-btn').addEventListener('click', prowlarrSearch);
$('prl-input').addEventListener('keydown', e => { if (e.key === 'Enter') prowlarrSearch(); });

$('search-prowlarr-btn').addEventListener('click', () => {
  $('prl-input').value = window._lastAlbumTitle || selectedRg?.title || '';
  document.querySelector('[data-tab="prowlarr"]').click();
  prowlarrSearch();
});

document.querySelectorAll('#type-tabs .type-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    activeType = tab.dataset.type;
    mbPage = 0;
    document.querySelectorAll('#type-tabs .type-tab').forEach(t => t.classList.toggle('active', t === tab));
    renderMbResults();
  });
});

$('mb-prev-btn').addEventListener('click', () => {
  mbPage--;
  renderMbResults();
  $('mb-results-wrap').scrollIntoView({ behavior: 'smooth' });
});
$('mb-next-btn').addEventListener('click', () => {
  mbPage++;
  renderMbResults();
  $('mb-results-wrap').scrollIntoView({ behavior: 'smooth' });
});

$('prl-prev-btn').addEventListener('click', () => {
  prlPage--;
  renderProwlarrResults();
  $('prl-results-wrap').scrollIntoView({ behavior: 'smooth' });
});
$('prl-next-btn').addEventListener('click', () => {
  prlPage++;
  renderProwlarrResults();
  $('prl-results-wrap').scrollIntoView({ behavior: 'smooth' });
});

// Load Lidarr root folders on startup
(async () => {
  try {
    const r = await fetch(`${lidarrBase()}/api/v1/rootfolder?apikey=${lidarrKey()}`);
    const folders = await r.json();
    const sel = $('root-folder');
    sel.innerHTML = folders.map(f => `<option value="${f.path}">${f.path}</option>`).join('');
  } catch(e) {}
})();

// Wire up reload button and auto-load indexers
document.getElementById('reload-indexers-btn').addEventListener('click', loadIndexers);
loadIndexers();
