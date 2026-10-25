"""Search the web through a private SearXNG, and read the pages it finds (D67).

SearXNG runs on the user's machine (deploy/searxng) and answers in JSON. It
asks the big search engines on the agent's behalf, which their terms do not
allow, so the agent searches little and slowly: a handful of queries per
investigation, a pause between them.

Pages are the open web: untrusted. A page is fetched only over http(s), never
from this machine or its network (each redirect is checked again, so a page
cannot bounce the reader to the local API), only as text, and only up to a
size. What is read goes to the model as data, never as instructions.
"""

import ipaddress
import logging
import socket
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx

from apply_agent.offers.linkedin_search import USER_AGENT
from apply_agent.providers.html_text import html_to_text

logger = logging.getLogger(__name__)

MAX_PAGE_BYTES: Final = 2_000_000
MAX_PAGE_CHARS: Final = 6_000
MIN_PAGE_CHARS: Final = 300
MAX_REDIRECTS: Final = 4
PAUSE_SECONDS: Final = 1.5
_TEXT_TYPES: Final = ("text/html", "text/plain", "application/xhtml+xml")
# Sites that need a signed-in account or refuse programs: nothing to read there.
SKIPPED_SITES: Final = ("linkedin.com", "glassdoor.", "indeed.", "facebook.com", "instagram.com")


class WebError(RuntimeError):
    """The search engine or a page could not be read."""


@dataclass(frozen=True, slots=True)
class WebResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class WebPage:
    url: str
    title: str
    text: str


class SearxngSearch:
    def __init__(
        self,
        client: httpx.Client,
        base_url: str,
        *,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._pause = pause
        self._searched = False

    def search(self, query: str, *, limit: int = 8) -> list[WebResult]:
        """Results for a query, best first. Raises WebError if SearXNG fails."""
        if self._searched:
            self._pause(PAUSE_SECONDS)
        self._searched = True
        try:
            response = self._client.get(
                f"{self._base}/search",
                params={"q": query, "format": "json", "language": "en", "safesearch": 0},
            )
        except httpx.HTTPError as err:
            raise WebError(f"SearXNG is not reachable ({type(err).__name__})") from err
        if response.status_code == 403:
            raise WebError("SearXNG refused JSON: turn on the json format in its settings")
        if response.status_code >= 400:
            raise WebError(f"SearXNG answered {response.status_code}")
        try:
            raw = response.json().get("results", [])
        except ValueError as err:
            raise WebError("SearXNG answered with something other than JSON") from err
        results = []
        for item in raw if isinstance(raw, list) else []:
            url = item.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                results.append(
                    WebResult(
                        title=str(item.get("title") or url)[:200],
                        url=url,
                        snippet=str(item.get("content") or "")[:500],
                    )
                )
        return results[:limit]


def _public_host(host: str) -> bool:
    """Whether every address the host resolves to is on the public internet."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)


def skipped(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    return any(site in host for site in SKIPPED_SITES)


class PageReader:
    def __init__(
        self, client: httpx.Client, *, is_public: Callable[[str], bool] = _public_host
    ) -> None:
        self._client = client
        self._is_public = is_public

    def read(self, url: str) -> WebPage:
        """A page as plain text, shortened. Raises WebError when it cannot be read."""
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise WebError("not a web address")
            if not self._is_public(parts.hostname):
                raise WebError("not a public address")
            try:
                with self._client.stream("GET", url) as response:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise WebError(f"the page answered {response.status_code}")
                    kind = response.headers.get("content-type", "").split(";")[0].strip()
                    if kind and kind not in _TEXT_TYPES:
                        raise WebError(f"not a text page ({kind})")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_PAGE_BYTES:
                            break
                    encoding = response.encoding or "utf-8"
            except httpx.HTTPError as err:
                raise WebError(f"the page could not be read ({type(err).__name__})") from err
            text = body.decode(encoding, errors="replace")
            plain = html_to_text(text) if kind != "text/plain" else text
            words = " ".join(plain.split())
            if len(words) < MIN_PAGE_CHARS:
                # A page built by JavaScript arrives empty. Kept, it would be a
                # source the model could cite for anything.
                raise WebError("the page has no readable text")
            title = _title(text) or url
            return WebPage(url=url, title=title[:200], text=words[:MAX_PAGE_CHARS])
        raise WebError("too many redirects")


def _title(html: str) -> str:
    start = html.find("<title")
    if start < 0:
        return ""
    open_end = html.find(">", start)
    close = html.find("</title>", open_end)
    if open_end < 0 or close < 0:
        return ""
    return " ".join(html_to_text(html[open_end + 1 : close]).split())


def pick_results(results: Sequence[WebResult], *, limit: int) -> list[WebResult]:
    """The best results, at most two per site, none from sites that cannot be read."""
    per_site: dict[str, int] = {}
    picked: list[WebResult] = []
    seen: set[str] = set()
    for result in results:
        host = (urlsplit(result.url).hostname or "").removeprefix("www.")
        if result.url in seen or skipped(result.url) or per_site.get(host, 0) >= 2:
            continue
        seen.add(result.url)
        per_site[host] = per_site.get(host, 0) + 1
        picked.append(result)
        if len(picked) >= limit:
            break
    return picked


def make_client() -> httpx.Client:
    # Redirects are followed by hand in PageReader, each checked again.
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15.0, follow_redirects=False)
