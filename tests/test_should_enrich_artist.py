"""app.core.beets_enricher.BeetsEnricher.should_enrich_artist — the
Japanese/Western-artist enrichment decision, including the
included_artists.json override for artists with a Western MB
country/area that should be force-enriched anyway.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.core.beets_enricher import BeetsEnricher
from app.storage.json_store import store


def _make_enricher(mb_artist_data: dict | None) -> BeetsEnricher:
    mb = MagicMock()
    mb.get_artist.return_value = mb_artist_data
    return BeetsEnricher(mb=mb, mapper=MagicMock(), notifier=MagicMock(),
                          mb_link=MagicMock(), scanner=MagicMock())


def test_western_country_is_skipped_by_default(isolated_env):
    enricher = _make_enricher({"country": "US", "area": {"name": "United States"}})
    decision, _ = enricher.should_enrich_artist("Some Western Artist", "mbid-1")
    assert decision == "no"


def test_included_artist_overrides_western_skip(isolated_env):
    store.included_artists.write(["Jeff Williams"])
    enricher = _make_enricher({"country": "US", "area": {"name": "United States"}})
    decision, reason = enricher.should_enrich_artist("Jeff Williams", "mbid-2")
    assert decision == "yes"
    assert "override" in reason.lower()


def test_included_artist_is_case_insensitive(isolated_env):
    store.included_artists.write(["Jeff Williams"])
    enricher = _make_enricher({"country": "US"})
    decision, _ = enricher.should_enrich_artist("jeff williams", "mbid-3")
    assert decision == "yes"


def test_included_artist_skips_mb_lookup_entirely(isolated_env):
    store.included_artists.write(["Jeff Williams"])
    enricher = _make_enricher(None)
    decision, _ = enricher.should_enrich_artist("Jeff Williams", None)
    assert decision == "yes"
    enricher.mb.get_artist.assert_not_called()


def test_explicit_exclude_still_wins_over_included(isolated_env):
    store.excluded_artists.write(["Jeff Williams"])
    store.included_artists.write(["Jeff Williams"])
    enricher = _make_enricher({"country": "US"})
    decision, reason = enricher.should_enrich_artist("Jeff Williams", "mbid-4")
    assert decision == "no"
    assert reason == "explicitly excluded"


def test_japanese_country_still_enriched(isolated_env):
    enricher = _make_enricher({"country": "JP"})
    decision, _ = enricher.should_enrich_artist("Some JP Artist", "mbid-5")
    assert decision == "yes"
