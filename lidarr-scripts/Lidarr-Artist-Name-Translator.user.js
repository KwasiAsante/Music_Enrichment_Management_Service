// ==UserScript==
// @name         Lidarr Artist Name Translator
// @namespace    http://tampermonkey.net/
// @version      3.5.0
// @description  Translates non-Latin artist names (Japanese, Korean, Chinese) and variant Latin names (Finnish/Nordic) to English/romaji. Search bar persists on all pages. Navigate to artist page on Enter.
// @author       kwasi
// @match        http://192.168.2.130:8686/lidarr*
// @match        https://kaneservarr.duckdns.org/lidarr*
// @exclude      http://192.168.2.130:8686/lidarr/login*
// @exclude      https://kaneservarr.duckdns.org/lidarr/login*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=2.130
// @grant        none
// @updateURL    https://raw.githubusercontent.com/KwasiAsante/Music_Enrichment_Management_Service/main/lidarr-scripts/Lidarr-Artist-Name-Translator.user.js
// @downloadURL  https://raw.githubusercontent.com/KwasiAsante/Music_Enrichment_Management_Service/main/lidarr-scripts/Lidarr-Artist-Name-Translator.user.js
// ==/UserScript==

(function () {
  'use strict';

  if (/\/login(\/|$|\?)/.test(location.pathname)) return;

  const CACHE_KEY = 'ljt_artist_map_v3';
  const CACHE_TTL = 24 * 60 * 60 * 1000;

  let artistMap = {};
  let initialized = false;

  // ── CACHE ────────────────────────────────────────────────────────────────
  function loadCache(currentCount) {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const { timestamp, data, count } = JSON.parse(raw);
      if (Date.now() - timestamp > CACHE_TTL) return null;
      if (currentCount !== undefined && count !== currentCount) {
        console.log(`[LJT] Count changed (${count}→${currentCount}), refreshing`);
        return null;
      }
      return data;
    } catch { return null; }
  }

  function saveCache(data, count) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({ timestamp: Date.now(), data, count }));
    } catch { }
  }

  // ── API ───────────────────────────────────────────────────────────────────
  async function fetchArtistMap() {
    const apiKey = window.Lidarr?.apiKey;
    const base = window.Lidarr?.urlBase || '';
    const url = `${base}/api/v1/artist${apiKey ? '?apikey=' + apiKey : ''}`;

    const res = await fetch(url);
    const data = await res.json();
    const currentCount = data.length;

    const cached = loadCache(currentCount);
    if (cached) {
      // Still build _ljtAllArtists for navigation even from cache
      window._ljtAllArtists = data.map(a => ({
        name: a.artistName,
        folder: (a.path || '').replace(/\/$/, '').split('/').pop(),
        mbid: a.foreignArtistId,
        url: `${base}/artist/${a.foreignArtistId}`
      }));
      console.log('[LJT] Loaded from cache:', Object.keys(cached).length);
      return cached;
    }

    const map = {};
    window._ljtAllArtists = data.map(a => {
      const kanji = a.artistName;
      const mbid = a.foreignArtistId;
      const folder = (a.path || '').replace(/\/$/, '').split('/').pop();
      if (/[À-ɏᄀ-ᇿ　-鿿㄰-㆏㐀-䶿가-힯豈-﫿＀-￯]/.test(kanji) && folder && folder !== kanji) {
        map[mbid] = { kanji, english: folder };
      }
      return { name: kanji, folder, mbid, url: `${base}/artist/${mbid}` };
    });

    console.log('[LJT] Built map:', Object.keys(map).length, 'non-Latin/variant artists');
    saveCache(map, currentCount);
    return map;
  }

  // ── STYLES ───────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('ljt-styles')) return;
    const s = document.createElement('style');
    s.id = 'ljt-styles';
    s.textContent = `
      #ljt-search-wrapper {
        position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
        z-index: 99999; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        min-width: 360px;
      }
      #ljt-search-inner {
        display: flex; align-items: center; background: #1a1d23;
        border: 1px solid #35383f; border-radius: 8px; padding: 7px 14px;
        gap: 8px; box-shadow: 0 4px 24px rgba(0,0,0,0.7); position: relative;
      }
      #ljt-search-input {
        background: transparent; border: none; outline: none;
        color: #f1f3f5; font-size: 14px; width: 280px; font-family: inherit;
      }
      #ljt-search-input::placeholder { color: #4b5563; }
      #ljt-search-clear, #ljt-refresh-btn {
        background: none; border: none; color: #4b5563; cursor: pointer;
        font-size: 14px; padding: 0; line-height: 1;
      }
      #ljt-search-clear { font-size: 15px; display: none; }
      #ljt-search-clear:hover, #ljt-refresh-btn:hover { color: #e5e7eb; }
      #ljt-refresh-btn.spinning { color: #60a5fa; animation: ljt-spin 1s linear infinite; }
      @keyframes ljt-spin { to { transform: rotate(360deg); } }

      #ljt-dropdown {
        position: absolute; top: calc(100% + 4px); left: 0; right: 0;
        background: #1a1d23; border: 1px solid #35383f; border-radius: 8px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.8); max-height: 320px;
        overflow-y: auto; display: none; z-index: 100000;
      }
      #ljt-dropdown.visible { display: block; }
      .ljt-suggestion {
        display: flex; align-items: center; padding: 9px 14px; cursor: pointer;
        gap: 10px; border-bottom: 1px solid #23272f; transition: background 0.1s;
      }
      .ljt-suggestion:last-child { border-bottom: none; }
      .ljt-suggestion:hover, .ljt-suggestion.ljt-sel { background: #23272f; }
      .ljt-s-primary { font-size: 13px; color: #e5e7eb; font-weight: 600; }
      .ljt-s-secondary { font-size: 11px; color: #60a5fa; font-style: italic; margin-top: 1px; }
      .ljt-no-results { padding: 12px 14px; color: #4b5563; font-size: 12px; text-align: center; }

      .ljt-english {
        display: block !important; font-size: 11px !important; color: #60a5fa !important;
        margin-top: 2px !important; font-style: italic !important; overflow: hidden !important;
        text-overflow: ellipsis !important; white-space: nowrap !important;
        max-width: 100% !important; opacity: 0.9 !important;
      }
    `;
    document.head.appendChild(s);
  }

  // ── SEARCH BAR ───────────────────────────────────────────────────────────
  function injectSearchBar() {
    if (document.getElementById('ljt-search-wrapper')) return;

    const wrapper = document.createElement('div');
    wrapper.id = 'ljt-search-wrapper';
    wrapper.innerHTML = `
      <div id="ljt-search-inner">
        <span style="opacity:0.5;font-size:14px">🔍</span>
        <input id="ljt-search-input" type="text"
          placeholder="Search artists…" autocomplete="off" spellcheck="false"/>
        <button id="ljt-search-clear" title="Clear">✕</button>
        <button id="ljt-refresh-btn" title="Refresh">↻</button>
        <div id="ljt-dropdown"></div>
      </div>`;
    document.body.appendChild(wrapper);

    const input = document.getElementById('ljt-search-input');
    const clear = document.getElementById('ljt-search-clear');
    const refresh = document.getElementById('ljt-refresh-btn');
    const dropdown = document.getElementById('ljt-dropdown');
    let selIdx = -1, searchTimer = null;

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      clear.style.display = q ? 'block' : 'none';
      selIdx = -1;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => updateDropdown(q), 100);
    });

    input.addEventListener('keydown', e => {
      const items = [...dropdown.querySelectorAll('.ljt-suggestion')];
      if (e.key === 'ArrowDown') {
        e.preventDefault(); selIdx = Math.min(selIdx + 1, items.length - 1);
        items.forEach((el, i) => el.classList.toggle('ljt-sel', i === selIdx));
        items[selIdx]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); selIdx = Math.max(selIdx - 1, 0);
        items.forEach((el, i) => el.classList.toggle('ljt-sel', i === selIdx));
        items[selIdx]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const target = items[selIdx] || items[0];
        if (target) target.click();
      } else if (e.key === 'Escape') {
        hideDropdown(); input.blur();
      }
    });

    clear.addEventListener('click', () => {
      input.value = ''; clear.style.display = 'none';
      hideDropdown();
    });

    refresh.addEventListener('click', async () => {
      refresh.classList.add('spinning');
      localStorage.removeItem(CACHE_KEY);
      document.querySelectorAll('[data-ljt-done]').forEach(el => {
        delete el.dataset.ljtDone;
        el.querySelectorAll('.ljt-english').forEach(l => l.remove());
      });
      try {
        artistMap = await fetchArtistMap();
        applyTranslations();
      } catch (e) { console.error('[LJT] Refresh failed:', e); }
      refresh.classList.remove('spinning');
    });

    document.addEventListener('click', e => {
      if (!wrapper.contains(e.target)) hideDropdown();
    });

    document.addEventListener('keydown', e => {
      if (e.ctrlKey && e.shiftKey && e.key === 'F') {
        e.preventDefault(); input.focus(); input.select();
      }
    });
  }

  function hideDropdown() {
    const dd = document.getElementById('ljt-dropdown');
    if (dd) { dd.innerHTML = ''; dd.classList.remove('visible'); }
  }

  function updateDropdown(q) {
    const dd = document.getElementById('ljt-dropdown');
    if (!dd) return;
    if (!q) { hideDropdown(); return; }

    const base = window.Lidarr?.urlBase || '';
    const all = window._ljtAllArtists || [];
    const matches = all.filter(a =>
      a.name.toLowerCase().includes(q) || a.folder.toLowerCase().includes(q)
    ).slice(0, 8);

    dd.innerHTML = '';
    if (!matches.length) {
      dd.innerHTML = '<div class="ljt-no-results">No artists found</div>';
      dd.classList.add('visible');
      return;
    }

    matches.forEach(a => {
      const isJapanese = /[À-ɏᄀ-ᇿ　-鿿㄰-㆏㐀-䶿가-힯豈-﫿＀-￯]/.test(a.name);
      const primary = isJapanese && a.folder && a.folder !== a.name ? a.folder : a.name;
      const secondary = isJapanese && a.folder && a.folder !== a.name ? a.name : '';

      const item = document.createElement('div');
      item.className = 'ljt-suggestion';
      item.innerHTML = `
        <span style="font-size:18px">🎵</span>
        <div style="min-width:0;flex:1">
          <div class="ljt-s-primary">${primary}</div>
          ${secondary ? `<div class="ljt-s-secondary">${secondary}</div>` : ''}
        </div>`;
      item.addEventListener('click', () => {
        window.location.href = `${base}/artist/${a.mbid}`;
      });
      dd.appendChild(item);
    });

    dd.classList.add('visible');
  }

  // ── TRANSLATIONS (library page only) ─────────────────────────────────────
  const SKIP = new Set(['Unmonitored', 'Monitored', 'Any', 'Ended', 'Continuing', 'None', 'Standard', '']);
  let translating = false;

  function applyTranslations() {
    if (translating) return;
    translating = true;
    document.querySelectorAll('[class*="ArtistIndexPoster-content"]').forEach(card => {
      if (card.dataset.ljtDone) return;
      const link = card.querySelector('a[href*="/artist/"]');
      if (!link) return;
      const mbid = link.href.match(/\/artist\/([a-f0-9-]{36})/)?.[1];
      if (!mbid) return;
      let titleEl = null, artistName = null;
      for (const el of card.querySelectorAll('[class*="ArtistIndexPoster-title"]')) {
        const text = el.childNodes[0]?.textContent?.trim() || el.textContent.trim();
        if (text && !SKIP.has(text)) { titleEl = el; artistName = text; break; }
      }
      if (!titleEl) return;
      const entry = artistMap[mbid];
      card.dataset.ljtDone = '1';
      if (entry?.english && entry.english !== artistName && !titleEl.querySelector('.ljt-english')) {
        const label = document.createElement('span');
        label.className = 'ljt-english';
        label.textContent = entry.english;
        label.title = entry.english;
        titleEl.appendChild(label);
      }
    });
    translating = false;
  }

  // ── INIT — runs on every page ─────────────────────────────────────────────
  async function init() {
    injectStyles();
    injectSearchBar();

    // Fetch artist data if we don't have it yet (needed for dropdown on all pages)
    if (!window._ljtAllArtists || !window.Lidarr?.apiKey) {
      // Wait for Lidarr to expose apiKey
      await new Promise(resolve => {
        if (window.Lidarr?.apiKey) return resolve();
        const t = setInterval(() => {
          if (window.Lidarr?.apiKey) { clearInterval(t); resolve(); }
        }, 300);
        setTimeout(() => { clearInterval(t); resolve(); }, 10000);
      });
    }

    try {
      artistMap = await fetchArtistMap();
    } catch (e) {
      console.error('[LJT] fetchArtistMap failed:', e);
    }

    // Apply translations on library page
    applyTranslations();
    let obsTimer = null;
    new MutationObserver(() => {
      if (translating) return;
      clearTimeout(obsTimer);
      obsTimer = setTimeout(applyTranslations, 150);
    }).observe(document.body, { childList: true, subtree: true });

    console.log('[LJT] Ready on', location.pathname);
  }

  // ── BOOT ─────────────────────────────────────────────────────────────────
  // Run immediately — search bar shows on all pages without waiting for artist cards
  injectStyles();
  injectSearchBar();

  // Then init fully once page is ready
  const bootTimer = setInterval(() => {
    if (window.Lidarr?.apiKey) {
      clearInterval(bootTimer);
      init();
    }
  }, 300);

  // SPA navigation — re-init on route change but keep search bar
  let lastUrl = location.href;
  new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      // Remove done markers so translations re-apply on new page
      document.querySelectorAll('[data-ljt-done]').forEach(el => delete el.dataset.ljtDone);
      // Re-apply translations after a short delay (new page content loads)
      setTimeout(applyTranslations, 800);
    }
  }).observe(document.body, { childList: true, subtree: true });

})();
