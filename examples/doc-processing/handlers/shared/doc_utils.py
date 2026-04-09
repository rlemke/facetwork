"""Shared utility functions for the doc-processing example.

All functions are pure and deterministic — they use hashlib for reproducible
test outputs rather than random data or real I/O.  When a real file is
available, the extraction functions read actual content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from facetwork.config import get_output_base

_LOCAL_OUTPUT = get_output_base()
_DOC_REPORTS_DIR = os.path.join(_LOCAL_OUTPUT, "doc-reports")
_SUMMARY_STORE_DIR = os.path.join(_LOCAL_OUTPUT, "doc-summaries")

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

SUPPORTED_FILE_TYPES = {"pdf", "txt", "md", "csv", "json", "html", "rst"}

DOCUMENT_CATEGORIES = [
    "legal",
    "technical",
    "financial",
    "medical",
    "academic",
    "correspondence",
    "report",
    "other",
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "legal": ["contract", "agreement", "clause", "party", "hereby", "law", "court"],
    "technical": ["api", "function", "system", "module", "algorithm", "code", "deploy"],
    "financial": ["revenue", "profit", "quarter", "fiscal", "budget", "investment"],
    "medical": ["patient", "diagnosis", "treatment", "clinical", "symptom", "dose"],
    "academic": ["research", "hypothesis", "methodology", "findings", "abstract"],
    "correspondence": ["dear", "regards", "sincerely", "meeting", "schedule"],
    "report": ["summary", "findings", "recommendations", "analysis", "overview"],
}


# ---------------------------------------------------------------------------
# File detection
# ---------------------------------------------------------------------------


def detect_file_type(file_path: str) -> dict:
    """Detect file type and extract metadata.

    Returns dict matching FileInfo schema:
    {file_path, file_type, file_size, page_count, encoding}
    """
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if ext not in SUPPORTED_FILE_TYPES:
        ext = "txt"  # default fallback

    file_size = 0
    encoding = "utf-8"
    page_count = 1

    if os.path.isfile(file_path):
        file_size = os.path.getsize(file_path)
        if ext == "pdf":
            # Estimate pages from file size (~3KB per page for text-heavy PDFs)
            page_count = max(1, file_size // 3000)
            encoding = "binary"
        else:
            # Estimate pages from line count (~60 lines per page)
            try:
                with open(file_path, encoding="utf-8") as f:
                    lines = sum(1 for _ in f)
                page_count = max(1, lines // 60)
            except (UnicodeDecodeError, OSError):
                encoding = "latin-1"
                page_count = max(1, file_size // 3000)
    else:
        # Deterministic fallback for non-existent files
        file_size = _hash_int(f"size:{file_path}", 500, 50000)
        page_count = _hash_int(f"pages:{file_path}", 1, 20)

    return {
        "file_path": file_path,
        "file_type": ext,
        "file_size": file_size,
        "page_count": page_count,
        "encoding": encoding,
    }


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def extract_text(file_path: str, file_type: str, encoding: str = "utf-8") -> dict:
    """Extract text content from a file.

    Returns dict matching ExtractedText schema:
    {text, char_count, word_count, method}
    """
    text = ""
    method = f"{file_type}_reader"

    if os.path.isfile(file_path):
        if file_type == "pdf":
            text = _extract_pdf_text(file_path)
            method = "pdf_text_extraction"
        else:
            enc = encoding if encoding != "binary" else "utf-8"
            try:
                with open(file_path, encoding=enc) as f:
                    text = f.read()
                method = f"direct_read_{enc}"
            except UnicodeDecodeError:
                with open(file_path, encoding="latin-1") as f:
                    text = f.read()
                method = "direct_read_latin1"
    else:
        # Deterministic synthetic text for non-existent files
        text = _generate_synthetic_text(file_path)
        method = "synthetic"

    words = text.split()
    return {
        "text": text,
        "char_count": len(text),
        "word_count": len(words),
        "method": method,
    }


def _extract_pdf_text(file_path: str) -> str:
    """Extract text from PDF. Falls back to synthetic if no PDF library."""
    try:
        import PyPDF2

        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages)
    except ImportError:
        # No PDF library — read raw bytes and extract ASCII
        with open(file_path, "rb") as f:
            raw = f.read()
        # Extract printable ASCII sequences
        text_parts = re.findall(rb"[\x20-\x7e]{4,}", raw)
        return "\n".join(part.decode("ascii") for part in text_parts[:200])


def _generate_synthetic_text(file_path: str) -> str:
    """Generate deterministic synthetic text for testing."""
    seed = hashlib.sha256(file_path.encode()).hexdigest()
    paragraphs = []
    for i in range(5):
        words = []
        word_count = _hash_int(f"{seed}:p{i}:len", 30, 80)
        for j in range(word_count):
            h = _hash_int(f"{seed}:p{i}:w{j}", 0, 1000)
            words.append(_WORD_LIST[h % len(_WORD_LIST)])
        paragraphs.append(" ".join(words))
    return "\n\n".join(paragraphs)


_WORD_LIST = [
    "the",
    "analysis",
    "system",
    "data",
    "process",
    "document",
    "report",
    "results",
    "summary",
    "method",
    "approach",
    "findings",
    "research",
    "implementation",
    "framework",
    "architecture",
    "performance",
    "quality",
    "review",
    "evaluation",
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
    "protocol",
    "standard",
    "compliance",
    "requirement",
    "specification",
    "design",
    "pattern",
    "structure",
    "component",
    "interface",
    "module",
    "function",
    "parameter",
    "variable",
    "algorithm",
    "optimization",
    "validation",
    "verification",
    "assessment",
    "measurement",
    "metric",
    "threshold",
    "baseline",
    "benchmark",
    "target",
    "objective",
    "criteria",
    "constraint",
    "dependency",
    "relationship",
    "mapping",
    "transformation",
    "extraction",
    "classification",
    "categorization",
    "annotation",
    "indexing",
    "retrieval",
    "storage",
    "processing",
    "computation",
    "execution",
    "scheduling",
    "orchestration",
    "coordination",
    "synchronization",
    "distribution",
    "replication",
    "partitioning",
    "aggregation",
    "filtering",
    "sorting",
    "ranking",
    "scoring",
    "weighting",
    "normalization",
    "tokenization",
    "segmentation",
    "clustering",
    "regression",
    "prediction",
    "detection",
    "recognition",
    "generation",
    "synthesis",
    "encoding",
    "decoding",
    "compression",
    "decompression",
    "encryption",
    "authentication",
    "authorization",
    "logging",
    "tracing",
    "profiling",
    "debugging",
    "resolution",
    "mitigation",
    "escalation",
    "notification",
    "alerting",
]


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------


def split_into_chunks(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[dict]:
    """Split text into overlapping word-based chunks.

    Returns list of dicts matching TextChunk schema fields:
    [{chunk_id, text, start_offset, end_offset, word_count}, ...]
    """
    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    chunk_idx = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)

        # Calculate character offsets
        start_offset = len(" ".join(words[:start])) + (1 if start > 0 else 0)
        end_offset = start_offset + len(chunk_text)

        chunks.append(
            {
                "chunk_id": f"chunk-{chunk_idx:03d}",
                "text": chunk_text,
                "start_offset": start_offset,
                "end_offset": end_offset,
                "word_count": len(chunk_words),
            }
        )

        chunk_idx += 1
        start = end - overlap if end < len(words) else len(words)
        # Prevent infinite loop when overlap >= chunk_size
        if start <= chunks[-1]["start_offset"] and end < len(words):
            start = end

    return chunks


# ---------------------------------------------------------------------------
# Chunk summarization
# ---------------------------------------------------------------------------


def summarize_chunk(chunk_id: str, text: str) -> dict:
    """Summarize a single text chunk deterministically.

    Returns dict matching ChunkSummary schema:
    {chunk_id, summary, key_phrases, word_count}
    """
    words = text.split()

    # Extract key phrases: pick distinctive words (>5 chars, deduplicated)
    seen: set[str] = set()
    key_phrases: list[str] = []
    for w in words:
        clean = w.lower().strip(".,;:!?()[]{}\"'")
        if len(clean) > 5 and clean not in seen:
            seen.add(clean)
            key_phrases.append(clean)
        if len(key_phrases) >= 5:
            break

    # Build a deterministic summary from first and last sentences
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) >= 2:
        summary = f"{sentences[0]}. {sentences[-1]}."
    elif sentences:
        summary = f"{sentences[0]}."
    else:
        summary = text[:200]

    # Trim to reasonable length
    if len(summary) > 300:
        summary = summary[:297] + "..."

    return {
        "chunk_id": chunk_id,
        "summary": summary,
        "key_phrases": key_phrases,
        "word_count": len(words),
    }


def save_chunk_summary(file_path: str, chunk_summary: dict) -> str:
    """Save a chunk summary to the summary store for later merging.

    Returns the path where the summary was saved.
    """
    doc_hash = hashlib.sha256(file_path.encode()).hexdigest()[:12]
    store_dir = os.path.join(_SUMMARY_STORE_DIR, doc_hash)
    os.makedirs(store_dir, exist_ok=True)

    chunk_id = chunk_summary["chunk_id"]
    out_path = os.path.join(store_dir, f"{chunk_id}.json")
    with open(out_path, "w") as f:
        json.dump(chunk_summary, f)

    return out_path


def load_chunk_summaries(file_path: str) -> list[dict]:
    """Load all chunk summaries for a document from the summary store."""
    doc_hash = hashlib.sha256(file_path.encode()).hexdigest()[:12]
    store_dir = os.path.join(_SUMMARY_STORE_DIR, doc_hash)

    if not os.path.isdir(store_dir):
        return []

    summaries = []
    for fname in sorted(os.listdir(store_dir)):
        if fname.endswith(".json"):
            with open(os.path.join(store_dir, fname)) as f:
                summaries.append(json.load(f))
    return summaries


# ---------------------------------------------------------------------------
# Summary merging
# ---------------------------------------------------------------------------


def merge_summaries(file_path: str, chunk_count: int) -> dict:
    """Merge all chunk summaries into a coherent document summary.

    Reads from the summary store, combines summaries and key phrases.

    Returns dict: {merged_summary, key_phrases, total_chunks}
    """
    summaries = load_chunk_summaries(file_path)

    if not summaries:
        return {
            "merged_summary": "No chunk summaries available.",
            "key_phrases": [],
            "total_chunks": 0,
        }

    # Combine all summaries
    all_summaries = [s["summary"] for s in summaries]
    merged = " ".join(all_summaries)

    # Trim if too long
    if len(merged) > 2000:
        merged = merged[:1997] + "..."

    # Deduplicate key phrases across all chunks
    seen: set[str] = set()
    key_phrases: list[str] = []
    for s in summaries:
        for phrase in s.get("key_phrases", []):
            if phrase.lower() not in seen:
                seen.add(phrase.lower())
                key_phrases.append(phrase)

    return {
        "merged_summary": merged,
        "key_phrases": key_phrases[:15],
        "total_chunks": len(summaries),
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_document(text: str, file_path: str) -> dict:
    """Classify a document into a category deterministically.

    Uses keyword matching to assign a category.

    Returns dict matching Classification schema:
    {category, confidence, subcategory, tags}
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score

    if scores:
        best_category = max(scores, key=scores.__getitem__)
        total_matches = sum(scores.values())
        confidence = round(scores[best_category] / max(total_matches, 1), 2)
    else:
        best_category = "other"
        confidence = 0.5

    # Determine subcategory from file extension
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    subcategory_map = {
        "pdf": "formatted_document",
        "md": "markdown_document",
        "txt": "plain_text",
        "csv": "tabular_data",
        "json": "structured_data",
        "html": "web_content",
        "rst": "restructured_text",
    }
    subcategory = subcategory_map.get(ext, "unknown_format")

    # Extract tags from top keywords found
    tags: list[str] = []
    for _category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower and kw not in tags:
                tags.append(kw)
        if len(tags) >= 8:
            break

    return {
        "category": best_category,
        "confidence": confidence,
        "subcategory": subcategory,
        "tags": tags[:8],
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    file_path: str,
    file_type: str,
    category: str,
    summary: str,
    key_phrases: list[str],
    chunk_count: int,
    word_count: int,
) -> tuple[str, dict]:
    """Generate an HTML report for a processed document.

    Returns (report_path, report_dict matching DocumentReport schema).
    """
    os.makedirs(_DOC_REPORTS_DIR, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(file_path))[0]
    report_path = os.path.join(_DOC_REPORTS_DIR, f"{base_name}-report.html")

    phrases_html = "".join(f"<li>{p}</li>" for p in key_phrases)

    html = f"""<!DOCTYPE html>
<html>
<head><title>Document Report: {base_name}</title>
<style>
  body {{ font-family: sans-serif; margin: 2em; }}
  h1 {{ color: #2c3e50; }}
  .meta {{ background: #ecf0f1; padding: 1em; border-radius: 4px; margin: 1em 0; }}
  .summary {{ line-height: 1.6; }}
  .phrases {{ columns: 2; }}
</style>
</head>
<body>
<h1>Document Report: {base_name}</h1>
<div class="meta">
  <p><strong>Source:</strong> {file_path}</p>
  <p><strong>Type:</strong> {file_type}</p>
  <p><strong>Category:</strong> {category}</p>
  <p><strong>Word Count:</strong> {word_count:,}</p>
  <p><strong>Chunks Processed:</strong> {chunk_count}</p>
</div>
<h2>Summary</h2>
<div class="summary"><p>{summary}</p></div>
<h2>Key Phrases</h2>
<ul class="phrases">{phrases_html}</ul>
</body>
</html>"""

    with open(report_path, "w") as f:
        f.write(html)

    report = {
        "file_path": file_path,
        "file_type": file_type,
        "category": category,
        "summary": summary,
        "key_phrases": key_phrases,
        "chunk_count": chunk_count,
        "word_count": word_count,
        "report_path": report_path,
    }

    return report_path, report
