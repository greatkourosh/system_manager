"""Unit tests for video_catalog.main_item (one card per main item grouping)."""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_catalog import main_item


class MainItemTests(unittest.TestCase):
    def test_windows_movie_root(self):
        self.assertEqual(
            main_item(r"G:\Movies\1981 - Body Heat - Crime, Drama 7.4 96\Featurettes\Deleted Scenes\1.mkv"),
            ("movies", "1981 - Body Heat - Crime, Drama 7.4 96",
             r"G:\Movies\1981 - Body Heat - Crime, Drama 7.4 96"))

    def test_windows_serial_season(self):
        self.assertEqual(
            main_item(r"G:\Serials\Black Mirror S07 of 07+ - Sci-fi, Drama 8.5 84\S01\Ep.mkv"),
            ("serials", "Black Mirror S07 of 07+ - Sci-fi, Drama 8.5 84",
             r"G:\Serials\Black Mirror S07 of 07+ - Sci-fi, Drama 8.5 84"))

    def test_posix_path(self):
        self.assertEqual(
            main_item("/mnt/Videos/Yanni/Yanni - Bonus/show.avi"),
            ("videos", "Yanni", "/mnt/Videos/Yanni"))

    def test_video_at_root_level(self):
        # file directly under the section root: no main folder, not a card
        self.assertIsNone(main_item(r"G:\Videos\loose.mkv"))

    def test_no_section_marker(self):
        self.assertIsNone(main_item(r"F:\Games\Steam\common\game\video.webm"))


if __name__ == "__main__":
    unittest.main()
