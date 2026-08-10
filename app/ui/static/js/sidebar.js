/**
 * Sidebar collapse/expand. The actual class application on load happens
 * synchronously in base.html (before the sidebar markup renders, to avoid
 * a flash of the wrong state) — this just wires up the toggle button for
 * subsequent clicks and keeps its icon/labels in sync.
 */

document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('sidebar-toggle');
  if (!toggle) return;

  syncToggleButton();

  toggle.addEventListener('click', () => {
    const collapsed = document.documentElement.classList.toggle('sidebar-collapsed');
    localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
    syncToggleButton();
  });

  function syncToggleButton() {
    const collapsed = document.documentElement.classList.contains('sidebar-collapsed');
    toggle.querySelector('.sidebar-toggle-icon').textContent = collapsed ? '›' : '‹';
    toggle.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    toggle.setAttribute('aria-label', toggle.title);
    toggle.setAttribute('aria-expanded', String(!collapsed));
  }
});
