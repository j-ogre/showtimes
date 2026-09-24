"""Match Fandango movies to IMDb IDs and Rotten Tomatoes pages."""

import html
import json
import logging
import re
import threading
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlencode

from . import net

IMDB_SUGGEST = "https://v3.sg.media-imdb.com/suggestion/x/{q}.json"
RT_SEARCH = "https://www.rottentomatoes.com/search?"
IMDB_TYPES = {"movie", "tvMovie", "video", "tvSpecial"}
YEAR_SUFFIX = re.compile(r"\s*\((\d{4})\)\s*$")
# Event branding Fandango appends to re-releases; the film's own year is unknown.
EVENT = re.compile(
    r"\s*(-\s*studio ghibli fest \d{4}|\d+(st|nd|rd|th)\s+anniversary(\s+remastered)?"
    r"|-?\s*\b4k\b|:?\s*\bencore\b|\bfan event screenings?\b)", re.I)
RT_ROW = re.compile(r"<search-page-media-row([^>]*)>(.*?)</search-page-media-row>", re.S)
RT_HREF = re.compile(r'href="(https://www\.rottentomatoes\.com/m/[^"?#]+)"')
RT_NAME = re.compile(r'data-qa="info-name"[^>]*>\s*([^<]+?)\s*<')
RT_YEAR = re.compile(r'release-year="(\d{4})"')

log = logging.getLogger(__name__)


def search_title(title: str) -> str:
    """Return a Fandango title without its trailing "(YYYY)" or event branding."""
    return re.sub(r"\s+", " ", EVENT.sub(" ", YEAR_SUFFIX.sub("", title))).strip(" -:")


def title_year(title: str, release_date: str | None) -> int | None:
    """Return the year from a Fandango "(YYYY)" suffix, else the release date's year.

    Returns None for event re-releases, whose Fandango year is the event's.
    """
    if EVENT.search(YEAR_SUFFIX.sub("", title)):
        return None
    m = YEAR_SUFFIX.search(title)
    if m:
        return int(m.group(1))
    if release_date and re.match(r"\d{4}", release_date):
        return int(release_date[:4])
    return None


def title_key(title: str) -> str:
    """Return `title` casefolded, without accents or punctuation, for exact comparison."""
    s = unicodedata.normalize("NFKD", title)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def pick(candidates: list[tuple[str, int | None, str]], title: str,
         year: int | None) -> tuple[str | None, int]:
    """Return (value, number of matches) for (title, year, value) candidates.

    A candidate matches when its title key equals `title`'s and, if both
    years are known, they are within one of each other. The value is the
    first match in the source's own ranking, or None when nothing matches.
    """
    key = title_key(title)
    hits = list(dict.fromkeys(
        value for name, y, value in candidates
        if title_key(name) == key
        and (year is None or y is None or abs(y - year) <= 1)
    ))
    return (hits[0] if hits else None), len(hits)


def imdb_candidates(payload: dict) -> list[tuple[str, int | None, str]]:
    """Return (title, year, tt ID) for each film in an IMDb suggestion payload."""
    return [
        (d.get("l") or "", d.get("y"), d["id"])
        for d in payload.get("d") or []
        if d.get("qid") in IMDB_TYPES and str(d.get("id", "")).startswith("tt")
    ]


def rt_candidates(page: str) -> list[tuple[str, int | None, str]]:
    """Return (title, year, movie URL) for each movie row in an RT search page."""
    out = []
    for attrs, body in RT_ROW.findall(page):
        href, name = RT_HREF.search(body), RT_NAME.search(body)
        if href and name:
            year = RT_YEAR.search(attrs)
            out.append((html.unescape(name.group(1)),
                        int(year.group(1)) if year else None, href.group(1)))
    return out


def search_imdb(query: str) -> list[tuple[str, int | None, str]]:
    """Return IMDb suggestion candidates for `query`. Raises net.FetchError."""
    body = net.fetch(IMDB_SUGGEST.format(q=quote(query.lower(), safe="")),
                     label="imdb-suggest.json")
    return imdb_candidates(json.loads(body))


def search_rt(query: str) -> list[tuple[str, int | None, str]]:
    """Return RT search candidates for `query`. Raises net.FetchError."""
    body = net.fetch(RT_SEARCH + urlencode({"search": query}), label="rt-search.html")
    return rt_candidates(body.decode("utf-8", "replace"))


class Resolver:
    """Resolve Fandango movie IDs through overrides, then the cache, then search."""

    SOURCES = {"imdb": search_imdb, "rt": search_rt}

    def __init__(self, overrides_path: Path, cache_path: Path):
        self.overrides = self._load_overrides(overrides_path)
        self.cache_path = cache_path
        self.cache = self._load_cache(cache_path)
        self._lock = threading.Lock()

    def resolve(self, fandango_id: str, title: str, release_date: str | None) -> dict:
        """Return {"imdb": tt ID or None, "rt": URL or None} for one movie."""
        out = {}
        query, year = search_title(title), title_year(title, release_date)
        for field, search in self.SOURCES.items():
            override = self.overrides.get(fandango_id, {}).get(field)
            with self._lock:
                cached = self.cache.get(fandango_id, {}).get(field)
            if override:
                out[field], how = override, "override"
            elif cached:
                out[field], how = cached, "cache"
            elif not query:
                out[field], how = None, "unmatched (empty title)"
            else:
                try:
                    out[field], n = pick(search(query), query, year)
                    how = f"search ({n} matches)"
                except (net.FetchError, ValueError) as exc:
                    out[field], how = None, f"search failed: {exc}"
                if out[field]:
                    with self._lock:
                        entry = self.cache.setdefault(fandango_id, {"title": title})
                        entry[field] = out[field]
            log.info("%s %s [%s] %r (%s): %s -> %s",
                     field, how, fandango_id, title, year, query, out[field])
        return out

    def save(self) -> None:
        """Write the cache file."""
        with self._lock:
            data = json.dumps(self.cache, indent=1, sort_keys=True, ensure_ascii=False)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.cache_path)

    @staticmethod
    def _load_overrides(path: Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        with path.open("rb") as f:
            data = tomllib.load(f)
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    @staticmethod
    def _load_cache(path: Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("ignoring unreadable cache %s", path)
            return {}
