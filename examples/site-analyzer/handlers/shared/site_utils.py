"""Shared utility functions for the site-analyzer example.

All functions are pure and deterministic — they use hashlib for reproducible
test outputs rather than random data or real I/O.  When real HTML files are
provided (via the sample data generator), the extraction functions parse
actual content.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import os
import re
from urllib.parse import urlparse

from facetwork.config import get_output_base

_LOCAL_OUTPUT = get_output_base()
_SITE_REPORTS_DIR = os.path.join(_LOCAL_OUTPUT, "site-reports")
_PAGE_STORE_DIR = os.path.join(_LOCAL_OUTPUT, "site-page-store")

# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _hash_int(seed: str, lo: int, hi: int) -> int:
    """Deterministic integer from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo))


def _hash_float(seed: str, lo: float, hi: float) -> float:
    """Deterministic float from a seed string."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % 10000) / 10000 * (hi - lo)


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

PAGE_TYPES = [
    "homepage",
    "blog",
    "landing",
    "product",
    "docs",
    "about",
    "contact",
    "error",
    "other",
]

PAGE_TYPE_SIGNALS: dict[str, list[str]] = {
    "homepage": ["welcome", "hero", "featured", "main-nav", "index"],
    "blog": ["article", "post", "author", "published", "blog", "date"],
    "landing": ["cta", "sign-up", "get-started", "pricing", "trial"],
    "product": ["price", "buy", "add-to-cart", "product", "features"],
    "docs": ["documentation", "api", "reference", "guide", "code", "syntax"],
    "about": ["about", "team", "mission", "story", "founded"],
    "contact": ["contact", "email", "phone", "address", "form"],
    "error": ["404", "not found", "error", "oops"],
}


# ---------------------------------------------------------------------------
# Crawl preparation
# ---------------------------------------------------------------------------


def prepare_crawl(urls: list[str]) -> dict:
    """Normalize and deduplicate URLs, generate a site identifier.

    Returns dict matching CrawlPlan schema:
    {site_id, urls, page_count, base_domain}
    """
    # Normalize: strip trailing slashes, lowercase scheme+host
    normalized: list[str] = []
    seen: set[str] = set()
    for url in urls:
        url = url.strip()
        if not url:
            continue
        # Ensure scheme
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        # Normalize
        parsed = urlparse(url)
        norm = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        if parsed.query:
            norm += f"?{parsed.query}"
        if norm not in seen:
            seen.add(norm)
            normalized.append(norm)

    # Derive base domain from first URL
    base_domain = ""
    if normalized:
        parsed = urlparse(normalized[0])
        base_domain = parsed.netloc.lower()

    # Generate deterministic site ID from sorted URL set
    url_hash = hashlib.sha256("|".join(sorted(normalized)).encode()).hexdigest()[:12]
    site_id = f"site-{url_hash}"

    return {
        "site_id": site_id,
        "urls": normalized,
        "page_count": len(normalized),
        "base_domain": base_domain,
    }


# ---------------------------------------------------------------------------
# Page fetching
# ---------------------------------------------------------------------------


def fetch_page(url: str, site_id: str) -> dict:
    """Fetch page content. Reads from local cache or generates synthetic HTML.

    Returns dict matching PageContent schema:
    {url, status_code, content_type, html, fetch_time_ms}
    """
    # Check if we have a cached HTML file from the sample data generator
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    cache_path = os.path.join(_PAGE_STORE_DIR, site_id, f"{url_hash}.html")

    html = ""
    status_code = 200
    fetch_time_ms = _hash_int(f"fetch:{url}", 50, 2000)

    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            html = f.read()
    else:
        # Generate deterministic synthetic HTML
        html = _generate_synthetic_html(url)

    return {
        "url": url,
        "status_code": status_code,
        "content_type": "text/html",
        "html": html,
        "fetch_time_ms": fetch_time_ms,
    }


def _generate_synthetic_html(url: str) -> str:
    """Generate deterministic synthetic HTML for testing."""
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    domain = parsed.netloc or "example.com"
    title = path.replace("/", " - ").replace("-", " ").title()

    seed = hashlib.sha256(url.encode()).hexdigest()
    word_count = _hash_int(f"{seed}:words", 100, 500)
    words = []
    for i in range(word_count):
        h = _hash_int(f"{seed}:w{i}", 0, len(_WORD_LIST))
        words.append(_WORD_LIST[h])
    body_text = " ".join(words)

    links = []
    link_count = _hash_int(f"{seed}:links", 3, 15)
    for i in range(link_count):
        h = _hash_int(f"{seed}:link{i}", 0, 100)
        if h < 60:
            links.append(
                f'<a href="/{_WORD_LIST[h % len(_WORD_LIST)]}">{_WORD_LIST[(h + 1) % len(_WORD_LIST)]}</a>'
            )
        elif h < 85:
            links.append(f'<a href="https://external-{h}.example.com/page">external link {i}</a>')
        else:
            links.append(f'<a href="https://broken-{h}.invalid/missing">broken link {i}</a>')
    links_html = "\n".join(links)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="description" content="Page about {title} on {domain}">
    <meta property="og:title" content="{title}">
    <meta property="og:image" content="https://{domain}/images/{path}.png">
    <title>{title}</title>
</head>
<body>
    <h1>{title}</h1>
    <h2>Overview</h2>
    <p>{body_text}</p>
    <h2>Related Links</h2>
    <nav>
    {links_html}
    </nav>
</body>
</html>"""


_WORD_LIST = [
    "the",
    "analysis",
    "system",
    "data",
    "process",
    "website",
    "report",
    "results",
    "content",
    "method",
    "page",
    "performance",
    "quality",
    "review",
    "server",
    "testing",
    "deployment",
    "integration",
    "service",
    "workflow",
    "pipeline",
    "output",
    "input",
    "configuration",
    "monitoring",
    "navigation",
    "header",
    "footer",
    "sidebar",
    "article",
    "section",
    "feature",
    "product",
    "pricing",
    "documentation",
    "guide",
    "tutorial",
    "dashboard",
    "analytics",
    "metrics",
    "conversion",
    "optimization",
    "responsive",
    "mobile",
    "desktop",
    "interface",
    "experience",
    "design",
    "layout",
    "template",
    "framework",
    "library",
    "component",
    "module",
    "authentication",
    "security",
    "privacy",
    "compliance",
    "accessibility",
    "search",
    "filter",
    "sort",
    "pagination",
    "loading",
    "caching",
    "database",
    "storage",
    "backup",
    "recovery",
    "migration",
    "upgrade",
    "notification",
    "alert",
    "message",
    "feedback",
    "support",
    "help",
    "contact",
    "about",
    "team",
    "blog",
    "news",
    "updates",
    "changelog",
    "release",
    "version",
    "status",
    "health",
    "uptime",
    "latency",
    "throughput",
    "bandwidth",
    "capacity",
    "scaling",
    "cluster",
    "node",
]


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def extract_metadata(url: str, html: str, site_id: str) -> dict:
    """Extract page metadata from HTML.

    Returns dict matching PageMetadata schema:
    {url, title, description, language, canonical_url, og_image, headings, word_count}
    """
    title = _extract_tag(html, "title") or url
    description = _extract_meta(html, "description") or ""
    language = _extract_attr(html, "html", "lang") or "en"
    canonical_url = _extract_link_rel(html, "canonical") or url
    og_image = _extract_meta(html, "og:image", attr="property") or ""

    # Extract headings
    headings = []
    for level in range(1, 4):
        for match in re.finditer(
            rf"<h{level}[^>]*>(.*?)</h{level}>", html, re.IGNORECASE | re.DOTALL
        ):
            text = _strip_tags(match.group(1)).strip()
            if text:
                headings.append({"level": level, "text": text})

    # Word count from visible text
    visible_text = _strip_tags(html)
    word_count = len(visible_text.split())

    result = {
        "url": url,
        "title": title,
        "description": description,
        "language": language,
        "canonical_url": canonical_url,
        "og_image": og_image,
        "headings": headings,
        "word_count": word_count,
    }

    # Persist for report aggregation
    _save_page_result(site_id, url, "metadata", result)

    return result


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------


def classify_page_type(url: str, html: str, site_id: str) -> dict:
    """Classify page type based on URL patterns and HTML content.

    Returns dict matching PageClassification schema:
    {url, page_type, confidence, signals}
    """
    html_lower = html.lower()
    url_lower = url.lower()

    scores: dict[str, int] = {}
    detected_signals: dict[str, list[str]] = {}

    for page_type, keywords in PAGE_TYPE_SIGNALS.items():
        found = []
        for kw in keywords:
            if kw in html_lower or kw in url_lower:
                found.append(kw)
        if found:
            scores[page_type] = len(found)
            detected_signals[page_type] = found

    # URL path heuristics
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    if path in ("", "index", "home"):
        scores["homepage"] = scores.get("homepage", 0) + 3
        detected_signals.setdefault("homepage", []).append(f"path={path or '/'}")
    elif "blog" in path or "post" in path or "article" in path:
        scores["blog"] = scores.get("blog", 0) + 3
        detected_signals.setdefault("blog", []).append(f"path={path}")
    elif "doc" in path or "api" in path or "guide" in path:
        scores["docs"] = scores.get("docs", 0) + 3
        detected_signals.setdefault("docs", []).append(f"path={path}")
    elif "about" in path:
        scores["about"] = scores.get("about", 0) + 3
        detected_signals.setdefault("about", []).append(f"path={path}")
    elif "contact" in path:
        scores["contact"] = scores.get("contact", 0) + 3
        detected_signals.setdefault("contact", []).append(f"path={path}")
    elif "product" in path or "pricing" in path:
        scores["product"] = scores.get("product", 0) + 3
        detected_signals.setdefault("product", []).append(f"path={path}")

    if scores:
        best_type = max(scores, key=scores.__getitem__)
        total = sum(scores.values())
        confidence = round(scores[best_type] / max(total, 1), 2)
        signals = detected_signals.get(best_type, [])
    else:
        best_type = "other"
        confidence = 0.5
        signals = ["no-matching-signals"]

    result = {
        "url": url,
        "page_type": best_type,
        "confidence": confidence,
        "signals": signals,
    }

    _save_page_result(site_id, url, "classification", result)

    return result


# ---------------------------------------------------------------------------
# Page summarization
# ---------------------------------------------------------------------------


def summarize_page(url: str, html: str, site_id: str) -> dict:
    """Summarize the visible text content of a page.

    Returns dict matching PageSummary schema:
    {url, summary, key_topics, word_count}
    """
    visible_text = _strip_tags(html)
    words = visible_text.split()
    word_count = len(words)

    # Build summary from first substantial text block
    sentences = re.split(r"[.!?]+", visible_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    if len(sentences) >= 3:
        summary = ". ".join(sentences[:3]) + "."
    elif sentences:
        summary = ". ".join(sentences) + "."
    else:
        summary = f"Page at {url} contains {word_count} words."

    if len(summary) > 500:
        summary = summary[:497] + "..."

    # Extract key topics: distinctive words >5 chars
    seen: set[str] = set()
    key_topics: list[str] = []
    for w in words:
        clean = w.lower().strip(".,;:!?()[]{}\"'<>/")
        if len(clean) > 5 and clean not in seen and clean.isalpha():
            seen.add(clean)
            key_topics.append(clean)
        if len(key_topics) >= 5:
            break

    result = {
        "url": url,
        "summary": summary,
        "key_topics": key_topics,
        "word_count": word_count,
    }

    _save_page_result(site_id, url, "summary", result)

    return result


# ---------------------------------------------------------------------------
# Broken link detection
# ---------------------------------------------------------------------------


def detect_broken_links(url: str, html: str, site_id: str) -> dict:
    """Scan HTML for links and classify them as internal, external, or broken.

    Uses deterministic heuristics rather than actual HTTP requests:
    - Links to *.invalid domains are treated as broken
    - Links with "broken" in the URL are treated as broken
    - All other links are treated as valid

    Returns dict matching LinkReport schema:
    {url, total_links, internal_links, external_links, broken_links, broken_count}
    """
    parsed_base = urlparse(url)
    base_domain = parsed_base.netloc.lower()

    # Extract all hrefs
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)

    internal = 0
    external = 0
    broken: list[dict] = []

    for href in hrefs:
        # Skip anchors, javascript, mailto
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        # Resolve relative URLs
        if href.startswith("/"):
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{href}"
        elif not href.startswith(("http://", "https://")):
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}/{href}"
        else:
            full_url = href

        link_parsed = urlparse(full_url)
        link_domain = link_parsed.netloc.lower()

        # Classify
        is_broken = (
            link_domain.endswith(".invalid")
            or "broken" in href.lower()
            or "missing" in href.lower()
        )

        if is_broken:
            broken.append(
                {
                    "url": full_url,
                    "source_page": url,
                    "reason": "domain_unreachable"
                    if link_domain.endswith(".invalid")
                    else "likely_broken",
                }
            )
        elif link_domain == base_domain or not link_domain:
            internal += 1
        else:
            external += 1

    result = {
        "url": url,
        "total_links": len(hrefs),
        "internal_links": internal,
        "external_links": external,
        "broken_links": broken,
        "broken_count": len(broken),
    }

    _save_page_result(site_id, url, "links", result)

    return result


# ---------------------------------------------------------------------------
# Site report generation
# ---------------------------------------------------------------------------


def generate_site_report(site_id: str, page_count: int) -> tuple[str, dict]:
    """Generate a consolidated HTML report from all page analysis results.

    Reads from the page store to aggregate results across all pages.

    Returns (report_path, report_dict matching SiteReport schema).
    """
    os.makedirs(_SITE_REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(_SITE_REPORTS_DIR, f"{site_id}-report.html")

    # Load all page results
    metadata_list = _load_page_results(site_id, "metadata")
    classification_list = _load_page_results(site_id, "classification")
    summary_list = _load_page_results(site_id, "summary")
    links_list = _load_page_results(site_id, "links")

    # Aggregate
    base_domain = ""
    if metadata_list:
        first_url = metadata_list[0].get("url", "")
        parsed = urlparse(first_url)
        base_domain = parsed.netloc

    total_broken = sum(lr.get("broken_count", 0) for lr in links_list)

    page_types: dict[str, int] = {}
    for c in classification_list:
        pt = c.get("page_type", "other")
        page_types[pt] = page_types.get(pt, 0) + 1

    # Build combined summary
    page_summaries = [s.get("summary", "") for s in summary_list]
    combined_summary = " ".join(page_summaries)
    if len(combined_summary) > 2000:
        combined_summary = combined_summary[:1997] + "..."

    # Generate HTML
    pages_html = ""
    for i, meta in enumerate(metadata_list):
        url = meta.get("url", "")
        title = meta.get("title", "Untitled")
        desc = meta.get("description", "")
        wc = meta.get("word_count", 0)

        classification = classification_list[i] if i < len(classification_list) else {}
        page_type = classification.get("page_type", "unknown")
        confidence = classification.get("confidence", 0)

        summary_data = summary_list[i] if i < len(summary_list) else {}
        page_summary = summary_data.get("summary", "")

        link_data = links_list[i] if i < len(links_list) else {}
        broken_count = link_data.get("broken_count", 0)
        total_links = link_data.get("total_links", 0)

        broken_items = ""
        for bl in link_data.get("broken_links", []):
            broken_items += f'<li class="broken">{html_mod.escape(bl.get("url", ""))} — {bl.get("reason", "")}</li>\n'

        pages_html += f"""
        <div class="page-card">
            <h3><a href="{html_mod.escape(url)}">{html_mod.escape(title)}</a></h3>
            <div class="meta-row">
                <span class="badge type-{page_type}">{page_type}</span>
                <span class="confidence">confidence: {confidence:.0%}</span>
                <span>{wc:,} words</span>
                <span>{total_links} links ({broken_count} broken)</span>
            </div>
            <p class="description">{html_mod.escape(desc)}</p>
            <p class="summary">{html_mod.escape(page_summary)}</p>
            {"<ul>" + broken_items + "</ul>" if broken_items else ""}
        </div>
"""

    type_breakdown = " | ".join(f"{k}: {v}" for k, v in sorted(page_types.items()))

    report_html = f"""<!DOCTYPE html>
<html>
<head><title>Site Report: {html_mod.escape(base_domain)}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2em; color: #333; }}
  h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 0.3em; }}
  h2 {{ color: #34495e; margin-top: 2em; }}
  .overview {{ background: #ecf0f1; padding: 1.5em; border-radius: 8px; margin: 1em 0;
               display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1em; }}
  .stat {{ text-align: center; }}
  .stat .number {{ font-size: 2em; font-weight: bold; color: #2c3e50; }}
  .stat .label {{ color: #7f8c8d; font-size: 0.9em; }}
  .page-card {{ border: 1px solid #ddd; border-radius: 8px; padding: 1.2em; margin: 1em 0; }}
  .page-card h3 {{ margin: 0 0 0.5em; }}
  .page-card h3 a {{ color: #2980b9; text-decoration: none; }}
  .meta-row {{ display: flex; gap: 1em; font-size: 0.85em; color: #666; margin-bottom: 0.5em; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; background: #3498db; color: white; font-size: 0.8em; }}
  .type-blog {{ background: #27ae60; }} .type-docs {{ background: #8e44ad; }}
  .type-product {{ background: #e67e22; }} .type-homepage {{ background: #2c3e50; }}
  .type-about {{ background: #16a085; }} .type-contact {{ background: #d35400; }}
  .description {{ color: #555; font-style: italic; }}
  .summary {{ line-height: 1.5; }}
  .broken {{ color: #e74c3c; }}
</style>
</head>
<body>
<h1>Site Analysis Report: {html_mod.escape(base_domain)}</h1>
<div class="overview">
    <div class="stat"><div class="number">{page_count}</div><div class="label">Pages Analyzed</div></div>
    <div class="stat"><div class="number">{total_broken}</div><div class="label">Broken Links</div></div>
    <div class="stat"><div class="number">{len(page_types)}</div><div class="label">Page Types</div></div>
</div>
<p><strong>Page types:</strong> {type_breakdown}</p>
<h2>Page Details</h2>
{pages_html}
<h2>Combined Summary</h2>
<p>{html_mod.escape(combined_summary)}</p>
</body>
</html>"""

    with open(report_path, "w") as f:
        f.write(report_html)

    report = {
        "site_id": site_id,
        "base_domain": base_domain,
        "page_count": len(metadata_list),
        "total_broken_links": total_broken,
        "page_types": page_types,
        "summary": combined_summary,
        "report_path": report_path,
    }

    return report_path, report


# ---------------------------------------------------------------------------
# Page result store — persists per-page analysis for cross-step aggregation
# ---------------------------------------------------------------------------


def _save_page_result(site_id: str, url: str, result_type: str, data: dict) -> str:
    """Save a page analysis result to the store."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    store_dir = os.path.join(_PAGE_STORE_DIR, site_id, result_type)
    os.makedirs(store_dir, exist_ok=True)

    out_path = os.path.join(store_dir, f"{url_hash}.json")
    with open(out_path, "w") as f:
        json.dump(data, f)
    return out_path


def _load_page_results(site_id: str, result_type: str) -> list[dict]:
    """Load all page results of a given type for a site."""
    store_dir = os.path.join(_PAGE_STORE_DIR, site_id, result_type)
    if not os.path.isdir(store_dir):
        return []

    results = []
    for fname in sorted(os.listdir(store_dir)):
        if fname.endswith(".json"):
            with open(os.path.join(store_dir, fname)) as f:
                results.append(json.load(f))
    return results


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


def _extract_tag(html: str, tag: str) -> str:
    """Extract text content of the first occurrence of a tag."""
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
    return _strip_tags(match.group(1)).strip() if match else ""


def _extract_meta(html: str, name: str, attr: str = "name") -> str:
    """Extract content attribute from a meta tag."""
    pattern = rf'<meta\s+{attr}=["\']?{re.escape(name)}["\']?\s+content=["\']([^"\']*)["\']'
    match = re.search(pattern, html, re.IGNORECASE)
    if not match:
        pattern = rf'<meta\s+content=["\']([^"\']*)["\']?\s+{attr}=["\']?{re.escape(name)}["\']?'
        match = re.search(pattern, html, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_attr(html: str, tag: str, attr: str) -> str:
    """Extract an attribute value from the first occurrence of a tag."""
    match = re.search(rf"<{tag}\s[^>]*{attr}=[\"']([^\"']*)[\"']", html, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_link_rel(html: str, rel: str) -> str:
    """Extract href from a <link rel='...'> tag."""
    pattern = rf'<link\s[^>]*rel=["\']?{re.escape(rel)}["\']?\s[^>]*href=["\']([^"\']*)["\']'
    match = re.search(pattern, html, re.IGNORECASE)
    if not match:
        pattern = rf'<link\s[^>]*href=["\']([^"\']*)["\']?\s[^>]*rel=["\']?{re.escape(rel)}["\']?'
        match = re.search(pattern, html, re.IGNORECASE)
    return match.group(1) if match else ""


def _strip_tags(html: str) -> str:
    """Remove HTML tags and decode entities, returning visible text."""
    # Remove script and style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode entities
    text = html_mod.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()
