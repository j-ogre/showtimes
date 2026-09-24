"""Start the local showtimes server and open it in the browser."""

import argparse
import logging
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from . import net
from .build import load_config
from .cache import Cache
from .resolve import Resolver
from .server import make_server

ROOT = Path(__file__).resolve().parent.parent
HOST, PORT = "127.0.0.1", 8000


def main(argv: list[str] | None = None) -> int:
    """Run the server until interrupted. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="python -m src.main",
                                     description="Local showtimes and ratings page.")
    parser.add_argument("--dump", action="store_true",
                        help="save raw responses under dumps/")
    parser.add_argument("--clear-cache", action="store_true",
                        help="delete cache/ids.json before starting")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cache = ROOT / "cache" / "ids.json"
    if args.clear_cache:
        cache.unlink(missing_ok=True)
    if args.dump:
        net.enable_dump(ROOT / "dumps" / datetime.now().strftime("%Y%m%d-%H%M%S"))

    try:
        config = load_config(ROOT / "config.toml")
        resolver = Resolver(ROOT / "overrides.toml", cache)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        server = make_server(HOST, PORT, config, resolver,
                             ROOT / "cache" / "title.ratings.tsv.gz", Cache(ROOT / "cache"))
    except OSError as exc:
        print(f"error: cannot listen on {HOST}:{PORT}: {exc}", file=sys.stderr)
        return 1

    url = f"http://{HOST}:{PORT}/"
    print(f"Serving {url}  (Ctrl-C to stop)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
