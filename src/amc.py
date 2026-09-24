"""Links to AMC's own showtimes pages."""

import re
from datetime import date
from urllib.parse import urlparse


def theater_page(url: str) -> str:
    """Return an AMC theater-page URL without a trailing slash.

    Raises ValueError if `url` is not an amctheatres.com theater page.
    """
    parts = urlparse(url)
    if (parts.scheme != "https" or parts.netloc != "www.amctheatres.com"
            or not re.fullmatch(r"/movie-theatres/[a-z0-9-]+/[a-z0-9-]+/?", parts.path)):
        raise ValueError(f"not an AMC theater-page URL: {url}")
    return url.rstrip("/")


def day_url(page: str, day: date) -> str:
    """Return the URL of all showtimes for `day` at the AMC theater `page`."""
    slug = page.rsplit("/", 1)[1]
    return f"{page}/showtimes/all/{day.isoformat()}/{slug}/all"
