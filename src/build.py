"""Assemble one date's movies, theaters, showtimes and scores."""

import logging
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import amc, fandango, net, ratings
from .cache import Cache
from .resolve import Resolver

WORKERS = 8
FANDANGO_GAP_S = 1.0
SHOWTIMES_MAX_AGE_S = 6 * 3600
# Every other daily run. Scheduled runs start up to an hour late, so the ages
# sit between run gaps: 24h reuses, 48h refetches.
RT_MAX_AGE_S = 44 * 3600
NO_RT = {"critics": None, "consensus": None, "synopsis": None}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    timezone: ZoneInfo
    theaters: tuple[fandango.Theater, ...]
    amc: dict[str, str]
    names: dict[str, str]


def load_config(path: Path) -> Config:
    """Return the Config in `path`.

    Raises ValueError on a missing or invalid timezone, theater URL, AMC
    entry or theater name.
    """
    with path.open("rb") as f:
        data = tomllib.load(f)
    try:
        tz = ZoneInfo(data["timezone"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{path}: timezone must be an IANA name") from exc
    urls = data.get("theaters") or []
    if not urls:
        raise ValueError(f"{path}: theaters is empty")
    theaters = tuple(fandango.theater_from_url(u) for u in urls)
    ids = {t.id for t in theaters}
    pages = {}
    for tid, page in (data.get("amc") or {}).items():
        if tid not in ids:
            raise ValueError(f"{path}: [amc] {tid} is not a theater ID in theaters")
        pages[tid] = amc.theater_page(page)
    names = {}
    for tid, name in (data.get("names") or {}).items():
        if tid not in ids:
            raise ValueError(f"{path}: [names] {tid} is not a theater ID in theaters")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: [names] {tid} must be a non-empty string")
        names[tid] = name.strip()
    return Config(tz, theaters, pages, names)


def build(day: date, config: Config, resolver: Resolver, ratings_path: Path,
          store: Cache, refresh: bool = False) -> dict:
    """Return the page data for `day` as a JSON-serializable dict.

    Showtimes and RT page data are reused from `store` for SHOWTIMES_MAX_AGE_S
    and RT_MAX_AGE_S. IMDb ratings come from IMDb's daily ratings file, kept
    at `ratings_path`. With `refresh`, all three are fetched again.
    `fetched_at` is when the oldest showtimes used were fetched.
    """
    store.prune("showtimes", SHOWTIMES_MAX_AGE_S)
    store.prune("rt", RT_MAX_AGE_S)
    warnings, failed, saved = [], [], []
    merged: dict[str, dict] = {}
    requested = False
    for theater in config.theaters:
        key = f"{theater.id}_{day.isoformat()}"
        entry = None if refresh else store.get("showtimes", key, SHOWTIMES_MAX_AGE_S)
        if entry is None:
            if requested:
                time.sleep(FANDANGO_GAP_S)
            requested = True
            try:
                vm = fandango.fetch_day(theater, day)
            except (net.FetchError, fandango.FandangoError) as exc:
                log.warning("theater %s failed: %s", theater.slug, exc)
                failed.append(theater.slug)
                continue
            entry = store.put("showtimes", key, vm)
        saved.append(entry.saved_at)
        vm = entry.value
        if vm is None:
            continue
        name, movies = fandango.parse(vm)
        amc_page = config.amc.get(theater.id)
        if amc_page:
            link = amc.day_url(amc_page, day)
            for m in movies:
                for s in m["showtimes"]:
                    s["ticket_url"] = link
        for m in movies:
            if not m["showtimes"]:
                continue
            movie = merged.setdefault(m["id"], {"id": m["id"], "title": m["title"],
                                                "theaters": []})
            for field in ("release_date", "runtime", "genres", "poster"):
                if not movie.get(field):
                    movie[field] = m[field]
            movie["theaters"].append({"name": config.names.get(theater.id) or name or theater.slug,
                                      "showtimes": m["showtimes"]})

    movies = sorted(merged.values(), key=lambda m: m["title"].casefold())
    for m in movies:
        m["theaters"].sort(key=lambda t: t["name"].casefold())

    with ThreadPoolExecutor(WORKERS) as pool:
        ids = list(pool.map(
            lambda m: resolver.resolve(m["id"], m["title"], m["release_date"]), movies))
        resolver.save()
        for m, found in zip(movies, ids):
            m["imdb_id"], m["rt_url"] = found["imdb"], found["rt"]

        rt = pool.map(lambda m: _rt(m["rt_url"], warnings, store, refresh), movies)
        ids = {m["imdb_id"] for m in movies if m["imdb_id"]}
        try:
            daily = ratings.imdb_dataset_ratings(ids, ratings_path, refresh)
        except ratings.RatingsError as exc:
            log.warning("%s", exc)
            warnings.append("IMDb's ratings file failed to load.")
            daily = {}
        for m, page in zip(movies, rt):
            m["imdb_rating"] = daily.get(m["imdb_id"])
            m["rt_critics"] = page["critics"]
            m["rt_consensus"], m["synopsis"] = page["consensus"], page["synopsis"]

    return {
        "date": day.isoformat(),
        "fetched_at": datetime.fromtimestamp(min(saved, default=time.time()),
                                             config.timezone).isoformat(timespec="seconds"),
        "movies": movies,
        "failed": failed,
        "no_showtimes": not movies and not failed,
        "warnings": sorted(set(warnings)),
    }


def _rt(url: str | None, warnings: list, store: Cache, refresh: bool) -> dict:
    if not url:
        return NO_RT
    key = urlparse(url).path
    entry = None if refresh else store.get("rt", key, RT_MAX_AGE_S)
    if entry is not None:
        return entry.value
    try:
        page = ratings.rt_scores(url)
    except (net.FetchError, ratings.RatingsError) as exc:
        log.warning("RT %s: %s", url, exc)
        warnings.append("Some Rotten Tomatoes scores failed to load.")
        return NO_RT
    store.put("rt", key, page)
    return page
