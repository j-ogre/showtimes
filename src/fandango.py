"""Fetch and normalize one day of Fandango showtimes for one theater."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse

from . import net

API = "https://www.fandango.com/napi/theaterMovieShowtimes/{tid}?startDate={day}"
TAG_ORDER = ("IMAX 70MM", "IMAX", "Dolby", "RPX", "ScreenX", "4DX", "3D")

log = logging.getLogger(__name__)


class FandangoError(Exception):
    """Raised when Fandango returns something other than a showtimes payload."""


@dataclass(frozen=True)
class Theater:
    url: str
    slug: str
    id: str


def theater_from_url(url: str) -> Theater:
    """Return the Theater for a Fandango theater-page URL.

    Raises ValueError if the URL is not a Fandango theater page.
    """
    parts = urlparse(url)
    m = re.fullmatch(r"/([a-z0-9-]+)/theater-page/?", parts.path)
    if not parts.netloc.endswith("fandango.com") or not m or "-" not in m.group(1):
        raise ValueError(f"not a Fandango theater-page URL: {url}")
    slug = m.group(1)
    return Theater(url=url, slug=slug, id=slug.rsplit("-", 1)[1])


def fetch_day(theater: Theater, day: date) -> dict | None:
    """Return the payload's viewModel, or None if nothing is published for `day`.

    Raises net.FetchError or FandangoError.
    """
    body = net.fetch(
        API.format(tid=theater.id, day=day.isoformat()),
        # Fandango answers 403 without a fandango.com Referer.
        headers={"Referer": theater.url, "Accept": "application/json"},
        label=f"fandango-{theater.id}-{day}.json",
    )
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise FandangoError(f"{theater.slug}: invalid JSON") from exc
    return view_model(data, theater.slug)


def view_model(data: dict, slug: str = "") -> dict | None:
    """Return the viewModel of a decoded payload, or None for an unpublished date.

    Raises FandangoError for an error payload such as an unknown theater ID.
    """
    if isinstance(data, dict):
        if isinstance(data.get("viewModel"), dict):
            return data["viewModel"]
        if data == {"date": None}:
            return None
    raise FandangoError(f"{slug}: unexpected payload {str(data)[:80]}")


def parse(vm: dict) -> tuple[str, list[dict]]:
    """Return (theater name, movies) from a viewModel.

    Each movie is {id, title, release_date, runtime, genres, poster, showtimes}.
    Each showtime is
    {label, at, tags, status, ticket_url}, where `at` is the local start time
    as "YYYY-MM-DDTHH:MM".
    """
    name = ((vm.get("theater") or {}).get("details") or {}).get("name") or ""
    movies = []
    for m in vm.get("movies") or []:
        title = m.get("title") or ""
        shows = []
        for variant in m.get("variants") or []:
            for group in variant.get("amenityGroups") or []:
                group_tags = _tags(a.get("name") for a in group.get("amenities") or [])
                for s in group.get("showtimes") or []:
                    show = _showtime(s, group_tags)
                    if show is None:
                        log.warning("%s: %s: skipped showtime without a time: %s",
                                    name, title, s.get("date"))
                    else:
                        shows.append(show)
        shows.sort(key=lambda s: s["at"])
        movies.append({
            "id": str(m.get("id") or title),
            "title": title,
            "release_date": m.get("releaseDate"),
            "runtime": m.get("runtime") or None,
            "genres": list(m.get("genres") or []),
            "poster": ((m.get("poster") or {}).get("size") or {}).get("200"),
            "showtimes": shows,
        })
    return name, movies


def _showtime(s: dict, group_tags: set[str]) -> dict | None:
    try:
        at = datetime.strptime(s.get("ticketingDate") or "", "%Y-%m-%d+%H:%M")
    except ValueError:
        return None
    status = s.get("type") or "available"
    if s.get("expired"):
        status = "pastshowtime"
    elif s.get("isSoldOut"):
        status = "soldout"
    tags = group_tags | _tags(f.get("filterName") for f in s.get("filmFormat") or [])
    return {
        "label": s.get("date") or at.strftime("%H:%M"),
        "at": at.strftime("%Y-%m-%dT%H:%M"),
        "tags": _ordered(tags),
        "status": status,
        "ticket_url": s.get("ticketingJumpPageURL"),
    }


def _tags(names) -> set[str]:
    tags = set()
    for name in names:
        n = (name or "").upper()
        if "IMAX" in n:
            tags.add("IMAX 70MM" if "70MM" in n else "IMAX")
        if "DOLBY CINEMA" in n:
            tags.add("Dolby")
        if "RPX" in n:
            tags.add("RPX")
        if "SCREENX" in n:
            tags.add("ScreenX")
        if "4DX" in n:
            tags.add("4DX")
        if re.search(r"\b3D\b", n):
            tags.add("3D")
    return tags


def _ordered(tags: set[str]) -> list[str]:
    if "IMAX 70MM" in tags:
        tags = tags - {"IMAX"}
    return [t for t in TAG_ORDER if t in tags]
