"""HTTP GET with retries and optional raw-payload dumps."""

import itertools
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
RETRY_STATUSES = {429, 500, 502, 503, 504}

log = logging.getLogger(__name__)
_dump_dir: Path | None = None
_dump_seq = itertools.count(1)


class FetchError(Exception):
    """Raised when a URL cannot be fetched. `problem` omits the URL."""

    def __init__(self, url: str, problem: str):
        super().__init__(f"{url}: {problem}")
        self.problem = problem


def enable_dump(directory: Path) -> None:
    """Save every response body fetched from now on under `directory`."""
    global _dump_dir
    directory.mkdir(parents=True, exist_ok=True)
    _dump_dir = directory


def fetch(url: str, *, headers: dict | None = None, label: str = "response",
          attempts: int = 3, timeout: float = 20) -> bytes:
    """Return the body of a 200 response to GET `url`.

    Retries network errors and 429/5xx responses with backoff.
    Raises FetchError on any other status or when retries run out.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        **(headers or {}),
    })
    for attempt in range(attempts):
        retry = True
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.status == 200:
                    _dump(label, body)
                    return body
                # IMDb answers bot checks with 202 and a challenge page.
                problem = f"HTTP {resp.status}"
                retry = False
        except urllib.error.HTTPError as exc:
            problem = f"HTTP {exc.code}"
            retry = exc.code in RETRY_STATUSES
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            problem = str(exc)
        if not retry or attempt == attempts - 1:
            raise FetchError(url, problem)
        log.debug("retrying %s after %s", label, problem)
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _dump(label: str, body: bytes) -> None:
    if _dump_dir is None:
        return
    name = f"{next(_dump_seq):04d}-{re.sub(r'[^A-Za-z0-9._-]+', '_', label)}"
    (_dump_dir / name).write_bytes(body)
