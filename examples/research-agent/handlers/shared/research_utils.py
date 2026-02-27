"""Core research utilities — deterministic stubs for testing.

Uses only hashlib and json from stdlib. 8 functions that produce
consistent, reproducible output from the same inputs.

Designed for real LLM dispatch when an Anthropic client is available;
these stubs provide synthetic fallback for testing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _hash_int(seed: str, low: int = 0, high: int = 100) -> int:
    """Deterministic integer from seed string."""
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return low + (h % (high - low + 1))


def _hash_float(seed: str, low: float = 0.0, high: float = 1.0) -> float:
    """Deterministic float from seed string."""
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    ratio = h / 0xFFFFFFFF
    return low + ratio * (high - low)


def plan_topic(topic: str, depth: int = 3, max_subtopics: int = 5) -> dict[str, Any]:
    """Plan a research topic — returns a Topic dict.

    Keywords are deterministically generated from the topic hash.
    """
    h = hashlib.md5(topic.encode()).hexdigest()
    keywords = [f"kw_{h[i:i+4]}" for i in range(0, min(depth * 4, 16), 4)]
    return {
        "name": topic,
        "depth": depth,
        "keywords": keywords,
        "summary": f"Research plan for '{topic}' at depth {depth}",
        "max_subtopics": max_subtopics,
    }


def decompose_topic(topic: dict[str, Any], max_subtopics: int = 5) -> list[dict[str, Any]]:
    """Decompose a topic into subtopics."""
    name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
    subtopics = []
    for i in range(max_subtopics):
        h = hashlib.md5(f"{name}_sub_{i}".encode()).hexdigest()
        subtopics.append({
            "name": f"{name}_subtopic_{i}",
            "parent_topic": name,
            "description": f"Investigation of aspect {i} of {name} (hash: {h[:6]})",
            "priority": max_subtopics - i,
        })
    return subtopics


def gather_sources(subtopic: dict[str, Any], max_sources: int = 5) -> list[dict[str, Any]]:
    """Gather sources for a subtopic (capped at 5)."""
    name = subtopic.get("name", "unknown") if isinstance(subtopic, dict) else str(subtopic)
    count = min(max_sources, 5)
    sources = []
    for i in range(count):
        seed = f"{name}_source_{i}"
        sources.append({
            "title": f"Source {i}: {name}",
            "url": f"https://example.com/research/{hashlib.md5(seed.encode()).hexdigest()[:8]}",
            "relevance_score": round(_hash_float(seed, 0.5, 1.0), 4),
            "source_type": ["journal", "conference", "book", "report", "website"][i % 5],
        })
    return sources


def extract_findings(subtopic: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract findings from sources for a subtopic."""
    name = subtopic.get("name", "unknown") if isinstance(subtopic, dict) else str(subtopic)
    findings = []
    for i, source in enumerate(sources):
        seed = f"{name}_finding_{i}"
        title = source.get("title", f"source_{i}") if isinstance(source, dict) else str(source)
        findings.append({
            "claim": f"Finding {i} from {name}: evidence suggests correlation (seed: {seed[:20]})",
            "evidence": f"Based on analysis of {title}",
            "confidence": round(_hash_float(seed, 0.4, 0.95), 2),
            "source_title": title,
        })
    return findings


def synthesize_findings(topic: dict[str, Any], all_findings: list[Any]) -> dict[str, Any]:
    """Synthesize findings into an analysis.

    Flattens nested finding lists, identifies themes, contradictions, and gaps.
    """
    name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)

    # Flatten nested findings lists
    flat_findings: list[Any] = []
    for item in all_findings:
        if isinstance(item, list):
            flat_findings.extend(item)
        elif isinstance(item, str):
            try:
                parsed = json.loads(item)
                if isinstance(parsed, list):
                    flat_findings.extend(parsed)
                else:
                    flat_findings.append(parsed)
            except (json.JSONDecodeError, TypeError):
                flat_findings.append({"claim": item})
        else:
            flat_findings.append(item)

    n = len(flat_findings)
    seed = f"{name}_synth_{n}"

    themes = [f"Theme {i}: Pattern in {name}" for i in range(min(n, 3))]
    contradictions = [f"Contradiction {i}: Conflicting evidence" for i in range(min(n // 3, 2))]
    gaps = [f"Gap {i}: Insufficient data" for i in range(min(n // 2, 3))]

    return {
        "themes": themes,
        "contradictions": contradictions,
        "gaps": gaps,
        "summary": f"Synthesis of {n} findings for '{name}': {len(themes)} themes identified",
        "confidence_score": round(_hash_float(seed, 0.5, 0.9), 2),
    }


def identify_gaps(
    analysis: dict[str, Any], topic: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Identify research gaps and recommendations.

    Returns (gaps_list, recommendations_list).
    """
    name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
    existing_gaps = analysis.get("gaps", []) if isinstance(analysis, dict) else []

    gaps = []
    for i, g in enumerate(existing_gaps):
        seed = f"{name}_gap_{i}"
        gaps.append({
            "description": g if isinstance(g, str) else f"Gap {i} in {name}",
            "severity": ["high", "medium", "low"][i % 3],
            "area": f"area_{_hash_int(seed, 0, 5)}",
        })

    # Add a gap if none exist
    if not gaps:
        gaps.append({
            "description": f"No gaps identified for {name} — recommend deeper analysis",
            "severity": "low",
            "area": "methodology",
        })

    recommendations = []
    for i in range(len(gaps)):
        seed = f"{name}_rec_{i}"
        recommendations.append({
            "action": f"Investigate {gaps[i]['description'][:50]}",
            "priority": ["high", "medium", "low"][i % 3],
            "estimated_effort": ["low", "medium", "high"][i % 3],
        })

    return gaps, recommendations


def draft_report(
    topic: dict[str, Any], analysis: dict[str, Any], gaps: list[Any]
) -> dict[str, Any]:
    """Draft a research report with 5 sections."""
    name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
    themes = analysis.get("themes", []) if isinstance(analysis, dict) else []
    summary = analysis.get("summary", "") if isinstance(analysis, dict) else str(analysis)

    sections = [
        {"title": "Introduction", "content": f"This report examines {name}."},
        {"title": "Methodology", "content": f"Analysis covered {len(themes)} themes."},
        {"title": "Findings", "content": summary},
        {"title": "Discussion", "content": f"Key gaps: {len(gaps) if isinstance(gaps, list) else 0} identified."},
        {"title": "Conclusion", "content": f"Research on {name} reveals significant insights."},
    ]

    word_count = sum(len(s["content"].split()) for s in sections)
    citations = [f"[{i+1}] {t}" for i, t in enumerate(themes[:5])]

    return {
        "title": f"Research Report: {name}",
        "sections": sections,
        "word_count": word_count,
        "citations": citations,
    }


def review_draft(
    draft: dict[str, Any], topic: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    """Review a draft report — score range 55-94, approved if >= 70."""
    name = topic.get("name", "unknown") if isinstance(topic, dict) else str(topic)
    title = draft.get("title", "Untitled") if isinstance(draft, dict) else str(draft)

    seed = f"{name}_{title}_review"
    score = _hash_int(seed, 55, 94)

    feedback = []
    if score < 70:
        feedback.append("Needs significant revision: strengthen evidence")
        feedback.append("Consider adding more recent sources")
    else:
        feedback.append("Well-structured analysis")
        if score >= 85:
            feedback.append("Excellent synthesis of findings")

    suggested_edits = []
    if score < 80:
        suggested_edits.append({"section": "Methodology", "suggestion": "Add sample size details"})
    if score < 70:
        suggested_edits.append({"section": "Findings", "suggestion": "Strengthen evidence chain"})

    return {
        "score": score,
        "approved": score >= 70,
        "feedback": feedback,
        "suggested_edits": suggested_edits,
    }
