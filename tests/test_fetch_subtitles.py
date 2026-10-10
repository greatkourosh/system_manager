import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch_subtitles import _excluded


class ExclusionTests(unittest.TestCase):
    """_excluded decides what never gets fetched, so it must match a whole path
    component. A substring match reaches real titles."""

    def test_game_assets_are_excluded(self):
        # These match on the dota/steamapps component, not on the leaf folder —
        # "heroes" alone is also a perfectly ordinary serial folder name.
        base = r"F:\Games\Steam\steamapps\common\dota 2 beta\game\dota\panorama\videos"
        for folder in ("heroes", "events", "portraits", "healthbar_deaths", "login"):
            self.assertTrue(
                _excluded(base + "\\" + folder + r"\clip.webm"),
                "%s should be excluded" % folder)

    def test_a_serial_called_heroes_is_not_a_game_asset(self):
        self.assertFalse(
            _excluded(r"G:\Serials\Heroes\Season 1\Heroes S01E01.mkv"))

    def test_real_titles_survive(self):
        # Both regressed while this was a substring match: "behind" reached
        # "Behind Her Eyes", "sample" reached the episode below.
        for title in ("Leave the World Behind", "Behind Her Eyes", "Heroes",
                      "Benedetta 2021 French 1080p BluRay"):
            self.assertFalse(_excluded(r"G:\Movies\%s\movie.mkv" % title), title)

    def test_extras_are_excluded_as_whole_components_only(self):
        self.assertTrue(_excluded(r"G:\Movies\Some Movie\Featurettes\clip.mkv"))
        self.assertTrue(_excluded(r"G:\Movies\Some Movie\Extras\clip.mkv"))
        # The filename is a component too, so an "extras"-named episode in a
        # normal folder is still a real episode.
        self.assertFalse(
            _excluded(r"G:\Serials\Show\Season 1\s01e02 - The Extras.mkv"))


if __name__ == "__main__":
    unittest.main()