import json, os, tempfile, unittest
from unittest.mock import patch

from system_manager import create_app
from system_manager.organizer import api

CATALOG = [
    {"path": "G:\\Music\\Alpha\\Album One\\01 Song One.mp3", "drive": "G", "category": "music", "ext": ".mp3", "size_bytes": 100, "mtime": 1701066090},
    {"path": "G:\\Music\\Alpha\\Album Two\\02 Song Two.flac", "drive": "G", "category": "music", "ext": ".flac", "size_bytes": 300, "mtime": 0},
    {"path": "G:\\Music\\Alpha\\Live\\03 Song Three.mp3", "drive": "G", "category": "music", "ext": ".mp3", "size_bytes": 40, "mtime": 0},
    {"path": "E:\\Music\\Beta\\Album B\\01 Song B.mp3", "drive": "E", "category": "music", "ext": ".mp3", "size_bytes": 50, "mtime": 0},
]

class MusicPageTests(unittest.TestCase):
    def setUp(self):
        data = tempfile.mkdtemp(prefix="sm-music-test-")
        with open(os.path.join(data, "music_catalog.json"), "w", encoding="utf-8") as f:
            json.dump(CATALOG, f)
        with open(os.path.join(data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump({"problems": [{"path": CATALOG[0]["path"], "missing": ["title"]}]}, f)
        self.data = data
        for name, value in (("DATA", data),
                            ("TAG_PLAN_FILE", os.path.join(data, "tag_plan.json")),

                            ("CMD_DIR", data)):
            patcher = patch(f"system_manager.organizer.api.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = create_app({"DISABLE_AUTH": 1, "TESTING": True,
                          "AUDIT_PATH": os.path.join(data, "actions.db")})
        self.client = app.test_client()

    def get(self, **params):
        return self.client.get("/organizer/music", query_string=params).get_data(as_text=True)

    def test_artist_mode_sorted_by_track_count_with_untagged_flag(self):
        body = self.get()
        self.assertLess(body.index('data-key="Alpha"'), body.index('data-key="Beta"'))
        self.assertIn("1 untagged", body)

    def test_drive_filter_drops_other_drive_artists(self):
        body = self.get(drive="E")
        self.assertIn('data-key="Beta"', body)
        self.assertNotIn('data-key="Alpha"', body)

    def test_artist_drilldown_lists_tracks_even_off_page(self):
        self.assertIn("01 Song B.mp3", self.get(key="Beta"))

    def test_folder_mode_splits_artist_into_albums(self):
        body = self.get(mode="folder")
        self.assertIn('data-key="G:/Music/Alpha/Live"', body)
        self.assertIn('title="Alpha/Live"', body)

    def test_folder_drilldown_lists_only_that_folder(self):
        body = self.get(mode="folder", key="G:\\Music\\Alpha\\Live")
        self.assertIn("03 Song Three.mp3", body)
        self.assertNotIn("01 Song One.mp3", body)

class FixUntaggedTests(MusicPageTests):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp(prefix="sm-media-test-")
        self.track = os.path.join(self.media, "01 Song One.mp3")
        with open(self.track, "wb") as f:
            f.write((bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)) * 3)
        for target in ("system_manager.organizer.api.to_host_path", "apply_music_tags.to_host_path"):
            mapper = patch(target, lambda p: self.track if p == CATALOG[0]["path"] else p)
            mapper.start()
            self.addCleanup(mapper.stop)
        # MusicBrainz is not reachable from the test run and must not be
        # consulted: the year lookup is a network call the tests never asked for.
        no_mb = patch("system_manager.organizer.mb_lookup.lookup_albums",
                      return_value={("Alpha", "Album One"): None})
        no_mb.start()
        self.addCleanup(no_mb.stop)

    def post(self, body):
        return self.client.post("/organizer/api/music/fix-untagged", json=body)

    def test_fix_untagged_exports_only_that_artist(self):
        r = self.post({"key": "Alpha", "mode": "artist", "dry": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        data = json.loads(r.get_data(as_text=True))
        self.assertTrue(data["ok"])
        self.assertEqual(data["accepted"], 1)
        self.assertIn("would-apply=1", data["summary"])
        plan_entries = json.load(open(os.path.join(self.data, "tag_plan.json")))["entries"]
        self.assertEqual(list(plan_entries), [CATALOG[0]["path"]])
        group = json.load(open(os.path.join(self.data, "tag_fix_group.json")))["entries"]
        self.assertEqual([e["path"] for e in group], [CATALOG[0]["path"]])

    def test_real_run_writes_artist_tag(self):
        from mutagen import File
        r = self.post({"key": "Alpha", "mode": "artist", "dry": False})
        self.assertTrue(json.loads(r.get_data(as_text=True))["ok"])
        self.assertEqual(File(self.track, easy=True).get("artist"), ["Alpha"])

    def test_low_confidence_year_does_not_block_track(self):
        from mutagen import File
        r = self.post({"key": "Alpha", "mode": "artist", "dry": False})
        self.assertTrue(json.loads(r.get_data(as_text=True))["ok"])
        self.assertEqual(File(self.track, easy=True).get("year"), None)

    def test_written_tag_drops_the_count_on_the_page(self):
        """A written tag must come off the card's untagged count.

        The audit is a snapshot from scan time, so a fix leaves it stale and
        the card keeps offering the same tracks again.
        """
        self.post({"key": "Alpha", "mode": "artist", "dry": False})
        body = self.get()
        self.assertNotIn("1 untagged", body)
        audit = json.load(open(os.path.join(self.data, "music_tag_audit.json")))
        self.assertEqual(audit["files_needing_fix"], 0)
        self.assertEqual(audit["problems"], [])

    def test_fix_untagged_rejects_group_with_no_untagged_tracks(self):
        self.assertEqual(self.post({"key": "Beta", "mode": "artist"}).status_code, 400)

    def test_the_failure_links_to_the_music_tags_page(self):
        """A group nothing can be proposed for must say where to go instead.

        The page's own filters are the folder, the missing field and the plan
        state, so the link carries the field the group was actually missing.
        """
        # A filename that is only a track number carries no tag evidence
        # after the prefix is stripped, so nothing can be proposed for it.
        flat = [{"path": "G:\\Music\\Files\\001).mp3", "drive": "G",
                 "category": "music", "ext": ".mp3", "size_bytes": 10, "mtime": 0}]
        with open(os.path.join(self.data, "music_catalog.json"), "w", encoding="utf-8") as f:
            json.dump(flat, f)
        with open(os.path.join(self.data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump({"problems": [{"path": flat[0]["path"], "missing": ["title", "album"]}]}, f)
        r = self.post({"key": "Files", "mode": "artist", "dry": True})
        self.assertEqual(r.status_code, 400)
        data = json.loads(r.get_data(as_text=True))
        self.assertFalse(data["ok"])
        self.assertIn("Music Tags", data["error"])
        self.assertIn("/organizer/music/tags", data["tags_url"])
        # An artist is not a folder on that page, so the link searches for
        # the artist's name and narrows to the field that was missing.
        self.assertIn("q=Files", data["tags_url"])
        self.assertIn("missing=", data["tags_url"])
        self.assertIn("title", data["tags_url"])

    def test_a_folder_group_links_to_its_folder(self):
        """A folder is a folder on the Music Tags page, so it filters by it."""
        # A filename that is only a track number carries no tag evidence.
        flat = [{"path": "G:\\Music\\Files\\001).mp3", "drive": "G",
                 "category": "music", "ext": ".mp3", "size_bytes": 10, "mtime": 0}]
        with open(os.path.join(self.data, "music_catalog.json"), "w", encoding="utf-8") as f:
            json.dump(flat, f)
        with open(os.path.join(self.data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump({"problems": [{"path": flat[0]["path"], "missing": ["title"]}]}, f)
        r = self.post({"key": "G:/Music/Files", "mode": "folder", "dry": True})
        self.assertEqual(r.status_code, 400)
        data = json.loads(r.get_data(as_text=True))
        self.assertIn("folder=Files", data["tags_url"])
        self.assertNotIn("q=", data["tags_url"])

    def test_a_big_group_is_served_in_chunks(self):
        """One request cannot cover thousands of files, so the group is
        chunked and the response says where the next one starts."""
        many = self._big_group(1200)
        r = self.post({"key": "Big", "mode": "artist", "dry": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        data = json.loads(r.get_data(as_text=True))
        self.assertEqual(data["total"], 1200)
        self.assertEqual(data["processed"], 500)
        self.assertEqual(data["remaining"], 700)
        self.assertEqual(data["next_offset"], 500)
        # The chunk covers only the first 500 tracks.
        plan = json.load(open(os.path.join(self.data, "tag_plan.json")))["entries"]
        self.assertEqual(len(plan), 500)

    def test_the_offset_selects_the_next_chunk(self):
        self._big_group(1200)
        r = self.post({"key": "Big", "mode": "artist", "dry": True, "offset": 500})
        data = json.loads(r.get_data(as_text=True))
        self.assertEqual(data["offset"], 500)
        self.assertEqual(data["processed"], 1000)
        self.assertEqual(data["remaining"], 200)
        plan = json.load(open(os.path.join(self.data, "tag_plan.json")))["entries"]
        self.assertEqual(len(plan), 500)
        names = sorted(p.split("\\")[-1] for p in plan)
        # The second chunk is the tracks after the first 500.
        self.assertEqual(names[0], "500 Song.mp3")
        self.assertEqual(names[-1], "999 Song.mp3")

    def _big_group(self, count):
        """A catalog of `count` tracks, each pointing at a file that exists."""
        many = [{"path": f"G:\\Music\\Big\\Album\\{i:03d} Song.mp3", "drive": "G",
                 "category": "music", "ext": ".mp3", "size_bytes": 10, "mtime": 0}
                for i in range(count)]
        with open(os.path.join(self.data, "music_catalog.json"), "w", encoding="utf-8") as f:
            json.dump(many, f)
        with open(os.path.join(self.data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump({"problems": [{"path": m["path"], "missing": ["title"]} for m in many]}, f)
        # Every path maps to a file that exists, so the write step finds them.
        def to_real(path):
            return self.track if path.startswith("G:\\Music\\") else path
        for target in ("system_manager.organizer.api.to_host_path", "apply_music_tags.to_host_path"):
            mapper = patch(target, to_real)
            mapper.start()
            self.addCleanup(mapper.stop)
        return many


class MusicBrainzYearTests(unittest.TestCase):
    """The year has to come from the album, not from whichever release of the
    track happened to match first."""

    def setUp(self):
        from system_manager.organizer import mb_lookup
        self.mb = mb_lookup
        mb_lookup._album_cache.clear()
        mb_lookup._track_cache.clear()

    def tearDown(self):
        self.mb._album_cache.clear()
        self.mb._track_cache.clear()

    def test_year_comes_from_the_album_release(self):
        with patch.object(self.mb, "_search", return_value=[
                {"id": "r1", "title": "Album One", "score": 100}]), \
             patch.object(self.mb, "_release_detail", return_value=("2010", {"song one": 1})):
            found = self.mb.lookup_albums([{"artist": "Alpha", "album": "Album One"}])
        self.assertEqual(found[("Alpha", "Album One")]["year"], "2010")

    def test_a_fuzzy_release_does_not_win_over_an_exact_one(self):
        releases = [{"id": "r2", "title": "Album One (Live)", "score": 100},
                    {"id": "r1", "title": "Album One", "score": 88}]
        with patch.object(self.mb, "_search", return_value=releases), \
             patch.object(self.mb, "_release_detail", return_value=("2010", {})):
            found = self.mb.lookup_albums([{"artist": "Alpha", "album": "Album One"}])
        self.assertEqual(found[("Alpha", "Album One")]["year"], "2010")

    def test_the_same_album_is_looked_up_once(self):
        """The dashboard asks for the same album twice per click (preview,
        then write); a second request would double the wait."""
        with patch.object(self.mb, "_search", return_value=[
                {"id": "r1", "title": "Album One", "score": 100}]) as search, \
             patch.object(self.mb, "_release_detail", return_value=("2010", {})):
            self.mb.lookup_albums([{"artist": "Alpha", "album": "Album One"}])
            self.mb.lookup_albums([{"artist": "Alpha", "album": "Album One"}])
        self.assertEqual(search.call_count, 1)

    def test_track_not_on_the_release_gets_no_year(self):
        """A folder named after an album it is not must not borrow its year."""
        proposed = {"G:\\Music\\Alpha\\Album One\\09 - Other Song.mp3": {
            "fields": {"artist": {"value": "Alpha"}, "album": {"value": "Album One"},
                       "title": {"value": "Other Song"},
                       "year": {"value": "2020", "confidence": "low"}}}}
        by_path = {"G:\\Music\\Alpha\\Album One\\09 - Other Song.mp3": {"year": None}}
        with patch.object(self.mb, "lookup_albums", return_value={
                ("Alpha", "Album One"): {"year": "2010", "tracks": {"song one": 1}}}):
            from system_manager.organizer import api
            api._fill_years_from_musicbrainz(proposed, by_path)
        year = proposed["G:\\Music\\Alpha\\Album One\\09 - Other Song.mp3"]["fields"]["year"]
        self.assertEqual(year["value"], "2020")
        self.assertEqual(year["confidence"], "low")

    def test_a_compilation_track_falls_back_to_its_studio_album(self):
        """A folder with no release of its own is dated from the studio album.

        The compilation folder has no MusicBrainz release, so the track's own
        studio album is the only honest answer.
        """
        proposed = {"G:\\Music\\Alpha\\Compilation\\01 - Song One.mp3": {
            "fields": {"artist": {"value": "Alpha"}, "album": {"value": "Compilation"},
                       "title": {"value": "Song One"},
                       "year": {"value": "2020", "confidence": "low"}}}}
        by_path = {"G:\\Music\\Alpha\\Compilation\\01 - Song One.mp3": {"year": None}}
        with patch.object(self.mb, "lookup_albums", return_value={}), \
             patch.object(self.mb, "lookup_track_years", return_value={
                 ("Alpha", "Song One"): {"year": "1994", "album": "Studio Album"}}) as tracks:
            from system_manager.organizer import api
            api._fill_years_from_musicbrainz(proposed, by_path)
        year = proposed["G:\\Music\\Alpha\\Compilation\\01 - Song One.mp3"]["fields"]["year"]
        self.assertEqual(year["value"], "1994")
        self.assertEqual(year["source"], "musicbrainz")

    def test_a_track_with_no_studio_album_stays_undated(self):
        """Better no year than the year of a concert or a compilation."""
        proposed = {"G:\\Music\\Alpha\\Compilation\\01 - Song One.mp3": {
            "fields": {"artist": {"value": "Alpha"}, "album": {"value": "Compilation"},
                       "title": {"value": "Song One"},
                       "year": {"value": "2020", "confidence": "low"}}}}
        by_path = {"G:\\Music\\Alpha\\Compilation\\01 - Song One.mp3": {"year": None}}
        with patch.object(self.mb, "lookup_albums", return_value={}), \
             patch.object(self.mb, "lookup_track_years", return_value={
                 ("Alpha", "Song One"): None}):
            from system_manager.organizer import api
            api._fill_years_from_musicbrainz(proposed, by_path)
        year = proposed["G:\\Music\\Alpha\\Compilation\\01 - Song One.mp3"]["fields"]["year"]
        self.assertEqual(year["confidence"], "low")

    def test_a_large_unknown_group_is_not_looked_up_track_by_track(self):
        """An artist whose albums are all unknown must not cost a request
        per track -- that is minutes of waiting for nothing."""
        from system_manager.organizer import api
        proposed = {}
        by_path = {}
        for i in range(500):
            path = f"G:\\Music\\Big\\Compilation\\{i:03d} Song.mp3"
            proposed[path] = {"fields": {"artist": {"value": "Big"},
                                         "album": {"value": "Compilation"},
                                         "title": {"value": f"Song {i}"},
                                         "year": {"value": "2020", "confidence": "low"}}}
            by_path[path] = {"year": None}
        with patch.object(self.mb, "lookup_albums", return_value={}), \
             patch.object(self.mb, "lookup_track_years") as fallback:
            api._fill_years_from_musicbrainz(proposed, by_path)
        fallback.assert_not_called()
        for entry in proposed.values():
            self.assertEqual(entry["fields"]["year"]["confidence"], "low")


class TrackYearLookupTests(unittest.TestCase):
    """A track's year comes from the studio album, not from the live one."""

    def setUp(self):
        from system_manager.organizer import mb_lookup
        self.mb = mb_lookup
        mb_lookup._track_cache.clear()

    def tearDown(self):
        self.mb._track_cache.clear()

    def _recording(self, releases):
        return {"recordings": [{"title": "Song One", "releases": releases}]}

    def test_a_live_release_is_not_the_year(self):
        releases = [{"date": "2018-11-16", "title": "Live at the Acropolis",
                     "status": "Official",
                     "release-group": {"primary-type": "Album", "secondary-types": ["Live"]}}]
        with patch.object(self.mb, "_get", return_value=self._recording(releases)):
            found = self.mb.lookup_track_years([{"artist": "Alpha", "title": "Song One"}])
        self.assertIsNone(found[("Alpha", "Song One")])

    def test_a_studio_album_is_the_year(self):
        releases = [{"date": "2018-11-16", "title": "Live at the Acropolis",
                     "status": "Official",
                     "release-group": {"primary-type": "Album", "secondary-types": ["Live"]}},
                    {"date": "1994-03-01", "title": "Studio Album", "status": "Official",
                     "release-group": {"primary-type": "Album", "secondary-types": None}}]
        with patch.object(self.mb, "_get", return_value=self._recording(releases)):
            found = self.mb.lookup_track_years([{"artist": "Alpha", "title": "Song One"}])
        self.assertEqual(found[("Alpha", "Song One")]["year"], "1994")
        self.assertEqual(found[("Alpha", "Song One")]["album"], "Studio Album")

    def test_a_compilation_release_is_not_the_year(self):
        releases = [{"date": "1991-05-01", "title": "In Celebration of Life",
                     "status": "Official",
                     "release-group": {"primary-type": "Album",
                                       "secondary-types": ["Compilation"]}}]
        with patch.object(self.mb, "_get", return_value=self._recording(releases)):
            found = self.mb.lookup_track_years([{"artist": "Alpha", "title": "Song One"}])
        self.assertIsNone(found[("Alpha", "Song One")])

    def test_the_same_track_is_looked_up_once(self):
        releases = [{"date": "1994-03-01", "title": "Studio Album", "status": "Official",
                     "release-group": {"primary-type": "Album", "secondary-types": None}}]
        with patch.object(self.mb, "_get", return_value=self._recording(releases)) as get:
            self.mb.lookup_track_years([{"artist": "Alpha", "title": "Song One"}])
            self.mb.lookup_track_years([{"artist": "Alpha", "title": "Song One"}])
        self.assertEqual(get.call_count, 1)

    def test_the_number_of_lookups_is_capped(self):
        """Each lookup is a request and MusicBrainz allows one a second, so a
        large group must not turn into minutes of waiting."""
        tracks = [{"artist": "Alpha", "title": f"Song {i}"} for i in range(50)]
        with patch.object(self.mb, "_get", return_value=self._recording([])) as get:
            self.mb.lookup_track_years(tracks, limit=5)
        self.assertEqual(get.call_count, 5)

    def test_a_capped_track_is_undated(self):
        tracks = [{"artist": "Alpha", "title": f"Song {i}"} for i in range(50)]
        with patch.object(self.mb, "_get", return_value=self._recording([])):
            found = self.mb.lookup_track_years(tracks, limit=2)
        self.assertIsNone(found[("Alpha", "Song 49")])

class RecordingTagFillTests(unittest.TestCase):
    def setUp(self):
        from system_manager.organizer import mb_lookup
        self.mb = mb_lookup
        mb_lookup._recording_cache.clear()
        mb_lookup._album_cache.clear()
        mb_lookup._track_cache.clear()

    def test_recording_lookup_fills_artist_album_year(self):
        """A flat folder proposes the title from the filename, and the recording
        lookup answers who recorded it, filling the fields the file is missing."""
        with patch.object(self.mb, "_get", return_value={"recordings": [
            {"title": "Hayat Sana Guzel", "artist-credit": [{"name": "Murat Boz"}],
             "releases": [{"title": "Hayat Sana Güzel", "date": "2010-04-10"}]}]}):
            found = self.mb.lookup_recordings([{"title": "Hayat Sana Guzel"}])
        self.assertEqual(found[("", "Hayat Sana Guzel")]["artist"], "Murat Boz")
        self.assertEqual(found[("", "Hayat Sana Guzel")]["year"], "2010")

class FolderCoverTests(unittest.TestCase):
    def test_refuses_path_outside_music_root(self):
        self.assertIsNone(api._folder_cover("E:\\Windows\\System32"))

    def test_refuses_traversal_out_of_music_root(self):
        self.assertIsNone(api._folder_cover("G:\\Music\\..\\..\\etc"))

if __name__ == "__main__":
    unittest.main()
