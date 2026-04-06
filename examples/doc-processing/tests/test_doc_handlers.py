"""Tests for the doc-processing example handlers."""

from __future__ import annotations

import json
import os


# ---------------------------------------------------------------------------
# TestDocUtils — utility function tests
# ---------------------------------------------------------------------------
class TestDocUtils:
    def test_detect_file_type_txt(self):
        from handlers.shared.doc_utils import detect_file_type

        result = detect_file_type("/tmp/docs/readme.txt")
        assert result["file_type"] == "txt"
        assert result["file_path"] == "/tmp/docs/readme.txt"
        assert result["encoding"] == "utf-8"
        assert isinstance(result["file_size"], int)
        assert isinstance(result["page_count"], int)

    def test_detect_file_type_pdf(self):
        from handlers.shared.doc_utils import detect_file_type

        result = detect_file_type("/tmp/docs/report.pdf")
        assert result["file_type"] == "pdf"

    def test_detect_file_type_md(self):
        from handlers.shared.doc_utils import detect_file_type

        result = detect_file_type("/tmp/docs/notes.md")
        assert result["file_type"] == "md"

    def test_detect_file_type_unknown_extension(self):
        from handlers.shared.doc_utils import detect_file_type

        result = detect_file_type("/tmp/docs/data.xyz")
        assert result["file_type"] == "txt"  # fallback

    def test_detect_file_type_real_file(self, tmp_path):
        from handlers.shared.doc_utils import detect_file_type

        f = tmp_path / "test.txt"
        f.write_text("Hello world\n" * 100)
        result = detect_file_type(str(f))
        assert result["file_type"] == "txt"
        assert result["file_size"] > 0
        assert result["page_count"] >= 1

    def test_detect_file_type_deterministic(self):
        from handlers.shared.doc_utils import detect_file_type

        r1 = detect_file_type("/tmp/docs/report.pdf")
        r2 = detect_file_type("/tmp/docs/report.pdf")
        assert r1 == r2

    def test_extract_text_real_file(self, tmp_path):
        from handlers.shared.doc_utils import extract_text

        content = "This is a test document with multiple words for extraction."
        f = tmp_path / "sample.txt"
        f.write_text(content)
        result = extract_text(str(f), "txt")
        assert result["text"] == content
        assert result["word_count"] == 10
        assert result["char_count"] == len(content)
        assert "direct_read" in result["method"]

    def test_extract_text_nonexistent_file(self):
        from handlers.shared.doc_utils import extract_text

        result = extract_text("/nonexistent/file.txt", "txt")
        assert result["method"] == "synthetic"
        assert result["word_count"] > 0
        assert result["char_count"] > 0

    def test_extract_text_deterministic(self):
        from handlers.shared.doc_utils import extract_text

        r1 = extract_text("/nonexistent/file.txt", "txt")
        r2 = extract_text("/nonexistent/file.txt", "txt")
        assert r1 == r2

    def test_split_into_chunks_basic(self):
        from handlers.shared.doc_utils import split_into_chunks

        text = " ".join(f"word{i}" for i in range(50))
        chunks = split_into_chunks(text, chunk_size=20, overlap=5)
        assert len(chunks) >= 2
        assert chunks[0]["chunk_id"] == "chunk-000"
        assert chunks[0]["word_count"] == 20
        assert chunks[1]["chunk_id"] == "chunk-001"

    def test_split_into_chunks_empty(self):
        from handlers.shared.doc_utils import split_into_chunks

        chunks = split_into_chunks("", chunk_size=10)
        assert chunks == []

    def test_split_into_chunks_small_text(self):
        from handlers.shared.doc_utils import split_into_chunks

        chunks = split_into_chunks("hello world", chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0]["word_count"] == 2

    def test_split_into_chunks_overlap(self):
        from handlers.shared.doc_utils import split_into_chunks

        text = " ".join(f"word{i}" for i in range(30))
        chunks = split_into_chunks(text, chunk_size=15, overlap=5)
        assert len(chunks) >= 2
        # Check overlap: last words of chunk 0 should appear in chunk 1
        words_0 = chunks[0]["text"].split()
        words_1 = chunks[1]["text"].split()
        overlap_words = set(words_0[-5:]) & set(words_1[:5])
        assert len(overlap_words) > 0

    def test_summarize_chunk_returns_fields(self):
        from handlers.shared.doc_utils import summarize_chunk

        text = "The analysis framework provides comprehensive evaluation metrics. Performance benchmarks indicate significant improvement."
        result = summarize_chunk("chunk-000", text)
        assert result["chunk_id"] == "chunk-000"
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0
        assert isinstance(result["key_phrases"], list)
        assert result["word_count"] > 0

    def test_summarize_chunk_deterministic(self):
        from handlers.shared.doc_utils import summarize_chunk

        text = "Research methodology demonstrates robust findings across multiple domains."
        r1 = summarize_chunk("chunk-001", text)
        r2 = summarize_chunk("chunk-001", text)
        assert r1 == r2

    def test_summarize_chunk_key_phrases(self):
        from handlers.shared.doc_utils import summarize_chunk

        text = "The implementation architecture uses optimization algorithms for performance evaluation and verification testing."
        result = summarize_chunk("chunk-002", text)
        assert len(result["key_phrases"]) >= 1
        assert all(len(p) > 5 for p in result["key_phrases"])

    def test_save_and_load_chunk_summaries(self, tmp_path, monkeypatch):
        from handlers.shared import doc_utils
        from handlers.shared.doc_utils import (
            load_chunk_summaries,
            save_chunk_summary,
        )

        monkeypatch.setattr(doc_utils, "_SUMMARY_STORE_DIR", str(tmp_path / "store"))

        summary1 = {
            "chunk_id": "chunk-000",
            "summary": "First chunk.",
            "key_phrases": ["analysis"],
            "word_count": 10,
        }
        summary2 = {
            "chunk_id": "chunk-001",
            "summary": "Second chunk.",
            "key_phrases": ["system"],
            "word_count": 15,
        }

        save_chunk_summary("/tmp/test.txt", summary1)
        save_chunk_summary("/tmp/test.txt", summary2)

        loaded = load_chunk_summaries("/tmp/test.txt")
        assert len(loaded) == 2
        assert loaded[0]["chunk_id"] == "chunk-000"
        assert loaded[1]["chunk_id"] == "chunk-001"

    def test_merge_summaries(self, tmp_path, monkeypatch):
        from handlers.shared import doc_utils
        from handlers.shared.doc_utils import merge_summaries, save_chunk_summary

        monkeypatch.setattr(doc_utils, "_SUMMARY_STORE_DIR", str(tmp_path / "store"))

        save_chunk_summary(
            "/tmp/merge-test.txt",
            {
                "chunk_id": "chunk-000",
                "summary": "First summary.",
                "key_phrases": ["analysis", "framework"],
                "word_count": 10,
            },
        )
        save_chunk_summary(
            "/tmp/merge-test.txt",
            {
                "chunk_id": "chunk-001",
                "summary": "Second summary.",
                "key_phrases": ["system", "analysis"],
                "word_count": 15,
            },
        )

        result = merge_summaries("/tmp/merge-test.txt", 2)
        assert "First summary" in result["merged_summary"]
        assert "Second summary" in result["merged_summary"]
        assert result["total_chunks"] == 2
        # "analysis" should be deduplicated
        assert result["key_phrases"].count("analysis") == 1

    def test_merge_summaries_empty(self):
        from handlers.shared.doc_utils import merge_summaries

        result = merge_summaries("/nonexistent/path.txt", 0)
        assert result["total_chunks"] == 0
        assert result["key_phrases"] == []

    def test_classify_document_technical(self):
        from handlers.shared.doc_utils import classify_document

        text = (
            "The API function implements a modular system architecture with algorithm optimization."
        )
        result = classify_document(text, "spec.md")
        assert result["category"] == "technical"
        assert result["confidence"] > 0
        assert isinstance(result["tags"], list)
        assert result["subcategory"] == "markdown_document"

    def test_classify_document_financial(self):
        from handlers.shared.doc_utils import classify_document

        text = (
            "Q3 revenue exceeded budget forecasts. Profit margins improved with investment returns."
        )
        result = classify_document(text, "q3-report.pdf")
        assert result["category"] == "financial"
        assert result["subcategory"] == "formatted_document"

    def test_classify_document_unknown(self):
        from handlers.shared.doc_utils import classify_document

        text = "xyzzy plugh nothing relevant here"
        result = classify_document(text, "mystery.txt")
        assert result["category"] == "other"

    def test_generate_report(self, tmp_path, monkeypatch):
        from handlers.shared import doc_utils
        from handlers.shared.doc_utils import generate_report

        monkeypatch.setattr(doc_utils, "_DOC_REPORTS_DIR", str(tmp_path / "reports"))

        report_path, report = generate_report(
            file_path="/tmp/test.txt",
            file_type="txt",
            category="technical",
            summary="A technical document about systems.",
            key_phrases=["system", "architecture"],
            chunk_count=3,
            word_count=500,
        )
        assert report_path.endswith(".html")
        assert os.path.isfile(report_path)
        assert report["category"] == "technical"
        assert report["chunk_count"] == 3
        assert report["word_count"] == 500

        with open(report_path) as f:
            html = f.read()
        assert "technical" in html
        assert "system" in html


# ---------------------------------------------------------------------------
# TestDetectionHandlers — detection handler wrapper tests
# ---------------------------------------------------------------------------
class TestDetectionHandlers:
    def test_handle_detect_file_type(self):
        from handlers.detection.detection_handlers import handle_detect_file_type

        result = handle_detect_file_type({"file_path": "/tmp/docs/readme.txt"})
        assert "info" in result
        assert result["info"]["file_type"] == "txt"

    def test_handle_detect_file_type_step_log(self):
        from handlers.detection.detection_handlers import handle_detect_file_type

        messages = []
        handle_detect_file_type(
            {
                "file_path": "/tmp/docs/readme.txt",
                "_step_log": lambda msg, level: messages.append((msg, level)),
            }
        )
        assert len(messages) == 1
        assert "Detected" in messages[0][0]

    def test_dispatch_keys(self):
        from handlers.detection.detection_handlers import _DISPATCH

        assert "doc.Detection.DetectFileType" in _DISPATCH

    def test_handle_dispatches(self):
        from handlers.detection.detection_handlers import handle

        result = handle(
            {"_facet_name": "doc.Detection.DetectFileType", "file_path": "/tmp/test.txt"}
        )
        assert "info" in result


# ---------------------------------------------------------------------------
# TestExtractionHandlers
# ---------------------------------------------------------------------------
class TestExtractionHandlers:
    def test_handle_extract_text(self):
        from handlers.extraction.extraction_handlers import handle_extract_text

        result = handle_extract_text({"file_path": "/tmp/test.txt", "file_type": "txt"})
        assert "extracted" in result
        assert result["extracted"]["word_count"] > 0

    def test_dispatch_keys(self):
        from handlers.extraction.extraction_handlers import _DISPATCH

        assert "doc.Extraction.ExtractText" in _DISPATCH


# ---------------------------------------------------------------------------
# TestChunkingHandlers
# ---------------------------------------------------------------------------
class TestChunkingHandlers:
    def test_handle_split_into_chunks(self):
        from handlers.chunking.chunking_handlers import handle_split_into_chunks

        text = " ".join(f"word{i}" for i in range(50))
        result = handle_split_into_chunks({"text": text, "chunk_size": 20, "overlap": 5})
        assert "chunks" in result
        assert "chunk_count" in result
        assert len(result["chunks"]) >= 2
        assert result["chunk_count"] == len(result["chunks"])

    def test_dispatch_keys(self):
        from handlers.chunking.chunking_handlers import _DISPATCH

        assert "doc.Chunking.SplitIntoChunks" in _DISPATCH


# ---------------------------------------------------------------------------
# TestSummarizationHandlers
# ---------------------------------------------------------------------------
class TestSummarizationHandlers:
    def test_handle_summarize_chunk(self):
        from handlers.summarization.summarization_handlers import handle_summarize_chunk

        result = handle_summarize_chunk(
            {
                "chunk_id": "chunk-000",
                "text": "The system architecture provides robust performance optimization.",
                "file_path": "",
            }
        )
        assert "summary" in result
        assert result["summary"]["chunk_id"] == "chunk-000"

    def test_handle_merge_summaries(self, tmp_path, monkeypatch):
        from handlers.shared import doc_utils
        from handlers.shared.doc_utils import save_chunk_summary
        from handlers.summarization.summarization_handlers import handle_merge_summaries

        monkeypatch.setattr(doc_utils, "_SUMMARY_STORE_DIR", str(tmp_path / "store"))

        save_chunk_summary(
            "/tmp/merge.txt",
            {
                "chunk_id": "chunk-000",
                "summary": "Test summary.",
                "key_phrases": ["test"],
                "word_count": 5,
            },
        )

        result = handle_merge_summaries({"file_path": "/tmp/merge.txt", "chunk_count": 1})
        assert "merged_summary" in result
        assert result["total_chunks"] == 1

    def test_dispatch_keys(self):
        from handlers.summarization.summarization_handlers import _DISPATCH

        assert "doc.Summarization.SummarizeChunk" in _DISPATCH
        assert "doc.Summarization.MergeSummaries" in _DISPATCH


# ---------------------------------------------------------------------------
# TestClassificationHandlers
# ---------------------------------------------------------------------------
class TestClassificationHandlers:
    def test_handle_classify_document(self):
        from handlers.classification.classification_handlers import handle_classify_document

        result = handle_classify_document(
            {
                "text": "The contract agreement between parties hereby establishes the terms.",
                "file_path": "/tmp/contract.pdf",
            }
        )
        assert "classification" in result
        assert result["classification"]["category"] == "legal"

    def test_dispatch_keys(self):
        from handlers.classification.classification_handlers import _DISPATCH

        assert "doc.Classification.ClassifyDocument" in _DISPATCH


# ---------------------------------------------------------------------------
# TestReportingHandlers
# ---------------------------------------------------------------------------
class TestReportingHandlers:
    def test_handle_generate_report(self, tmp_path, monkeypatch):
        from handlers.reporting.reporting_handlers import handle_generate_report
        from handlers.shared import doc_utils

        monkeypatch.setattr(doc_utils, "_DOC_REPORTS_DIR", str(tmp_path / "reports"))

        result = handle_generate_report(
            {
                "file_path": "/tmp/test.txt",
                "file_type": "txt",
                "category": "technical",
                "summary": "A test summary.",
                "key_phrases": json.dumps(["test", "summary"]),
                "chunk_count": "2",
                "word_count": "100",
            }
        )
        assert "report" in result
        assert result["report"]["report_path"].endswith(".html")

    def test_dispatch_keys(self):
        from handlers.reporting.reporting_handlers import _DISPATCH

        assert "doc.Reporting.GenerateReport" in _DISPATCH


# ---------------------------------------------------------------------------
# TestRegistration — verify all handlers register correctly
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_register_all_handlers(self):
        from unittest.mock import MagicMock

        from handlers import register_all_handlers

        poller = MagicMock()
        register_all_handlers(poller)
        # 7 handlers total: DetectFileType, ExtractText, SplitIntoChunks,
        # SummarizeChunk, MergeSummaries, ClassifyDocument, GenerateReport
        assert poller.register.call_count == 7

    def test_register_all_registry_handlers(self):
        from unittest.mock import MagicMock

        from handlers import register_all_registry_handlers

        runner = MagicMock()
        register_all_registry_handlers(runner)
        assert runner.register_handler.call_count == 7
