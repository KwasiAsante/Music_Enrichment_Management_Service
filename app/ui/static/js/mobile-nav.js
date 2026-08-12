/**
 * Mobile off-canvas sidebar. Below the mobile breakpoint (see base.css),
 * .sidebar becomes a fixed-position drawer hidden off-screen by default;
 * this wires the hamburger button, backdrop tap, and Escape key to toggle
 * it via a single `html.mobile-nav-open` class.
 *
 * Deliberately NOT persisted to localStorage (unlike the desktop
 * collapse/theme preferences) — every page load is a fresh navigation in
 * this server-rendered app, and a drawer that remembers "open" across
 * page loads would just mean it's permanently in the way rather than
 * genuinely persisting a preference someone chose.
 */

document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('mobile-nav-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!toggle || !backdrop) return;

  toggle.addEventListener('click', () => {
    const isOpen = document.documentElement.classList.toggle('mobile-nav-open');
    toggle.setAttribute('aria-expanded', String(isOpen));
    toggle.setAttribute('aria-label', isOpen ? 'Close menu' : 'Open menu');
  });

  backdrop.addEventListener('click', closeMobileNav);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMobileNav();
  });

  function closeMobileNav() {
    document.documentElement.classList.remove('mobile-nav-open');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Open menu');
  }
});
