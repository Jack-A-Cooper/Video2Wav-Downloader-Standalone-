"""URL safety checks for user-provided network targets."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class UrlSafetyError(ValueError):
    """Raised when user input is not an allowed public HTTP(S) URL."""
    pass


def validate_user_url(raw_url: str) -> str:
    """Validate that a URL is public HTTP(S) and safe to hand to extractors."""
    url = raw_url.strip()
    if not url:
        raise UrlSafetyError("Empty input.")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UrlSafetyError("Only http:// and https:// URLs are allowed.")
    if not parsed.netloc:
        raise UrlSafetyError("URL is missing a hostname.")
    if parsed.username or parsed.password:
        raise UrlSafetyError("URLs with embedded credentials are not allowed.")

    host = parsed.hostname or ""
    lower_host = host.lower()
    if lower_host == "localhost" or lower_host.endswith(".local"):
        raise UrlSafetyError("Localhost and .local targets are not allowed.")

    try:
        ip = ipaddress.ip_address(lower_host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UrlSafetyError("Private, loopback, or non-public IP targets are not allowed.")
    except ValueError:
        pass

    return url
