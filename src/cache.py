"""JSON values saved as files under one directory, each with the time it was saved."""

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Entry:
    saved_at: float
    value: Any


class Cache:
    """Values stored at <root>/<folder>/<name>.json."""

    def __init__(self, root: Path):
        self.root = root

    def get(self, folder: str, name: str, max_age_s: float) -> Entry | None:
        """Return the Entry saved under (folder, name) less than `max_age_s` ago.

        Returns None when there is no such entry. An unreadable file counts as
        no entry.
        """
        path = self._path(folder, name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = Entry(float(data["saved_at"]), data["value"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("ignoring unreadable cache file %s: %s", path, exc)
            return None
        return entry if 0 <= time.time() - entry.saved_at < max_age_s else None

    def put(self, folder: str, name: str, value: Any) -> Entry:
        """Save `value` under (folder, name) and return its Entry.

        A failed write is logged, and the Entry is returned anyway.
        """
        entry = Entry(time.time(), value)
        path = self._path(folder, name)
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as f:
                tmp = f.name
                json.dump({"saved_at": entry.saved_at, "value": value}, f,
                          ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not write cache file %s: %s", path, exc)
            if tmp:
                Path(tmp).unlink(missing_ok=True)
        return entry

    def prune(self, folder: str, max_age_s: float) -> None:
        """Delete files in `folder` last modified `max_age_s` or more ago."""
        cutoff = time.time() - max_age_s
        try:
            paths = list((self.root / folder).iterdir())
        except FileNotFoundError:
            return
        for path in paths:
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("could not prune cache file %s: %s", path, exc)

    def _path(self, folder: str, name: str) -> Path:
        # Names come from URLs and titles; this keeps them inside `folder`.
        return self.root / folder / (re.sub(r"[^A-Za-z0-9_-]+", "_", name) + ".json")
