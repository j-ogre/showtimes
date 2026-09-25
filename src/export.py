"""Write the page and a range of days as static files for hosting."""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .build import build, load_config
from .cache import Cache
from .main import ROOT
from .resolve import Resolver
from .server import page_html

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Write <out>/index.html and <out>/data/<date>.json. Returns the process exit code.

    The exit code is 1 when no day has showtimes from at least one theater.
    """
    parser = argparse.ArgumentParser(prog="python -m src.export",
                                     description="Write the page and showtimes as static files.")
    parser.add_argument("out", type=Path, help="output folder")
    parser.add_argument("--days", type=int, default=8, help="days to write, starting today")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(ROOT / "config.toml")
        resolver = Resolver(ROOT / "overrides.toml", ROOT / "cache" / "ids.json")
        data_dir = args.out / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (args.out / "index.html").write_text(page_html(config, True, args.days), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    store = Cache(ROOT / "cache")
    today = datetime.now(config.timezone).date()
    days = [today + timedelta(days=n) for n in range(args.days)]
    loaded = 0
    for day in days:
        try:
            data = build(day, config, resolver, ROOT / "cache" / "title.ratings.tsv.gz", store)
        except Exception:
            log.exception("build failed for %s", day)
            continue
        name = f"{day.isoformat()}.json"
        try:
            (data_dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if len(data["failed"]) < len(config.theaters):
            loaded += 1
        log.info("%s: %d movies, %d theaters failed", day, len(data["movies"]), len(data["failed"]))

    # A day whose build failed keeps its earlier file; days outside the range are removed.
    keep = {f"{day.isoformat()}.json" for day in days}
    for old in data_dir.glob("*.json"):
        if old.name not in keep:
            old.unlink(missing_ok=True)
    return 0 if loaded else 1


if __name__ == "__main__":
    sys.exit(main())
