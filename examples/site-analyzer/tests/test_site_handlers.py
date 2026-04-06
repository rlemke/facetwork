"""Tests for the site-analyzer example handlers."""

from __future__ import annotations

import json
import os

# ---------------------------------------------------------------------------
# Sample HTML for tests
# ---------------------------------------------------------------------------
SAMPLE_BLOG_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="description" content="A blog post about distributed systems">
    <meta property="og:title" content="Understanding Consensus">
    <meta property="og:image" content="https://example.com/images/consensus.png">
    <title>Understanding Consensus Algorithms</title>
</head>
<body>
    <h1>Understanding Consensus Algorithms</h1>
    <article>
        <p class="author">By Jane Smith | Published 2025-09-15</p>
        <h2>Introduction</h2>
        <p>Consensus algorithms are fundamental to distributed systems. They ensure
        that multiple nodes agree on a shared state despite failures and network
        partitions. This post explores Raft, Paxos, and PBFT.</p>
        <h2>Raft Protocol</h2>
        <p>Raft simplifies consensus through leader election and log replication.
        A cluster of nodes elects a leader that manages all client requests.</p>
        <h3>Leader Election</h3>
        <p>When a follower times out waiting for heartbeats, it becomes a candidate
        and requests votes from other nodes.</p>
    </article>
    <nav>
        <a href="/blog">Blog Home</a>
        <a href="/about">About</a>
        <a href="https://external-site.example.com/resources">Resources</a>
        <a href="https://broken-link.invalid/missing">Dead Link</a>
    </nav>
</body>
</html>"""

SAMPLE_PRODUCT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta name="description" content="CloudFlow - workflow automation platform">
    <title>CloudFlow - Automate Your Workflows</title>
</head>
<body>
    <h1>CloudFlow Platform</h1>
    <div class="hero">
        <p>Automate your workflows with our powerful product. Features include
        real-time monitoring, auto-scaling, and enterprise security.</p>
        <a href="/pricing" class="cta">View Pricing</a>
        <a href="/sign-up" class="cta">Get Started Free</a>
    </div>
    <h2>Features</h2>
    <p>Built for teams that need reliable workflow automation at scale.</p>
    <a href="https://broken-test.invalid/page">Old Partner Link</a>
</body>
</html>"""


# ---------------------------------------------------------------------------
# TestSiteUtils — utility function tests
# ---------------------------------------------------------------------------
class TestSiteUtils:
    def test_prepare_crawl_basic(self):
        from handlers.shared.site_utils import prepare_crawl

        result = prepare_crawl(["https://example.com/", "https://example.com/about"])
        assert result["page_count"] == 2
        assert result["base_domain"] == "example.com"
        assert result["site_id"].startswith("site-")
        assert len(result["urls"]) == 2

    def test_prepare_crawl_deduplication(self):
        from handlers.shared.site_utils import prepare_crawl

        result = prepare_crawl(
            [
                "https://example.com/page",
                "https://example.com/page/",  # trailing slash
                "https://example.com/page",  # duplicate
            ]
        )
        assert result["page_count"] == 1

    def test_prepare_crawl_adds_scheme(self):
        from handlers.shared.site_utils import prepare_crawl

        result = prepare_crawl(["example.com/page"])
        assert result["urls"][0].startswith("https://")

    def test_prepare_crawl_empty(self):
        from handlers.shared.site_utils import prepare_crawl

        result = prepare_crawl([])
        assert result["page_count"] == 0
        assert result["urls"] == []

    def test_prepare_crawl_deterministic(self):
        from handlers.shared.site_utils import prepare_crawl

        r1 = prepare_crawl(["https://example.com"])
        r2 = prepare_crawl(["https://example.com"])
        assert r1["site_id"] == r2["site_id"]

    def test_fetch_page_synthetic(self):
        from handlers.shared.site_utils import fetch_page

        result = fetch_page("https://example.com/about", "site-test")
        assert result["url"] == "https://example.com/about"
        assert result["status_code"] == 200
        assert result["content_type"] == "text/html"
        assert "<html" in result["html"]
        assert "<title>" in result["html"]
        assert isinstance(result["fetch_time_ms"], int)

    def test_fetch_page_deterministic(self):
        from handlers.shared.site_utils import fetch_page

        r1 = fetch_page("https://example.com/page", "site-123")
        r2 = fetch_page("https://example.com/page", "site-123")
        assert r1["html"] == r2["html"]

    def test_extract_metadata_from_html(self):
        from handlers.shared.site_utils import extract_metadata

        result = extract_metadata(
            "https://example.com/blog/consensus", SAMPLE_BLOG_HTML, "site-test"
        )
        assert result["title"] == "Understanding Consensus Algorithms"
        assert "distributed systems" in result["description"]
        assert result["language"] == "en"
        assert result["og_image"] == "https://example.com/images/consensus.png"
        assert len(result["headings"]) >= 3
        assert result["word_count"] > 50
        assert result["url"] == "https://example.com/blog/consensus"

    def test_extract_metadata_headings(self):
        from handlers.shared.site_utils import extract_metadata

        result = extract_metadata("https://example.com/blog", SAMPLE_BLOG_HTML, "site-test")
        h1s = [h for h in result["headings"] if h["level"] == 1]
        h2s = [h for h in result["headings"] if h["level"] == 2]
        assert len(h1s) >= 1
        assert len(h2s) >= 2

    def test_classify_page_type_blog(self):
        from handlers.shared.site_utils import classify_page_type

        result = classify_page_type(
            "https://example.com/blog/post-1", SAMPLE_BLOG_HTML, "site-test"
        )
        assert result["page_type"] == "blog"
        assert result["confidence"] > 0
        assert len(result["signals"]) > 0

    def test_classify_page_type_product(self):
        from handlers.shared.site_utils import classify_page_type

        result = classify_page_type("https://example.com/product", SAMPLE_PRODUCT_HTML, "site-test")
        # Should detect product or landing signals
        assert result["page_type"] in ("product", "landing")

    def test_classify_page_type_homepage(self):
        from handlers.shared.site_utils import classify_page_type

        result = classify_page_type(
            "https://example.com/", "<html><body>Welcome to our site</body></html>", "site-test"
        )
        assert result["page_type"] == "homepage"

    def test_classify_page_type_unknown(self):
        from handlers.shared.site_utils import classify_page_type

        result = classify_page_type(
            "https://example.com/xyz", "<html><body>xyzzy</body></html>", "site-test"
        )
        assert result["page_type"] == "other"

    def test_summarize_page(self):
        from handlers.shared.site_utils import summarize_page

        result = summarize_page("https://example.com/blog/post", SAMPLE_BLOG_HTML, "site-test")
        assert result["url"] == "https://example.com/blog/post"
        assert len(result["summary"]) > 20
        assert len(result["key_topics"]) >= 1
        assert result["word_count"] > 0

    def test_summarize_page_deterministic(self):
        from handlers.shared.site_utils import summarize_page

        r1 = summarize_page("https://example.com/page", SAMPLE_BLOG_HTML, "site-test")
        r2 = summarize_page("https://example.com/page", SAMPLE_BLOG_HTML, "site-test")
        assert r1 == r2

    def test_detect_broken_links(self):
        from handlers.shared.site_utils import detect_broken_links

        result = detect_broken_links("https://example.com/blog/post", SAMPLE_BLOG_HTML, "site-test")
        assert result["url"] == "https://example.com/blog/post"
        assert result["total_links"] == 4
        assert result["internal_links"] == 2  # /blog, /about
        assert result["external_links"] == 1  # external-site.example.com
        assert result["broken_count"] == 1  # broken-link.invalid
        assert len(result["broken_links"]) == 1
        assert "invalid" in result["broken_links"][0]["url"]

    def test_detect_broken_links_product(self):
        from handlers.shared.site_utils import detect_broken_links

        result = detect_broken_links(
            "https://example.com/product", SAMPLE_PRODUCT_HTML, "site-test"
        )
        assert result["broken_count"] == 1
        assert result["internal_links"] == 2  # /pricing, /sign-up

    def test_detect_broken_links_no_links(self):
        from handlers.shared.site_utils import detect_broken_links

        result = detect_broken_links(
            "https://example.com/empty", "<html><body>No links</body></html>", "site-test"
        )
        assert result["total_links"] == 0
        assert result["broken_count"] == 0

    def test_page_store_roundtrip(self):
        from handlers.shared.site_utils import _load_page_results, _save_page_result

        _save_page_result("site-123", "https://example.com/a", "metadata", {"title": "Page A"})
        _save_page_result("site-123", "https://example.com/b", "metadata", {"title": "Page B"})

        results = _load_page_results("site-123", "metadata")
        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert titles == {"Page A", "Page B"}

    def test_generate_site_report(self):
        from handlers.shared.site_utils import (
            _save_page_result,
            generate_site_report,
        )

        # Simulate page results
        _save_page_result(
            "site-abc",
            "https://example.com/",
            "metadata",
            {
                "url": "https://example.com/",
                "title": "Example Home",
                "description": "Welcome",
                "word_count": 100,
            },
        )
        _save_page_result(
            "site-abc",
            "https://example.com/",
            "classification",
            {
                "url": "https://example.com/",
                "page_type": "homepage",
                "confidence": 0.9,
            },
        )
        _save_page_result(
            "site-abc",
            "https://example.com/",
            "summary",
            {
                "url": "https://example.com/",
                "summary": "The example homepage.",
            },
        )
        _save_page_result(
            "site-abc",
            "https://example.com/",
            "links",
            {
                "url": "https://example.com/",
                "total_links": 5,
                "broken_count": 1,
                "broken_links": [
                    {"url": "https://broken.invalid/x", "reason": "domain_unreachable"}
                ],
            },
        )

        report_path, report = generate_site_report("site-abc", 1)
        assert report_path.endswith(".html")
        assert os.path.isfile(report_path)
        assert report["page_count"] == 1
        assert report["total_broken_links"] == 1
        assert "homepage" in report["page_types"]

        with open(report_path) as f:
            html = f.read()
        assert "Example Home" in html
        assert "broken" in html.lower()


# ---------------------------------------------------------------------------
# TestCrawlHandlers
# ---------------------------------------------------------------------------
class TestCrawlHandlers:
    def test_handle_prepare_crawl(self):
        from handlers.crawl.crawl_handlers import handle_prepare_crawl

        result = handle_prepare_crawl(
            {
                "urls": json.dumps(["https://example.com/", "https://example.com/about"]),
            }
        )
        assert "plan" in result
        assert result["plan"]["page_count"] == 2

    def test_handle_prepare_crawl_list_input(self):
        from handlers.crawl.crawl_handlers import handle_prepare_crawl

        result = handle_prepare_crawl(
            {
                "urls": ["https://example.com/"],
            }
        )
        assert result["plan"]["page_count"] == 1

    def test_handle_fetch_page(self):
        from handlers.crawl.crawl_handlers import handle_fetch_page

        result = handle_fetch_page({"url": "https://example.com/", "site_id": "site-test"})
        assert "page" in result
        assert result["page"]["status_code"] == 200
        assert "<html" in result["page"]["html"]

    def test_dispatch_keys(self):
        from handlers.crawl.crawl_handlers import _DISPATCH

        assert "site.Crawl.PrepareCrawl" in _DISPATCH
        assert "site.Crawl.FetchPage" in _DISPATCH

    def test_handle_dispatches(self):
        from handlers.crawl.crawl_handlers import handle

        result = handle(
            {
                "_facet_name": "site.Crawl.PrepareCrawl",
                "urls": ["https://example.com/"],
            }
        )
        assert "plan" in result


# ---------------------------------------------------------------------------
# TestMetadataHandlers
# ---------------------------------------------------------------------------
class TestMetadataHandlers:
    def test_handle_extract_metadata(self):
        from handlers.metadata.metadata_handlers import handle_extract_metadata

        result = handle_extract_metadata(
            {
                "url": "https://example.com/blog",
                "html": SAMPLE_BLOG_HTML,
                "site_id": "site-test",
            }
        )
        assert "meta" in result
        assert result["meta"]["title"] == "Understanding Consensus Algorithms"

    def test_step_log(self):
        from handlers.metadata.metadata_handlers import handle_extract_metadata

        messages = []
        handle_extract_metadata(
            {
                "url": "https://example.com/",
                "html": SAMPLE_BLOG_HTML,
                "site_id": "site-test",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Extracted metadata" in messages[0][0]

    def test_dispatch_keys(self):
        from handlers.metadata.metadata_handlers import _DISPATCH

        assert "site.Metadata.ExtractMetadata" in _DISPATCH


# ---------------------------------------------------------------------------
# TestClassificationHandlers
# ---------------------------------------------------------------------------
class TestClassificationHandlers:
    def test_handle_classify_blog(self):
        from handlers.classification.classification_handlers import handle_classify_page_type

        result = handle_classify_page_type(
            {
                "url": "https://example.com/blog/post-1",
                "html": SAMPLE_BLOG_HTML,
                "site_id": "site-test",
            }
        )
        assert "classification" in result
        assert result["classification"]["page_type"] == "blog"

    def test_dispatch_keys(self):
        from handlers.classification.classification_handlers import _DISPATCH

        assert "site.Classification.ClassifyPageType" in _DISPATCH


# ---------------------------------------------------------------------------
# TestSummarizationHandlers
# ---------------------------------------------------------------------------
class TestSummarizationHandlers:
    def test_handle_summarize_page(self):
        from handlers.summarization.summarization_handlers import handle_summarize_page

        result = handle_summarize_page(
            {
                "url": "https://example.com/blog/post",
                "html": SAMPLE_BLOG_HTML,
                "site_id": "site-test",
            }
        )
        assert "summary" in result
        assert result["summary"]["word_count"] > 0
        assert len(result["summary"]["key_topics"]) >= 1

    def test_dispatch_keys(self):
        from handlers.summarization.summarization_handlers import _DISPATCH

        assert "site.Summarization.SummarizePage" in _DISPATCH


# ---------------------------------------------------------------------------
# TestLinksHandlers
# ---------------------------------------------------------------------------
class TestLinksHandlers:
    def test_handle_detect_broken_links(self):
        from handlers.links.links_handlers import handle_detect_broken_links

        result = handle_detect_broken_links(
            {
                "url": "https://example.com/blog/post",
                "html": SAMPLE_BLOG_HTML,
                "site_id": "site-test",
            }
        )
        assert "link_report" in result
        assert result["link_report"]["broken_count"] == 1

    def test_step_log_warning_on_broken(self):
        from handlers.links.links_handlers import handle_detect_broken_links

        messages = []
        handle_detect_broken_links(
            {
                "url": "https://example.com/blog/post",
                "html": SAMPLE_BLOG_HTML,
                "site_id": "site-test",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert messages[0][1] == "warning"

    def test_step_log_success_no_broken(self):
        from handlers.links.links_handlers import handle_detect_broken_links

        messages = []
        handle_detect_broken_links(
            {
                "url": "https://example.com/page",
                "html": "<html><body><a href='/about'>About</a></body></html>",
                "site_id": "site-test",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert messages[0][1] == "success"

    def test_dispatch_keys(self):
        from handlers.links.links_handlers import _DISPATCH

        assert "site.Links.DetectBrokenLinks" in _DISPATCH


# ---------------------------------------------------------------------------
# TestReportingHandlers
# ---------------------------------------------------------------------------
class TestReportingHandlers:
    def test_handle_generate_site_report(self):
        from handlers.reporting.reporting_handlers import handle_generate_site_report
        from handlers.shared.site_utils import _save_page_result

        _save_page_result(
            "site-rpt",
            "https://example.com/",
            "metadata",
            {
                "url": "https://example.com/",
                "title": "Home",
                "description": "",
                "word_count": 50,
            },
        )
        _save_page_result(
            "site-rpt",
            "https://example.com/",
            "classification",
            {
                "url": "https://example.com/",
                "page_type": "homepage",
                "confidence": 0.8,
            },
        )
        _save_page_result(
            "site-rpt",
            "https://example.com/",
            "summary",
            {
                "url": "https://example.com/",
                "summary": "Homepage summary.",
            },
        )
        _save_page_result(
            "site-rpt",
            "https://example.com/",
            "links",
            {
                "url": "https://example.com/",
                "total_links": 3,
                "broken_count": 0,
                "broken_links": [],
            },
        )

        result = handle_generate_site_report({"site_id": "site-rpt", "page_count": 1})
        assert "report" in result
        assert result["report"]["report_path"].endswith(".html")

    def test_dispatch_keys(self):
        from handlers.reporting.reporting_handlers import _DISPATCH

        assert "site.Reporting.GenerateSiteReport" in _DISPATCH


# ---------------------------------------------------------------------------
# TestRegistration — verify all handlers register correctly
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_register_all_handlers(self):
        from unittest.mock import MagicMock

        from handlers import register_all_handlers

        poller = MagicMock()
        register_all_handlers(poller)
        # 7 handlers: PrepareCrawl, FetchPage, ExtractMetadata, ClassifyPageType,
        # SummarizePage, DetectBrokenLinks, GenerateSiteReport
        assert poller.register.call_count == 7

    def test_register_all_registry_handlers(self):
        from unittest.mock import MagicMock

        from handlers import register_all_registry_handlers

        runner = MagicMock()
        register_all_registry_handlers(runner)
        assert runner.register_handler.call_count == 7
