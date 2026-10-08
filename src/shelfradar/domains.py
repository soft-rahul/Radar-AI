"""URL → site name (eTLD+1), offline. `foodsure.co.in`, not `co.in`."""

from functools import lru_cache
from urllib.parse import parse_qs, urlparse

import tldextract

# Bundled public-suffix snapshot only: no network fetch at runtime or in tests.
_extract = tldextract.TLDExtract(suffix_list_urls=())

_REDIRECT_HOSTS = {"www.google.com", "google.com", "www.bing.com", "bing.com"}


def is_translated(url: str) -> bool:
    """Google serves some Hindi-answer sources as machine translations of English pages."""
    return urlparse(url).netloc == "translate.google.com"


def unwrap(url: str) -> str:
    """Follow google.com/url?q=…, translate.google.com/translate?u=… and similar to the real target."""
    parsed = urlparse(url)
    wrapped = (parsed.netloc in _REDIRECT_HOSTS and parsed.path in ("/url", "/ck/a")) or (
        parsed.netloc == "translate.google.com" and parsed.path == "/translate")
    if wrapped:
        query = parse_qs(parsed.query)
        target = query.get("q") or query.get("u")
        if target and target[0].startswith("http"):
            return target[0]
    return url


@lru_cache(maxsize=4096)
def domain_of(url: str) -> str:
    result = _extract(unwrap(url))
    return result.top_domain_under_public_suffix or result.domain or ""
