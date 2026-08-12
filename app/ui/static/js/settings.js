/**
 * Settings page.
 *
 * Save: collects every input's current value (skipping blank password
 * fields, which mean "leave unchanged" — see app/api/settings.py) into
 * one PUT /api/v1/settings, then reloads the page so the server-rendered
 * state (overridden/pending pills, the restart banner) reflects reality
 * rather than trying to hand-sync client state after the fact.
 *
 * Restart: confirms, POSTs /api/v1/settings/restart (which schedules a
 * graceful SIGTERM ~0.5s out — see that endpoint for why), then polls
 * GET /health every second until it responds again (Docker's restart
 * policy, or uvicorn --reload's supervisor in local dev, brings the
 * process back), and reloads once it does. If it hasn't come back within
 * ~30s, gives up with a message rather than polling forever — most
 * likely cause at that point is a config value that broke startup
 * entirely (e.g. an invalid cron expression that slipped past
 * validation) and needs a look at the container logs.
 */

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('settings-form')?.addEventListener('submit', handleSave);
  document.getElementById('restart-btn')?.addEventListener('click', handleRestart);
});

async function handleSave(e) {
  e.preventDefault();
  const saveBtn = document.getElementById('save-btn');
  const resultEl = document.getElementById('save-result');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving…';
  resultEl.innerHTML = '';

  const values = {};
  document.querySelectorAll('[data-key]').forEach((el) => {
    const key = el.dataset.key;
    const type = el.dataset.type;
    if (type === 'bool') {
      values[key] = el.checked;
    } else if (type === 'password') {
      if (el.value !== '') values[key] = el.value; // blank = leave unchanged, omit entirely
    } else {
      values[key] = el.value;
    }
  });

  try {
    const res = await fetch('/api/v1/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `save failed (HTTP ${res.status})`);

    if (data.saved.length === 0) {
      resultEl.innerHTML = `<span class="badge badge-yellow">no changes</span>`;
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save Changes';
      return;
    }

    resultEl.innerHTML = `<span class="badge badge-green">saved</span> <span>${data.saved.length} setting${data.saved.length === 1 ? '' : 's'} changed — reloading…</span>`;
    setTimeout(() => window.location.reload(), 700);
  } catch (err) {
    resultEl.innerHTML = `<span class="badge badge-red">error</span> <span>${escapeHtml(err.message)}</span>`;
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save Changes';
  }
}

async function handleRestart() {
  if (!confirm('This restarts the app. It should come back up automatically within a few seconds. Continue?')) {
    return;
  }

  const btn = document.getElementById('restart-btn');
  const banner = document.getElementById('restart-banner');
  btn.disabled = true;
  btn.textContent = 'Restarting…';

  try {
    const res = await fetch('/api/v1/settings/restart', { method: 'POST' });
    if (!res.ok) throw new Error(`restart request failed (HTTP ${res.status})`);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Restart Now';
    banner.querySelector('.restart-banner-text').textContent = `Restart request failed: ${err.message}`;
    return;
  }

  banner.querySelector('.restart-banner-text').textContent = 'Restarting — waiting for the app to come back…';
  pollHealth(0);
}

async function pollHealth(attempt) {
  const MAX_ATTEMPTS = 30;
  if (attempt >= MAX_ATTEMPTS) {
    document.getElementById('restart-banner').querySelector('.restart-banner-text').textContent =
      "Still not back after 30s — check the container/process logs. If a saved value was invalid, startup may be failing.";
    return;
  }

  await new Promise((resolve) => setTimeout(resolve, 1000));

  try {
    const res = await fetch('/health', { cache: 'no-store' });
    if (res.ok) {
      window.location.reload();
      return;
    }
  } catch {
    // Expected mid-restart — the port isn't listening yet. Keep polling.
  }
  pollHealth(attempt + 1);
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}
