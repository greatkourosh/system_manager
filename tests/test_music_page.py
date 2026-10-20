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

    def test_fix_untagged_rejects_group_with_no_untagged_tracks(self):
        self.assertEqual(self.post({"key": "Beta", "mode": "artist"}).status_code, 400)

class FolderCoverTests(unittest.TestCase):
    def test_refuses_path_outside_music_root(self):
        self.assertIsNone(api._folder_cover("E:\\Windows\\System32"))

    def test_refuses_traversal_out_of_music_root(self):
        self.assertIsNone(api._folder_cover("G:\\Music\\..\\..\\etc"))

if __name__ == "__main__":
    unittest.main()
