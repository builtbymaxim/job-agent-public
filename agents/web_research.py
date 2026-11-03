"""Web-based company research via DuckDuckGo HTML search + page fetching."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
TIMEOUT = 8.0
MAX_RESULTS = 3
MAX_CHARS_PER_PAGE = 2000

SKIP_DOMAINS = {
    "linkedin.com", "xing.com", "indeed.com", "glassdoor.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "karriere.at", "stepstone.at", "stepstone.de", "stepstone.com",
}


def research_company_web(company: str, location: str = "") -> str:
    """Fetch company info via DuckDuckGo + page scrape. Returns concatenated snippets or ''."""
    try:
        urls = _ddg_search(f"{company} {location} company about")
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed for {company}: {e}")
        return ""

    if not urls:
        return ""

    snippets: list[str] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
    ) as client:
        for url in urls[:MAX_RESULTS]:
            try:
                text = _fetch_page_text(client, url)
                if text and len(text) > 200:
                    snippets.append(f"[Source: {url}]\n{text[:MAX_CHARS_PER_PAGE]}")
            except Exception as e:
                logger.debug(f"Fetch failed for {url}: {e}")
                continue

    return "\n\n---\n\n".join(snippets)


def fetch_url_text(url: str) -> str:
    """Fetch a single page and return cleaned readable text, or '' on failure.

    Used by direct-apply mode to pull a job posting the user already found.
    """
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            return _fetch_page_text(client, url)
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return ""


def _ddg_search(query: str) -> list[str]:
    """Scrape DuckDuckGo HTML results. Returns ordered list of result URLs."""
    params = {"q": query}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = client.post(DDG_URL, data=params)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    urls: list[str] = []
    for link in soup.select("a.result__a"):
        href = link.get("href", "")
        clean = _clean_ddg_url(href)
        if clean and _is_usable_url(clean) and clean not in urls:
            urls.append(clean)
        if len(urls) >= 6:
            break
    return urls


def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps results in redirect URLs; extract the real target."""
    match = re.search(r"uddg=([^&]+)", href)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    return href if href.startswith("http") else ""


def _is_usable_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return False
    if not host:
        return False
    return not any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS)


def _fetch_page_text(client: httpx.Client, url: str) -> str:
    """Fetch page and extract readable text content."""
    response = client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text
