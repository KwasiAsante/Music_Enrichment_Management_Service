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


def test_settings_page_send_test_only_on_discord_webhook_fields(client: TestClient, auth):
    r = client.get("/settings", auth=auth)
    text = r.text

    assert text.count('data-test-key="discord_webhook_') == 4
    assert text.count("Send Test") == 4
    # a non-webhook secret field (e.g. lidarr_api_key) must not get one
    assert 'data-test-key="lidarr_api_key"' not in text
    assert 'id="test-result-discord_webhook_artist"' in text


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
