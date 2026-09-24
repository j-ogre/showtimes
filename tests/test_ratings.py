import unittest
from pathlib import Path

from src import ratings

FIX = Path(__file__).parent / "fixtures" / "rt"


class RtPage(unittest.TestCase):
    def test_scores_consensus_synopsis(self):
        got = ratings.parse_rt_page((FIX / "m_resident_evil_2026.html").read_text())
        self.assertEqual(got["critics"], 96)
        self.assertTrue(got["consensus"].startswith("Packing unforgettable scares"))
        self.assertTrue(got["synopsis"].startswith("From the mind of visionary filmmaker"))

    def test_page_without_scorecard_raises(self):
        with self.assertRaises(ratings.RatingsError):
            ratings.parse_rt_page("<html></html>")


class ImdbDataset(unittest.TestCase):
    def test_lookup_from_local_file(self):
        import gzip, os, tempfile, time
        path = Path(tempfile.mkdtemp()) / "ratings.tsv.gz"
        with gzip.open(path, "wt") as f:
            f.write("tconst\taverageRating\tnumVotes\ntt1\t7.7\t45024\ntt2\t6.3\t8503\n")
        os.utime(path, (time.time(), time.time()))
        self.assertEqual(ratings.imdb_dataset_ratings({"tt1", "tt9"}, path), {"tt1": "7.7"})


if __name__ == "__main__":
    unittest.main()
