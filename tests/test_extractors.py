from pathlib import Path

import pytest

from newsbot.extractors import ExtractionError, extract_article
from newsbot.models import ArticleCandidate


FIXTURES = Path(__file__).parent / "fixtures"


def candidate(source: str) -> ArticleCandidate:
    return ArticleCandidate(
        article_id="article-id",
        source=source,
        category="Thời sự",
        title="Tiêu đề",
        url="https://example.test/article",
        published_at=None,
        rss_summary="Mô tả RSS",
    )


def test_extracts_vnexpress_main_text_and_removes_noise() -> None:
    html = (FIXTURES / "vnexpress-article.html").read_text(encoding="utf-8")

    article = extract_article(candidate("VnExpress"), html)

    assert article.text.startswith("Đoạn mở đầu")
    assert "Nội dung chính cần giữ" in article.text
    assert "Tin liên quan" not in article.text
    assert "Chú thích ảnh" not in article.text
    assert "Menu không được lấy" not in article.text
    assert article.word_count >= 40


def test_extracts_dantri_main_text_and_removes_noise() -> None:
    html = (FIXTURES / "dantri-article.html").read_text(encoding="utf-8")

    article = extract_article(candidate("Dân trí"), html)

    assert article.text.startswith("Nội dung Dân trí")
    assert "Quảng cáo" not in article.text
    assert "Tin liên quan" not in article.text
    assert "Đầu trang" not in article.text
    assert article.word_count >= 40


def test_rejects_page_without_article_content() -> None:
    with pytest.raises(ExtractionError, match="content"):
        extract_article(candidate("VnExpress"), "<html><body><nav>Menu</nav></body></html>")


@pytest.mark.parametrize(
    ("source", "fixture", "noise"),
    [
        ("CafeBiz", "cafebiz-article.html", "Theo nguồn thử nghiệm"),
        ("GenK", "genk-article.html", "Chú thích ảnh GenK"),
        ("Kenh14", "kenh14-article.html", "Tin liên quan Kenh14"),
    ],
)
def test_extracts_vccorp_sources(source: str, fixture: str, noise: str) -> None:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")

    article = extract_article(candidate(source), html)

    assert article.text.startswith(f"Sapo {source} thử nghiệm")
    assert "Đoạn nội dung thử nghiệm số 1 " in article.text
    assert "tiếp theo số 12 " in article.text
    assert noise not in article.text
    assert "Menu điều hướng" not in article.text
    assert "Bài viết khác" not in article.text
    assert article.word_count >= 200
