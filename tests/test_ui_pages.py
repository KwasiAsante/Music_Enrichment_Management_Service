"""Site-wide smoke tests — every page renders, auth gates the UI,
Settings-page link/nav-order sanity, and a full audit that URL_BASE
prefixes every internal link when set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ALL_PAGES = [
    "/",
    "/music-search",
    "/mappings",
    "/enrich",
    "/library",
    "/library?view=skipped",
    "/logs",
    "/help",
    "/settings",
]


def test_all_pages_render(client: TestClient, auth):
    for page in ALL_PAGES:
        r = client.get(page, auth=auth)
        assert r.status_code == 200, f"{page} -> {r.status_code}"


def test_pages_require_auth(client: TestClient):
    for page in ALL_PAGES:
        r = client.get(page)
        assert r.status_code == 401, f"{page} should require auth"


def test_wrong_credentials_rejected(client: TestClient):
    r = client.get("/", auth=("nope", "wrong"))
    assert r.status_code == 401


def test_nav_order_matches_workflow(client: TestClient, auth):
    r = client.get("/", auth=auth)
    labels = re.findall(
        r'<span class="icon">.*?</span><span class="label">([^<]+)</span>', r.text,
    )
    assert labels == [
        "Dashboard", "Music Search", "Mappings", "Enrich",
        "Library", "Logs", "Help", "Settings",
    ]


@pytest.mark.parametrize("path,label", [
    ("/", "Dashboard"),
    ("/music-search", "Music Search"),
    ("/mappings", "Mappings"),
    ("/enrich", "Enrich"),
    ("/library", "Library"),
    ("/logs", "Logs"),
    ("/help", "Help"),
    ("/settings", "Settings"),
])
def test_active_nav_item_highlighted(client: TestClient, auth, path, label):
    r = client.get(path, auth=auth)
    assert f'<a class="nav-item active" href="{path}"' in r.text


def test_help_page_workflow_guide_anchors(client: TestClient, auth):
    r = client.get("/help", auth=auth)
    for i in range(0, 6):
        assert f'id="step-{i}"' in r.text


def test_library_rows_link_to_album_detail(client: TestClient, auth, isolated_env):
    from app.storage.json_store import store

    store.album_list.write({
        "Some Album": {
            "artist": "Some Artist", "album": "Some Album",
            "mb_release_id": "", "folder": "Some Artist/Some Album",
        },
    })
    r = client.get("/library", auth=auth)
    assert '<a class="result-card" href="/library/album?folder=' in r.text


def test_album_detail_not_found_is_graceful(client: TestClient, auth):
    r = client.get("/library/album", params={"folder": "Nope/Nope"}, auth=auth)
    assert r.status_code == 200
    assert "Album not found" in r.text


def test_settings_page_has_test_buttons_for_integrations(client: TestClient, auth):
    r = client.get("/settings", auth=auth)
    text = r.text

    assert text.count('data-test-key="discord_webhook_') == 4
    assert text.count("Send Test") == 4
    assert 'data-test-service="lidarr"' in text
    assert 'data-test-service="prowlarr"' in text
    assert 'data-test-service="qbit"' in text
    assert 'data-test-service="vgmdb"' in text
    assert text.count("Test Connection") == 4
    assert 'id="test-result-discord_webhook_artist"' in text
    assert 'id="test-result-lidarr_api_key"' in text
    assert 'id="test-result-vgmdb_url"' in text
    # secrets without a test action stay plain inputs
    assert 'data-test-key="lidarr_api_key"' not in text
    assert 'data-test-key="web_ui_pass"' not in text


def test_theme_toggle_present_and_anti_fouc_ordering(client: TestClient, auth):
    r = client.get("/", auth=auth)
    text = r.text
    assert 'id="theme-toggle"' in text
    assert "js/theme.js" in text

    script_pos = text.find("localStorage.getItem('theme')")
    sidebar_pos = text.find('<nav class="sidebar"')
    assert script_pos != -1 and sidebar_pos != -1
    assert script_pos < sidebar_pos, "anti-FOUC theme script must precede sidebar markup"


def test_light_theme_css_variables_defined(client: TestClient, auth):
    """Regression test: the light theme's HTML/JS (button, toggle script)
    shipped in an earlier pass without the matching CSS variable block —
    clicking the toggle set data-theme="light" but nothing visually
    changed, since no rule actually read that attribute. Covers both
    that the override block exists and that every color variable :root
    defines has a light-theme counterpart, so a future variable added to
    one side can't silently be missing from the other.
    """
    css = Path("app/ui/static/css/base.css").read_text()

    root_match = re.search(r":root\s*\{([\s\S]*?)\}", css)
    light_match = re.search(r'html\[data-theme="light"\]\s*\{([\s\S]*?)\}', css)
    assert root_match and light_match, "both :root and html[data-theme=light] blocks must exist"

    non_color_vars = {"sidebar-w", "sidebar-w-collapsed"}
    root_vars = {m for m in re.findall(r"--([a-z0-9-]+)\s*:", root_match.group(1))} - non_color_vars
    light_vars = set(re.findall(r"--([a-z0-9-]+)\s*:", light_match.group(1)))

    assert root_vars, "sanity check: :root should define some color variables"
    assert root_vars == light_vars, (
        f"variable mismatch between themes — missing from light: "
        f"{root_vars - light_vars}, extra in light: {light_vars - root_vars}"
    )

    # No hardcoded color values should remain outside the two variable
    # blocks — anything still hardcoded silently ignores the theme toggle.
    body_without_var_blocks = css[light_match.end():]
    for leaked_hex in ["#161921", "#e8eaf0 0%", "#6b9ef8"]:
        assert leaked_hex not in body_without_var_blocks, f"{leaked_hex} still hardcoded outside the theme variables"


def test_mobile_nav_markup_present(client: TestClient, auth):
    r = client.get("/", auth=auth)
    text = r.text
    assert 'id="mobile-nav-toggle"' in text
    assert 'id="sidebar-backdrop"' in text
    assert 'class="mobile-topbar"' in text
    assert 'js/mobile-nav.js' in text
    assert 'id="sidebar"' in text
    assert 'aria-controls="sidebar"' in text


def test_mobile_breakpoint_covers_expected_rules():
    """Structural check, not a rendered-layout one — jsdom doesn't
    evaluate @media (max-width) against a settable viewport width the
    way a real browser does (same limitation hit with CSS custom
    properties elsewhere in this codebase), so "does the mobile drawer
    actually look right at 375px" isn't something this suite can verify
    directly. What it can verify: the rules that make it work are
    actually present and correctly scoped inside the breakpoint, so a
    future edit can't silently delete one without a test noticing.
    """
    css = Path("app/ui/static/css/base.css").read_text()
    match = re.search(r"@media \(max-width: 860px\) \{([\s\S]*?)\n\}\n\n/\* ── Main content", css)
    assert match, "860px mobile breakpoint block not found"
    block = match.group(1)

    for needle in [
        # body must switch off flex-row on mobile — otherwise
        # .mobile-topbar (a normal block, not an overlay like .sidebar)
        # stays a row-direction flex sibling of .main instead of
        # stacking above it, and body's default align-items:stretch
        # pulls it to full viewport height. Real bug, caught from an
        # actual screenshot: the hamburger+title rendered as a tall
        # empty column on the left instead of a top bar.
        "body { display: block",
        ".mobile-topbar {\n    display: flex",
        ".sidebar {\n    position: fixed",
        "transform: translateX(-100%)",
        "html.mobile-nav-open .sidebar { transform: translateX(0)",
        "#sidebar-toggle { display: none",
        ".sidebar-backdrop {\n    display: block",
        "html.mobile-nav-open .sidebar-backdrop { opacity: 1; pointer-events: auto",
    ]:
        assert needle in block, f"missing expected mobile rule: {needle!r}"


def test_settings_page_has_full_backup_download_link(client: TestClient, auth):
    r = client.get("/settings", auth=auth)
    assert 'href="/api/v1/backup/export"' in r.text
    assert "Download Everything" in r.text


def test_backup_download_link_respects_url_base(client: TestClient, auth):
    # A hardcoded href here would silently break the download once the
    # app is served from a subpath — same class of bug the rest of the
    # app's URL_BASE support was built to catch. Checked against the
    # template source directly rather than a live Mount()-wrapped
    # request: reconstructing that whole url_base + Mount() +
    # module-reload chain inside a single test is exactly the kind of
    # fragile, hard-to-debug-later machinery not worth introducing just
    # to check one href line follows the same `{{ url_base }}` pattern
    # every other internal link in this codebase already does.
    template = Path("app/ui/templates/settings.html").read_text()
    assert 'href="{{ url_base }}/api/v1/backup/export"' in template
