/**
 * URL base support (see app/config.py's `url_base` setting — the same
 * idea as Sonarr/Radarr/Lidarr's "URL Base": the whole app can be served
 * under a path prefix, e.g. /music-helper/*, for reverse-proxy subpath
 * deployment with no path-rewriting needed).
 *
 * Loaded first, before every other script on every page (see base.html)
 * — everything below depends on window.APP_URL_BASE being set before
 * any other script runs.
 *
 * `fetch()` is patched here so the ~20+ `fetch('/api/v1/...')` calls
 * scattered across every page's JS don't each need to remember to
 * prepend the prefix by hand — one bug-prone thing to get right in one
 * place, instead of N places. Anything NOT going through fetch()
 * (hardcoded <a href> in templates, dynamically-built href/src strings
 * in JS for things like the Library page's album links or cover art
 * <img> src) still needs — and has — `${window.APP_URL_BASE}` prepended
 * explicitly at each call site, since there's no equivalent hook for
 * "every string that looks like a URL."
 *
 * A no-op (not even a fetch wrapper installed) when URL_BASE isn't set,
 * which is the default — zero behavioural change for anyone not using it.
 */

(function () {
  const BASE = document.documentElement.dataset.urlBase || '';
  window.APP_URL_BASE = BASE;
  if (!BASE) return;

  const nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    if (typeof input === 'string' && input.startsWith('/') && !input.startsWith(BASE + '/')) {
      input = BASE + input;
    }
    return nativeFetch(input, init);
  };
})();
