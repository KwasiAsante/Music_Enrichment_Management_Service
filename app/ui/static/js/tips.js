/**
 * Dismissible tip banners (see templates/_tip_banner.html).
 *
 * Loaded unconditionally from base.html — every page may or may not have
 * a `.tip-banner`, so this just no-ops on pages that don't. Dismissal is
 * per-page (keyed by the banner's `data-tip-key`, which each page sets to
 * something unique) and persisted in localStorage under `tip_dismissed:*`
 * so it stays hidden across visits without a server round trip.
 */

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tip-banner[data-tip-key]').forEach((banner) => {
    const key = `tip_dismissed:${banner.dataset.tipKey}`;

    if (localStorage.getItem(key) === '1') {
      banner.remove();
      return;
    }

    banner.querySelector('.tip-banner-dismiss')?.addEventListener('click', () => {
      localStorage.setItem(key, '1');
      banner.remove();
    });
  });
});
