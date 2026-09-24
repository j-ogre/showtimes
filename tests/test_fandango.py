import json
import unittest
from pathlib import Path

from src import fandango

FIX = Path(__file__).parent / "fixtures" / "fandango"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def parsed(name):
    return fandango.parse(fandango.view_model(load(name)))


def movie(movies, title):
    return next(m for m in movies if m["title"] == title)


class TheaterUrl(unittest.TestCase):
    def test_id_is_last_slug_segment(self):
        t = fandango.theater_from_url(
            "https://www.fandango.com/universal-cinema-an-amc-theatre-aaawx/theater-page")
        self.assertEqual(t.id, "aaawx")
        self.assertEqual(t.slug, "universal-cinema-an-amc-theatre-aaawx")

    def test_rejects_other_urls(self):
        for url in ("https://www.fandango.com/aaawx", "https://example.com/x-aaawx/theater-page"):
            with self.assertRaises(ValueError):
                fandango.theater_from_url(url)


class Payloads(unittest.TestCase):
    def test_unpublished_date_is_none(self):
        self.assertIsNone(fandango.view_model(load("not_published")))

    def test_error_payload_raises(self):
        with self.assertRaises(fandango.FandangoError):
            fandango.view_model(load("bad_theater"))

    def test_movie_metadata(self):
        m = movie(parsed("aaqzz_2026-09-26")[1], "Princess Mononoke - Studio Ghibli Fest 2026")
        self.assertEqual((m["runtime"], m["release_date"]), (138, "2026-09-26"))
        self.assertEqual(m["genres"], ["Action/Adventure", "Animated", "Sci-Fi/Fantasy"])
        self.assertTrue(m["poster"].startswith("https://images.fandango.com/"))

    def test_theater_name(self):
        self.assertEqual(parsed("aaawx_2026-09-26")[0], "Universal Cinema, an AMC Theatre")


class Showtimes(unittest.TestCase):
    def test_after_midnight_show_sorts_last_with_next_date(self):
        _, movies = parsed("aaore_2026-09-26")
        last = movie(movies, "Primetime (2026)")["showtimes"][-1]
        self.assertEqual((last["label"], last["at"]), ("12:00a", "2026-09-27T00:00"))

    def test_tags_come_from_formats_and_amenities(self):
        _, movies = parsed("aaqzz_2026-09-26")
        tags = {t for m in movies for s in m["showtimes"] for t in s["tags"]}
        self.assertEqual(tags, {"IMAX", "Dolby", "ScreenX"})

    def test_imax_70mm_replaces_imax(self):
        _, movies = parsed("aaawx_2026-09-26")
        tag_sets = {tuple(s["tags"]) for m in movies for s in m["showtimes"]}
        self.assertIn(("IMAX 70MM",), tag_sets)
        self.assertFalse(any("IMAX" in t and "IMAX 70MM" in t for t in tag_sets))

    def test_past_showtimes_are_kept_and_flagged(self):
        _, movies = parsed("aaore_2026-09-24")
        statuses = [s["status"] for m in movies for s in m["showtimes"]]
        self.assertEqual(statuses.count("pastshowtime"), 15)

    def test_sold_out_is_flagged(self):
        _, movies = parsed("aaqcr_2026-09-26")
        shows = movie(movies, "Spider-Man: Brand New Day (2026)")["showtimes"]
        self.assertIn(("10:50p", "soldout"), [(s["label"], s["status"]) for s in shows])


if __name__ == "__main__":
    unittest.main()
