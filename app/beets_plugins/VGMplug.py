"""Custom beets metadata-source plugin for VGMDB (vgmdb.info).

Dropped into ``app/beets_plugins/`` and copied into the beets plugins
directory at Docker build time (see ``app/beets_plugins/.gitkeep``). This
is what ``beet import -S vgmdb:<id>`` actually talks to — it fetches an
album's track/credit data from a VGMDB instance and converts it into the
``AlbumInfo``/``TrackInfo`` shapes beets' autotagger expects.

Differs from the stock public ``beets-vgmdb`` plugin in a few
soundtrack-specific ways:

* **Per-field language priority.** VGMDB stores most text (album name,
  track titles, artist names) in multiple languages (English, romanised
  Japanese, Japanese). ``lang-priority`` picks which language ends up in
  the *final written tags*; ``match-lang-priority`` can independently
  control which language is used for *match-distance scoring* during
  import — useful when your files already have Japanese titles and you
  want beets to match them correctly, while still writing English tags.

* **Configurable artist role priority.** ``artist-priority`` controls
  which VGMDB credit role (composers / performers / arrangers) is used
  as the main album artist, since OSTs often credit performers and
  composers separately and the "right" one varies by release.

* **Never overwrites the MusicBrainz release id.** ``album_id`` is
  always returned as ``None`` so beets doesn't clobber
  ``musicbrainz_albumid`` with a VGMDB id; the VGMDB id is instead
  written to its own ``vgmdb_id`` / ``vgmdb_album_id`` tags.

* **Self-hosted instance support.** ``baseurl`` config overrides
  ``BASE_URL`` so this can point at a self-hosted vgmdb-api mirror
  instead of the public ``vgmdb.info``.
"""

from typing import Dict, List, Sequence, Optional, Iterable
import requests
import requests.exceptions
import re

from beets.plugins import BeetsPlugin
from beets.autotag.hooks import AlbumInfo, TrackInfo
from beets.autotag.distance import Distance, string_dist
from beets.autotag.match import Proposal, _add_candidate, _recommendation
from beets.ui import input_
from beets.util import PromptChoice

# VGMDB's API names languages with codes; this maps them to the values
# stored in album_info["names"] / track["names"] keys.
TRACK_NAME_CONVENTION = {"en": "English", "ja-latn": "Romaji", "ja": "Japanese"}


class VGMdbPlugin(BeetsPlugin):
    """Beets metadata-source plugin that resolves albums against VGMDB.

    Registered with beets as the ``VGMdb`` data source. Implements the
    ``MetadataSourcePlugin``-style interface beets expects:
    :meth:`candidates` / :meth:`album_for_id` to produce ``AlbumInfo``
    candidates, :meth:`track_distance` / :meth:`album_distance` to score
    them, and a ``before_choose_candidate`` listener to add manual-entry
    prompt choices during interactive import.
    """

    data_source = "VGMdb"  # MetadataSourcePlugin

    BASE_URL = "https://vgmdb.info"
    USERAGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:114.0) Gecko/20100101 Firefox/114.0"
    headers = {
        "User-Agent": USERAGENT
    }

    def __init__(self):
        """Load plugin config and pre-compute the language/artist-role
        priority lists used throughout the rest of the class."""
        super(VGMdbPlugin, self).__init__()
        self.config.add({"lang-priority": "en,ja-latn,ja", "source_weight": 0.0})
        self.config.add({"autosearch": False})
        self.config.add({"baseurl": self.BASE_URL})
        self.config.add({"artist-priority": "composers,performers,arrangers"})
        # match-lang-priority: language order used for track title distance calculation.
        # Set to "ja,ja-latn,en" when your files have Japanese titles so beets can
        # match them correctly against VGMDB. Final written tags still use lang-priority.
        self.config.add({"match-lang-priority": ""})

        self.artist_priority = self.config["artist-priority"].get().replace(" ", "").split(",")
        self.source_weight = self.config["source_weight"].as_number()
        self.lang = self.config["lang-priority"].get().replace(" ", "").split(",")
        self.track_pref = [TRACK_NAME_CONVENTION[lang] for lang in self.lang]
        self.auto = self.config["autosearch"].get()

        # Separate lang pref for distance matching — defaults to lang-priority if not set
        match_lang_str = self.config["match-lang-priority"].get().strip()
        if match_lang_str:
            match_langs = [l.strip() for l in match_lang_str.split(",") if l.strip()]
            self.match_track_pref = [
                TRACK_NAME_CONVENTION[l] for l in match_langs if l in TRACK_NAME_CONVENTION
            ]
        else:
            self.match_track_pref = self.track_pref

        self.register_listener("before_choose_candidate", self.before_choose_candidate_event)

    # ── URL helpers — computed lazily so baseurl from config.yaml is used ──────

    @property
    def _baseurl(self):
        """Configured VGMDB base URL, re-read on every access so a
        config.yaml change takes effect without restarting beets."""
        return self.config["baseurl"].get().rstrip("/")

    @property
    def _albumurl(self):
        return self._baseurl + "/album/"

    @property
    def _searchalbumsurl(self):
        return self._baseurl + "/search/albums/"

    # ── Plugin event handlers ──────────────────────────────────────────────────

    def before_choose_candidate_event(self, session, task):
        """Add the "type a VGMDB id" / "type a VGMDB query" manual prompt
        choices to beets' candidate-selection screen for album imports."""
        if task.is_album:
            return [
                PromptChoice("v", "type Vgmdb id", self.insert_manual_id),
                PromptChoice("q", "type vgmdb Query", self.custom_query),
            ]

    def parse_vgmdbinfo_artist(self, albuminfo, key, optional_album):
        """Try one credit role (``key`` — e.g. "composers") as the album
        artist.

        Picks the first credited person's name (using ``lang-priority``),
        adds a ``{role}_{lang}`` entry to ``optional_album`` for every
        language VGMDB has for that role (via
        :meth:`format_list_of_person`), and flags ``va`` (various artists)
        when more than one composer is credited.

        Returns ``(artist_found, main_artist, main_artist_id, va)``. Called
        in :data:`artist-priority<self.artist_priority>` order from
        :meth:`format_album_vgmdbinfo` until one role yields a result.
        """
        self._log.info(f"Completing artist info using {key}")
        artist_found = False
        va = False
        if len(albuminfo.get(key, [])) > 0:
            self._log.info(f"Found {len(albuminfo[key])} {key}")
            artist_found = True
            main_artist = list(albuminfo[key][0]["names"].values())[0]
            main_artist_id = (
                albuminfo[key][0]["link"].split("/")[1]
                if "link" in albuminfo[key][0].keys()
                else None
            )
            for lan in self.lang:
                if lan in albuminfo[key][0]["names"]:
                    main_artist = albuminfo[key][0]["names"][lan]
                    self._log.info(f"Final artist choice is {main_artist}")
                    break

            optional_album.update(self.format_list_of_person(albuminfo[key], key))
        else:
            self._log.info(f"{key} not found for this album.")
            main_artist = ""
            main_artist_id = None
        if len(albuminfo.get("composers", [])) > 1:
            va = True
        return artist_found, main_artist, main_artist_id, va

    def insert_manual_id(self, session, task):
        """Get a new `Proposal` using a manually-entered ID."""
        prompt = "Enter {} ID:".format("release" if task.is_album else "recording")
        search_id = input_(prompt).strip()

        candidates = {}
        custom_album = self.album_for_id(search_id)
        if custom_album is not None:
            _add_candidate(task.items, candidates, custom_album)
            rec = _recommendation(list(candidates.values()))
            return Proposal(list(candidates.values()), rec)
        else:
            self._log.error(f"Asking a manual id that has issues. {search_id}")
            return None

    def custom_query(self, session, task):
        """Get a new `Proposal` using a manually-entered query."""
        prompt = "Enter {} query:".format("release" if task.is_album else "recording")
        query = input_(prompt).strip()

        query_results = self._search_vgmdbinfo(query)
        candidates = {}
        if len(query_results) > 0:
            self._log.info(
                f"Found {len(query_results)} result for the query. Selecting the first choice."
            )
            _add_candidate(task.items, candidates, query_results[0])
            rec = _recommendation(list(candidates.values()))
            return Proposal(list(candidates.values()), rec)
        else:
            self._log.warning(f"No query result for {query}")
            return None

    # ── Distance functions ─────────────────────────────────────────────────────

    def track_distance(self, item, info: TrackInfo) -> Distance:
        """Score how well a local file's title matches one VGMDB track.

        Tries ``match_track_pref`` languages in order (e.g. Japanese, to
        match Japanese filenames) so correct matches aren't penalised
        just because the file titles aren't in VGMDB's default (English)
        language; only falls back to scanning every
        ``vgmdb_track_name_*`` variant and taking the closest if no
        preferred-language title is present.
        """
        dist = Distance()
        if info.data_source == self.data_source:
            min_dist = 1.0

            # First try match_track_pref languages in order (e.g. Japanese to match
            # Japanese filenames). This avoids penalising correct matches where the
            # file titles are in a different language than the VGMDB default (English).
            for pref_lang in self.match_track_pref:
                key = f"vgmdb_track_name_{pref_lang}"
                if key in info:
                    self._log.debug(f"match_track_pref: {self.match_track_pref}")
                    self._log.debug(f"info keys: {list(info.keys()) if hasattr(info, 'keys') else dir(info)}")
                    name_dist = string_dist(item.title, info[key])
                    if name_dist < min_dist:
                        min_dist = name_dist
                    break  # stop at first available preferred language

            # Fallback: try all vgmdb track name variants and take the best
            if min_dist == 1.0:
                for key in info.keys():
                    if key.startswith("vgmdb_track_name"):
                        name_dist = string_dist(item.title, info[key])
                        if name_dist < min_dist:
                            min_dist = name_dist

            dist.add("track_title", min_dist)
            dist.add("source", self.source_weight)
        return dist

    def album_distance(self, items: List, album_info: AlbumInfo, mapping: Dict) -> Distance:
        """Add the configured ``source_weight`` penalty/bonus for VGMDB
        candidates; beets combines this with per-track distances to rank
        candidates overall."""
        dist = Distance()
        if album_info.data_source == self.data_source:
            dist.add("source", self.source_weight)
        return dist

    # ── Search ─────────────────────────────────────────────────────────────────

    def _search_vgmdbinfo(self, query: str):
        """Search VGMDB by query string. Returns list of AlbumInfo objects."""
        try:
            req = requests.get(
                f"{self._searchalbumsurl}{query}?format=json",
                headers=self.headers
            )
            items = req.json()
            albums = []
            self._log.debug(
                f"Found {len(items['results']['albums'])} albums on VGMdb for query: {query}"
            )
            for album in items["results"]["albums"]:
                album_id = album["link"].split("/")[1]
                candidate_album = self.album_for_id(album_id)
                if candidate_album is not None:
                    albums.append(candidate_album)
                if len(albums) >= 5:
                    self._log.debug("Too many result on VGMDB, breaking")
                    break
            return albums
        except requests.exceptions.RequestException as e:
            self._log.error(f"Network Exception: {query}")
        except requests.exceptions.ChunkedEncodingError:
            self._log.error(f"Chunked Encoding Exception: {query}")
        except requests.exceptions.JSONDecodeError:
            self._log.error(f"Json Decode Error: {query}")
        return []

    # ── Formatting helpers ─────────────────────────────────────────────────────

    def _format_track_info(self, albuminfo, url):
        """Build the ``TrackInfo`` list for an album from VGMDB's
        disc/track structure.

        Track title uses ``lang-priority`` (so the final written tag is
        in the preferred language), but every language variant VGMDB has
        is also stashed as a ``vgmdb_track_name_{lang}`` optional field so
        :meth:`track_distance` can match against any of them.
        """
        tracks = []
        track_album_index = 0
        for disc_index, disc in enumerate(albuminfo["discs"]):
            disc_length = disc["disc_length"]
            for track_index, track in enumerate(disc["tracks"]):
                optional_args = {}
                track_album_index += 1

                track_l = track["track_length"].split(":")
                if (len(track_l) > 0) & (track_l[0] != "Unknown"):
                    track_length = 60 * int(track_l[0]) + int(track_l[1])
                else:
                    track_length = None

                # Pick title using lang-priority (for final written tags)
                track_title = list(track["names"].values())[0]
                for lang in self.track_pref:
                    if lang in track["names"].keys():
                        track_title = track["names"][lang]
                        break

                # Store ALL language variants so track_distance can use them
                for lang in track["names"].keys():
                    optional_args.update({f"vgmdb_track_name_{lang}": track["names"][lang]})

                tracks.append(
                    TrackInfo(
                        title=track_title,
                        track_id=None,
                        release_track_id=None,
                        artist=None,
                        artist_id=None,
                        length=float(track_length) if track_length is not None else None,
                        index=track_album_index,
                        medium=disc_index + 1,
                        medium_index=track_index + 1,
                        medium_total=len(disc["tracks"]),
                        artist_sort=None,
                        disctitle=disc["name"] if "name" in disc.keys() else None,
                        artist_credit=None,
                        data_source=self.data_source,
                        data_url=url,
                        media=None,
                        lyricist=None,
                        composer=None,
                        composer_sort=None,
                        arranger=None,
                        track_alt=None,
                        work=None,
                        mb_workid=None,
                        work_disambig=None,
                        bpm=None,
                        initial_key=None,
                        genre=None,
                        **optional_args,
                    )
                )
        return tracks

    def format_list_of_person(self, listofVGMPerson: List, typeofPerson: str):
        """Flatten a VGMDB credit-role list (e.g. all composers) into
        ``{typeofPerson}_{lang}: "Name A,Name B"`` entries, one per
        language VGMDB has names in, for every person who has that
        language. Used to add e.g. ``composers_en`` / ``composers_ja``
        tags via :meth:`parse_vgmdbinfo_artist`.
        """
        out = {}
        if len(listofVGMPerson) > 0:
            if "names" in listofVGMPerson[0].keys():
                for lang in listofVGMPerson[0]["names"].keys():
                    out[f"{typeofPerson}_{lang}"] = ",".join(
                        [
                            person["names"][lang]
                            for person in listofVGMPerson
                            if lang in person["names"].keys()
                        ]
                    )
        return out

    def format_album_vgmdbinfo(self, albuminfo: Dict, url: Optional[str] = None) -> AlbumInfo:
        """Convert one raw VGMDB album JSON payload into beets' ``AlbumInfo``.

        Resolves the album title and publisher/label via ``lang-priority``,
        the main artist via the ``artist-priority`` role chain (see
        :meth:`parse_vgmdbinfo_artist`), and builds the track list via
        :meth:`_format_track_info`. ``album_id`` is deliberately left
        ``None`` (see module docstring) so beets doesn't overwrite
        ``musicbrainz_albumid``; the VGMDB id is carried separately as the
        ``vgmdb_id`` / ``vgmdb_album_id`` optional fields.
        """
        main_artist = ""
        main_artist_id = None
        va = False
        optional_album = {}
        tracks = self._format_track_info(albuminfo, url)

        # Album Name — use lang-priority for final tags
        album_name = albuminfo["name"]
        for lang in self.lang:
            if lang in albuminfo["names"].keys():
                album_name = albuminfo["names"][lang]
                break

        # Album VGMdb ID
        album_id = albuminfo["link"].split("/")[1]
        optional_album.update({
            "vgmdb_id": album_id,           # written as tag: vgmdb_id
            "vgmdb_album_id": album_id,     # written as tag: vgmdb_album_id (separate, clear)
        })

        # Artist
        for key in self.artist_priority:
            artist_found, main_artist, main_artist_id, va = self.parse_vgmdbinfo_artist(
                albuminfo, key, optional_album
            )
            if artist_found:
                break



        # Release date
        date = albuminfo["release_date"].split("-")
        if len(date) == 3:
            year, month, day = date
            year = int(year)
            month = int(month)
            day = int(day)
        else:
            year = None
            month = None
            day = None

        # Label / publisher
        if "publisher" not in albuminfo:
            albuminfo["publisher"] = {"link": {}, "names": {}, "role": {}}
            if "distributor" in albuminfo:
                albuminfo["publisher"] = albuminfo["distributor"]
        publisher = (
            list(albuminfo["publisher"]["names"].values())[0]
            if len(albuminfo["publisher"]["names"]) > 0
            else None
        )
        for lang in self.lang:
            if lang in albuminfo["publisher"]["names"].keys():
                publisher = albuminfo["publisher"]["names"][lang]

        optional_album.update({"vgmdb_album_artist_id": main_artist_id})
        
        # Store VGMDB ID as custom tag — don't overwrite musicbrainz_albumid
        # with the vgmdb ID. The real MB release ID should be preserved.
        # optional_album already has {"vgmdb_id": album_id} from above.
        return AlbumInfo(
            tracks=tracks,
            album=album_name,
            album_id=None,  # prevents overwriting musicbrainz_albumid
            artist=main_artist,
            artist_id=None,
            asin=None,
            albumtype=albuminfo.get("classification", None),
            va=va,
            year=year,
            month=month,
            day=day,
            label=publisher,
            mediums=len(albuminfo["discs"]),
            artist_sort=None,
            releasegroup_id=None,
            catalognum=albuminfo["catalog"],
            script=None,
            language=None,
            country=None,
            style=None,
            genre=albuminfo["category"],
            albumstatus=None,
            media=albuminfo["media_format"],
            albumdisambig=None,
            releasegroupdisambig=None,
            artist_credit=None,
            original_year=None,
            original_month=None,
            original_day=None,
            data_source=self.data_source,
            data_url=albuminfo["vgmdb_link"],
            cover_art_url=albuminfo["picture_full"] if "picture_full" in albuminfo else None,
            **optional_album,
        )

    def sanitize(self, title: str) -> str:
        """Clean text for VGMdb search."""
        clean = re.sub(r"(?u)\W+", " ", title)
        clean = re.sub(r"(?i)\b(CD|disc)\s*\d+", "", clean)
        self._log.debug(f"Title satinize: {title} -> {clean}")
        return clean

    def candidates(
        self,
        items: List[str],
        artist: str,
        album: str,
        va_likely: bool,
        extra_tags=None,
    ) -> Sequence[AlbumInfo]:
        """Return automatic VGMDB candidates for an import, or ``[]`` if
        ``autosearch`` is disabled (the default — manual ``v``/``q``
        prompts via :meth:`before_choose_candidate_event` are the normal
        path; this only fires during beets' standard non-interactive
        candidate search)."""
        if self.auto:
            self._log.debug(f"Searching for candidate in VGMdb for {album}")
            albums = []
            queries = self._format_query(artist, album, va_likely)
            for query in queries:
                albums += self._search_vgmdbinfo(query)
            return albums
        return []

    def _format_query(self, artist, album, va_likely) -> Iterable:
        """Split the album title on " -" (e.g. "Title - Special Edition")
        into separate sanitized search queries, since VGMDB album titles
        with an edition/subtitle suffix often only match well on the
        portion before the dash."""
        return [self.sanitize(text) for text in album.split(" -") if len(self.sanitize(text)) > 0]

    def album_for_id(self, album_id: int) -> Optional[AlbumInfo]:
        """Take a VGMdb id and return an AlbumInfo object.

        Accepts plain numeric IDs (154235) or prefixed forms
        (vgmdb:154235 / vgmdb-154235) — prefix is stripped automatically.
        Uses baseurl from config.yaml so a self-hosted vgmdb.info
        instance is used instead of the public vgmdb.info.
        """
        album_id = str(album_id).replace("vgmdb:", "").replace("vgmdb-", "")
        self._log.debug(f"Querying VgmDB for release {album_id}")
        try:
            url = f"{self._albumurl}{album_id}"
            req = requests.get(f"{url}?format=json", headers=self.headers)
            req.raise_for_status()
            vgmdbinfo = req.json()
            return self.format_album_vgmdbinfo(vgmdbinfo, url=url)
        except requests.exceptions.HTTPError as e:
            self._log.error(f"HTTP Error: {album_id} — {e}")
        except requests.exceptions.RequestException as e:
            self._log.error(f"Network Problem: {album_id} — {e}")
        except requests.exceptions.JSONDecodeError as e:
            self._log.error(f"JsonDecodeError: {album_id} — {e}")
        return None