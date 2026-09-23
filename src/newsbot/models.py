from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    source: str
    category: str
    url: str
    id: str = ""


@dataclass(frozen=True, slots=True)
class ArticleCandidate:
    article_id: str
    source: str
    category: str
    title: str
    url: str
    published_at: datetime | None
    rss_summary: str
    feed_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    candidate: ArticleCandidate
    text: str
    word_count: int


@dataclass(frozen=True, slots=True)
class SummaryResult:
    text: str
    provider: str
    input_words: int
    output_words: int

