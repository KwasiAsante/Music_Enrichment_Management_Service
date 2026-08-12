/**
 * Album detail page. Almost everything is server-rendered (see
 * templates/album_detail.html) — the only interactivity here is:
 *
 *   - Flip the cover between front/back (only present when both exist).
 *   - Fall back to a plain placeholder if the art request 404s after the
 *     page already rendered (e.g. the file was removed moments ago) —
 *     same "missing thumbnail is expected, not an error" treatment the
 *     Library grid view already gives cover art.
 */

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
  if (!flipBtn || !img) return;

  flipBtn.addEventListener('click', () => {
    const showing = flipBtn.dataset.showing === 'front' ? 'back' : 'front';
    flipBtn.dataset.showing = showing;
    img.src = `${window.APP_URL_BASE}/api/v1/library/art?folder=${encodeURIComponent(flipBtn.dataset.folder)}&side=${showing}`;
    img.dataset.side = showing;
    flipBtn.textContent = showing === 'front' ? '⟳ Show back cover' : '⟳ Show front cover';
  });
});
