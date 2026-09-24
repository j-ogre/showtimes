import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import resolve
from src.resolve import imdb_candidates, pick, rt_candidates

FIX = Path(__file__).parent / "fixtures"


def imdb(name):
    return imdb_candidates(json.loads((FIX / "imdb" / f"{name}.json").read_text()))


def rt(name):
    return rt_candidates((FIX / "rt" / f"{name}.html").read_text())


class Titles(unittest.TestCase):
    def test_year_suffix(self):
        self.assertEqual(resolve.search_title("Resident Evil (2026)"), "Resident Evil")
        self.assertEqual(resolve.title_year("Resident Evil (2026)", "2026-09-25"), 2026)

    def test_year_from_release_date(self):
        self.assertEqual(resolve.title_year("Akira", "1988-01-01"), 1988)
        self.assertIsNone(resolve.title_year("Boogie Nights", None))

    def test_event_branding_is_stripped_and_year_dropped(self):
        title = "Ghost in the Shell 30th Anniversary - 4K (2026)"
        self.assertEqual(resolve.search_title(title), "Ghost in the Shell")
        self.assertIsNone(resolve.title_year(title, "2026-09-18"))
        self.assertEqual(resolve.search_title("Avengers Endgame: Encore (2026)"), "Avengers Endgame")

    def test_key_ignores_case_accents_punctuation(self):
        self.assertEqual(resolve.title_key("NO LIMBS, NO LIMITS: The NickV Story"),
                         resolve.title_key("No Limbs, No Limits: The NickV Story"))
        self.assertEqual(resolve.title_key("Beware Boiúna"), resolve.title_key("Beware Boiuna"))


class ImdbMatching(unittest.TestCase):
    def test_original_not_confused_with_sequel(self):
        self.assertEqual(pick(imdb("alien"), "Alien", 1979), ("tt0078748", 1))

    def test_remake_separated_by_year(self):
        self.assertEqual(pick(imdb("resident_evil"), "Resident Evil", 2026), ("tt35538033", 1))

    def test_same_name_without_year_takes_top_ranked(self):
        self.assertEqual(pick(imdb("resident_evil"), "Resident Evil", None), ("tt35538033", 2))

    def test_rerelease_uses_original_year(self):
        self.assertEqual(pick(imdb("akira"), "Akira", 1988), ("tt0094625", 1))

    def test_same_name_same_year_takes_top_ranked(self):
        self.assertEqual(pick(imdb("the_odyssey"), "The Odyssey", 2026), ("tt33764258", 3))


class RtMatching(unittest.TestCase):
    def test_remake_separated_by_year(self):
        url, _ = pick(rt("search_resident_evil"), "Resident Evil", 2026)
        self.assertEqual(url, "https://www.rottentomatoes.com/m/resident_evil_2026")

    def test_same_name_same_year_takes_top_ranked(self):
        url, n = pick(rt("search_the_odyssey"), "The Odyssey", 2026)
        self.assertEqual(url, "https://www.rottentomatoes.com/m/the_odyssey_2026")
        self.assertGreater(n, 1)


class ResolverOrder(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "overrides.toml").write_text('[100]\nimdb = "tt0000001"\n')
        self.resolver = resolve.Resolver(self.dir / "overrides.toml", self.dir / "ids.json")

    def test_override_wins_and_search_fills_the_rest(self):
        search = mock.Mock(return_value=[("Film", 2026, "https://www.rottentomatoes.com/m/film")])
        with mock.patch.dict(resolve.Resolver.SOURCES, {"imdb": mock.Mock(), "rt": search}):
            found = self.resolver.resolve("100", "Film (2026)", None)
        self.assertEqual(found, {"imdb": "tt0000001",
                                 "rt": "https://www.rottentomatoes.com/m/film"})
        search.assert_called_once_with("Film")

    def test_only_matches_are_cached(self):
        with mock.patch.dict(resolve.Resolver.SOURCES,
                             {"imdb": mock.Mock(return_value=[]), "rt": mock.Mock(return_value=[])}):
            self.resolver.resolve("200", "Nothing (2026)", None)
        self.resolver.save()
        self.assertEqual(json.loads((self.dir / "ids.json").read_text()), {})

    def test_cache_is_used_before_search(self):
        (self.dir / "ids.json").write_text(json.dumps({"300": {"imdb": "tt3", "rt": "u"}}))
        resolver = resolve.Resolver(self.dir / "overrides.toml", self.dir / "ids.json")
        with mock.patch.dict(resolve.Resolver.SOURCES,
                             {"imdb": mock.Mock(side_effect=AssertionError),
                              "rt": mock.Mock(side_effect=AssertionError)}):
            self.assertEqual(resolver.resolve("300", "X", None), {"imdb": "tt3", "rt": "u"})


if __name__ == "__main__":
    unittest.main()
