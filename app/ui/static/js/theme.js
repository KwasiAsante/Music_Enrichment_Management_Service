/**
 * Light/dark theme toggle. The actual class application on load happens
 * synchronously in base.html (before the sidebar markup renders, to
 * avoid a flash of the wrong theme) — this just wires the toggle button
 * for subsequent clicks and keeps its icon/labels in sync.
 *
 * Dark is the default and needs no attribute; light is applied via
 * data-theme="light" on <html> (see base.css's html[data-theme="light"]
 * block for the actual color overrides).
 */

document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('theme-toggle');
  if (!toggle) return;

  syncToggleButton();

  toggle.addEventListener('click', () => {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    if (isLight) {
      document.documentElement.removeAttribute('data-theme');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.setAttribute('data-theme', 'light');
      localStorage.setItem('theme', 'light');
    }
    syncToggleButton();
  });

  function syncToggleButton() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    toggle.querySelector('.theme-toggle-icon').textContent = isLight ? '☀' : '☾';
    toggle.title = isLight ? 'Switch to dark theme' : 'Switch to light theme';
    toggle.setAttribute('aria-label', toggle.title);
  }
});
