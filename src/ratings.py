"""IMDb ratings from IMDb's daily ratings file and RT scores from RT movie pages."""

import gzip
import html
import json
import logging
import re
import time
from pathlib import Path

from . import net

IMDB_DATASET = "https://datasets.imdbws.com/title.ratings.tsv.gz"
# IMDb regenerates the file once a day.
DATASET_MAX_AGE_S = 24 * 3600
RT_SCORECARD = re.compile(
    r'<script[^>]*id="media-scorecard-json"[^>]*>(.*?)</script>', re.S)
RT_CONSENSUS = re.compile(r'id="critics-consensus"[^>]*>.*?<p>(.*?)</p>', re.S)
RT_SYNOPSIS = re.compile(r'data-qa="synopsis-value"[^>]*>(.*?)</rt-text>', re.S)


class RatingsError(Exception):
    """Raised when a ratings source returns an error or an unreadable page."""


log = logging.getLogger(__name__)


def imdb_dataset_ratings(ids: set[str], path: Path, refresh: bool = False) -> dict[str, str]:
    """Return {tt ID: rating} for `ids` found in IMDb's daily ratings file.

    Downloads the file to `path` when it is missing, over a day old, or
    `refresh` is true. An older copy is used when the download fails.
    Raises RatingsError when no copy is available.
    """
    fresh = (not refresh and path.exists()
             and time.time() - path.stat().st_mtime < DATASET_MAX_AGE_S)
    if not fresh:
        try:
            body = net.fetch(IMDB_DATASET, label="imdb-ratings.tsv.gz", timeout=120)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        except (net.FetchError, OSError) as exc:
            if not path.exists():
                raise RatingsError(f"IMDb ratings file: {exc}") from exc
            log.warning("using stale IMDb ratings file: %s", exc)
    found = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                tt, _, rest = line.partition("\t")
                if tt in ids:
                    found[tt] = rest.split("\t", 1)[0]
    except (OSError, EOFError) as exc:
        raise RatingsError(f"IMDb ratings file unreadable: {exc}") from exc
    return found


def rt_scores(url: str) -> dict:
    """Return parse_rt_page's result for the RT movie page at `url`.

    Raises net.FetchError or RatingsError.
    """
    body = net.fetch(url, label="rt-movie.html")
    return parse_rt_page(body.decode("utf-8", "replace"))


def parse_rt_page(page: str) -> dict:
    """Return {critics, consensus, synopsis} from an RT movie page.

    `critics` is an int and the text fields are strings; any may be None.
    Raises RatingsError when the scorecard is missing or unreadable.
    """
    m = RT_SCORECARD.search(page)
    if not m:
        raise RatingsError("RT page has no scorecard")
    try:
        card = json.loads(m.group(1))
    except ValueError as exc:
        raise RatingsError("RT scorecard is not valid JSON") from exc
    return {
        "critics": _score(card.get("criticsScore")),
        "consensus": _text(RT_CONSENSUS.search(page)),
        "synopsis": _text(RT_SYNOPSIS.search(page)),
    }


def _text(m: re.Match | None) -> str | None:
    if not m:
        return None
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()
    return text or None


def _score(block: dict | None) -> int | None:
    score = (block or {}).get("score")
    try:
        return int(score)
    except (TypeError, ValueError):
        return None
