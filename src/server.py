"""Local web server: the page at / and showtimes JSON at /api/showtimes."""

import json
import logging
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .build import Config, build
from .cache import Cache
from .resolve import Resolver

PAGE = Path(__file__).resolve().parent.parent / "templates" / "page.html"

log = logging.getLogger(__name__)


def page_html(config: Config, static: bool) -> str:
    """Return the page with its settings filled in.

    A `static` page reads data/<date>.json next to itself instead of the API.
    Raises OSError if the template cannot be read.
    """
    return (PAGE.read_text(encoding="utf-8")
            .replace("__TIMEZONE__", json.dumps(config.timezone.key))
            .replace("__STATIC__", json.dumps(static)))


def make_server(host: str, port: int, config: Config, resolver: Resolver,
                ratings_path: Path, store: Cache) -> ThreadingHTTPServer:
    """Return a server bound to (host, port). Raises OSError if the port is taken."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                # Read per request so template edits show up on reload.
                self._send(200, "text/html; charset=utf-8", page_html(config, False).encode())
            elif url.path == "/api/showtimes":
                query = parse_qs(url.query)
                self._showtimes(query.get("date", [""])[0],
                                query.get("refresh", [""])[0] == "1")
            else:
                self._json(404, {"error": "not found"})

        def _showtimes(self, raw: str, refresh: bool) -> None:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                    raise ValueError
                day = date.fromisoformat(raw)
            except ValueError:
                self._json(400, {"error": "date must be YYYY-MM-DD"})
                return
            try:
                data = build(day, config, resolver, ratings_path, store, refresh)
            except Exception:
                log.exception("build failed for %s", day)
                self._json(500, {"error": "Fetch failed. See the terminal for details."})
                return
            self._json(200, data)

        def _json(self, status: int, data: dict) -> None:
            self._send(status, "application/json",
                       json.dumps(data, ensure_ascii=False).encode())

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return ThreadingHTTPServer((host, port), Handler)
