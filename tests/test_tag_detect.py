"""Tests for the music tag detector.

Two things have to hold at once. A layout the path actually determines
(``Music/<artist>/<album>/track``) must still be read, and a layout that
determines nothing must produce nothing -- an empty tag is re-auditable,
a guessed one is not. The second group covers genre needles, which used to
match anywhere in a path: "ney" inside "jour|ney" and "tar " inside "so|tar"
tagged English tracks as Persian Traditional.
"""
import unittest

from system_manager.organizer import tag_detect as t


def detect(path, mtime=None):
    return {k: v["value"] for k, v in t.detect_from_path(path, mtime).items()}


class NestedLayout(unittest.TestCase):
    ARTIST = "G:\\Music\\Ebi\\1976-Nazi Nazkon\\01 - Nazi Naz Kon.mp3"

    def test_reads_artist_album_year_from_folder_names(self):
        got = detect(self.ARTIST)
        self.assertEqual(got["artist"], "Ebi")
        self.assertEqual(got["album"], "1976-Nazi Nazkon")
        self.assertEqual(got["year"], "1976")
        self.assertEqual(got["title"], "Nazi Naz Kon")

    def test_artist_is_high_confidence_because_the_folder_is_evidence(self):
        self.assertEqual(t.detect_from_path(self.ARTIST)["artist"]["confidence"], "high")


class FlatLayout(unittest.TestCase):
    """`Music/vMusic/001) Artist - Title.mp3` -- one level below Music.

    There is no artist folder here, so the folder name must never be read
    as a tag ("vMusic" into every track). But the filename itself carries
    evidence when it follows "Artist - Title", so a flat folder proposes
    what the filename actually says -- at med confidence, since a filename
    can be renamed by anyone -- and stays silent when it doesn't.
    """

    def test_reads_artist_and_title_from_a_split_filename(self):
        got = detect("G:\\Music\\vMusic\\001) Aija Alsina - Awakening.mp3")
        self.assertEqual(got["artist"], "Aija Alsina")
        self.assertEqual(got["title"], "Awakening")
        self.assertEqual(t.detect_from_path("G:\\Music\\vMusic\\001) Aija Alsina - Awakening.mp3")["artist"]["confidence"], "med")

    def test_a_filename_without_a_split_gives_only_a_title(self):
        got = detect("G:\\Music\\vMusic\\01) Just A Song.mp3")
        self.assertNotIn("artist", got)
        self.assertEqual(got.get("title"), "Just A Song")

    def test_the_folder_name_is_never_the_artist(self):
        got = detect("G:\\Music\\vMusic\\001) Aija Alsina - Awakening.mp3")
        self.assertNotEqual(got.get("artist"), "vMusic")

    def test_still_proposes_a_matched_genre(self):
        got = detect("G:\\Music\\vMusic\\01) Bach - Goldberg Variations.mp3")
        self.assertEqual(got.get("genre"), "International Classical")


class NoFallback(unittest.TestCase):
    def test_unmatched_path_gets_no_genre_at_all(self):
        self.assertIsNone(t.detect_genre("G:\\Music\\Something Unheard Of\\track.mp3"))

    def test_no_unknown_placeholder_reaches_the_plan(self):
        for field in ("genre", "artist", "title"):
            self.assertNotIn("Unknown", str(detect("G:\\Music\\vMusic\\001) X - Y.mp3")))


class NeedleBoundaries(unittest.TestCase):
    def test_short_needle_does_not_match_inside_a_word(self):
        self.assertIsNone(t.detect_genre("G:\\Music\\01_Healing Harps - A Peaceful Journey.mp3"))

    def test_short_needle_with_trailing_space_does_not_match_inside_a_word(self):
        # "so|tar" -- the trailing space in the needle is what made it match
        # mid-word, so only the word-boundary form rejects this.
        self.assertIsNone(t.detect_genre(
            "G:\\Music\\01_Edy Hafler - Killing Me Softly With His Song Guitar Solo.mp3"))

    def test_the_same_needle_still_matches_as_a_whole_word(self):
        self.assertEqual(t.detect_genre("G:\\Music\\Tar - Traditional Set\\01.mp3"),
                         "Persian Traditional")

    def test_genuine_persian_needle_still_matches(self):
        self.assertEqual(t.detect_genre("G:\\Music\\Ney - Persian Instrument\\01.mp3"),
                         "Persian Traditional")

    def test_path_needle_with_its_own_boundaries_still_matches(self):
        self.assertEqual(t.detect_genre("F:\\Games\\Steam\\sounds\\x.wav"), "Game Soundtrack")


if __name__ == "__main__":
    unittest.main()
