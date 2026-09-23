from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup, Tag

from newsbot.models import ArticleCandidate, ExtractedArticle


# CafeBiz, GenK and Kenh14 share the same publishing platform and markup.
VCCORP_CONTENT_SELECTORS = (".detail-content", "[data-role='content']")
VCCORP_LEAD_SELECTORS = ("[data-role='sapo']", ".knc-sapo", ".sapo")
CONTENT_SELECTORS = {
    "VnExpress": ("article.fck_detail", ".fck_detail"),
    "Dân trí": (".singular-content", "article .singular-content", "article"),
    "CafeBiz": VCCORP_CONTENT_SELECTORS,
    "GenK": VCCORP_CONTENT_SELECTORS,
    "Kenh14": VCCORP_CONTENT_SELECTORS,
}
LEAD_SELECTORS = {
    "VnExpress": ("p.description", ".description"),
    "Dân trí": (".singular-sapo", "h2.singular-sapo"),
    "CafeBiz": VCCORP_LEAD_SELECTORS,
    "GenK": VCCORP_LEAD_SELECTORS,
    "Kenh14": VCCORP_LEAD_SELECTORS,
}
REMOVE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "figure",
    ".related-news",
    ".box-tinlienquan",
    ".ads",
    ".advertisement",
    "[data-role='related-news']",
    ".PhotoCMS_Caption",
    ".link-source-wrapper",
    ".kbwscwl-relatedbox",
    ".link-callout",
)
MIN_ARTICLE_WORDS = 40
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class ExtractionError(ValueError):
    """Raised when a page cannot provide enough article content."""


def _normalize_text(value: str) -> str:
    value = re.sub(r"\s+([,.;:!?%)\]])", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _content_blocks(node: Tag) -> list[str]:
    isolated = BeautifulSoup(str(node), "lxml")
    for selector in REMOVE_SELECTORS:
        for unwanted in isolated.select(selector):
            unwanted.decompose()

    blocks: list[str] = []
    for element in isolated.select("p, h2, h3"):
        text = _normalize_text(element.get_text(" ", strip=True))
        if text and (not blocks or blocks[-1] != text):
            blocks.append(text)
    return blocks


def _lead_text(soup: BeautifulSoup, source: str) -> str:
    for selector in LEAD_SELECTORS.get(source, ()):
        node = soup.select_one(selector)
        if node is not None:
            return _normalize_text(node.get_text(" ", strip=True))
    return ""


def extract_article(candidate: ArticleCandidate, html: str) -> ExtractedArticle:
    soup = BeautifulSoup(html, "lxml")
    selectors = (*CONTENT_SELECTORS.get(candidate.source, ()), "article")
    lead = _lead_text(soup, candidate.source)

    for selector in dict.fromkeys(selectors):
        node = soup.select_one(selector)
        if node is None:
            continue
        blocks = _content_blocks(node)
        if lead and (not blocks or blocks[0] != lead):
            blocks.insert(0, lead)
        text = "\n\n".join(blocks)
        word_count = len(text.split())
        if word_count >= MIN_ARTICLE_WORDS:
            return ExtractedArticle(candidate=candidate, text=text, word_count=word_count)

    raise ExtractionError(f"Unable to extract article content from {candidate.source}")


async def fetch_and_extract(
    client: httpx.AsyncClient,
    candidate: ArticleCandidate,
    *,
    attempts: int = 3,
) -> ExtractedArticle:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(
                candidate.url,
                headers={"User-Agent": USER_AGENT},
                timeout=20.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            return extract_article(candidate, response.text)
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code != 429 and exc.response.status_code < 500:
                break
        except (httpx.RequestError, ExtractionError) as exc:
            last_error = exc
            if isinstance(exc, ExtractionError):
                break

        if attempt < attempts - 1:
            await asyncio.sleep(0.5 * (2**attempt))

    raise ExtractionError(f"Unable to fetch article content from {candidate.source}") from last_error
