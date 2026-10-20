"""The music tags page can limit its folder list to folders that still need fixes.

A folder with zero incomplete/error/unreadable files is noise when fixing tags,
so ``?needs_only=1`` must drop it. The filter has to run before the 60-folder
cap, or a clean folder could push a dirty one off the list.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from system_manager import create_app
from system_manager.organizer import api

AUDIT = {
    "total_files": 3,
    "status_counts": {},
    "missing_field_counts": {},
    "files_needing_fix": 1,
    "problems": [],
    "per_folder": {
        "Dirty": {"files": 2, "no_tags": 1, "bytes": 10},
        "Clean": {"files": 1, "no_tags": 0, "bytes": 5},
    },
}

class NeedsOnlyFilter(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix="sm-music-tags-")
        with open(os.path.join(self.data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump(AUDIT, f)
        patcher = patch.object(api, "DATA", self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        plan = patch.object(api, "TAG_PLAN_FILE", os.path.join(self.data, "tag_plan.json"))
        plan.start()
        self.addCleanup(plan.stop)
        app = create_app({"DISABLE_AUTH": 1, "TESTING": True,
                          "AUDIT_PATH": os.path.join(self.data, "actions.db")})
        self.client = app.test_client()

    def folder_options(self, query=""):
        html = self.client.get("/organizer/music/tags" + query).get_data(as_text=True)
        return sorted(v for v in ("Dirty", "Clean") if f'value="{v}"' in html)

    def test_default_lists_only_folders_needing_fixes(self):
        self.assertEqual(self.folder_options(), ["Dirty"])

    def test_needs_only_0_lists_every_folder(self):
        self.assertEqual(self.folder_options("?needs_only=0"), ["Clean", "Dirty"])

    def test_needs_only_drops_folders_with_nothing_to_fix(self):
        self.assertEqual(self.folder_options("?needs_only=1"), ["Dirty"])

    def test_needs_only_is_not_capped_at_60_folders(self):
        audit = dict(AUDIT, per_folder={f"Dirty{i:03}": {"files": 1, "no_tags": 1, "bytes": 1}
                                        for i in range(75)})
        with open(os.path.join(self.data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump(audit, f)
        html = self.client.get("/organizer/music/tags?needs_only=1").get_data(as_text=True)
        self.assertEqual(html.count('value="Dirty'), 75)
        html = self.client.get("/organizer/music/tags?needs_only=0").get_data(as_text=True)
        self.assertEqual(html.count('value="Dirty'), 60)

    def test_apply_route_dry_run_writes_nothing(self):
        fix_list = os.path.join(self.data, "tag_fix_list.json")
        with open(fix_list, "w", encoding="utf-8") as f:
            json.dump({"entries": [{"path": "/nonexistent/x.mp3", "tags": {"year": "2020"}, "overwrites": []}]}, f)
        with patch.object(api, "TAG_FIX_LIST", fix_list):
            r = self.client.post("/organizer/api/tags/apply", json={"dry": True})
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["dry"])
        self.assertIn("RUN END", body["summary"])

    def test_bulk_accept_filtered_covers_every_page_not_just_visible_rows(self):
        problems = [{"path": f"G:\\Music\\Moein\\a{i}.mp3", "status": "incomplete", "missing": ["year"]}
                    for i in range(60)]
        problems.append({"path": "G:\\Music\\Other\\b.mp3", "status": "incomplete", "missing": ["year"]})
        with open(os.path.join(self.data, "music_tag_audit.json"), "w", encoding="utf-8") as f:
            json.dump(dict(AUDIT, problems=problems), f)
        entries = {p["path"]: {"accepted": False, "fields": {}} for p in problems}
        with open(os.path.join(self.data, "tag_plan.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "updated": None, "entries": entries}, f)
        r = self.client.post("/organizer/api/tags/bulk",
                             json={"mode": "accept", "filters": {"folder": "Moein", "state": "all", "missing": "all", "q": ""}})
        self.assertEqual(r.get_json()["updated"], 60)
        with open(os.path.join(self.data, "tag_plan.json"), encoding="utf-8") as f:
            saved = json.load(f)["entries"]
        self.assertTrue(all(v["accepted"] for k, v in saved.items() if "Moein" in k))
        self.assertFalse(saved["G:\\Music\\Other\\b.mp3"]["accepted"])



if __name__ == "__main__":
    unittest.main()
