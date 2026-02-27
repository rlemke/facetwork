"""Core debate utilities — deterministic stubs for testing.

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


_STANCES = ["for", "against", "neutral"]
_PERSONAS = ["proposer", "critic", "synthesizer"]


def frame_debate(topic: str, num_agents: int = 3) -> dict[str, Any]:
    """Frame a debate topic — returns topic_analysis, positions, stakes."""
    h = hashlib.md5(topic.encode()).hexdigest()
    positions = []
    for i in range(num_agents):
        stance = _STANCES[i % len(_STANCES)]
        positions.append({
            "stance": stance,
            "rationale": f"Position {i} on '{topic}': {stance} (hash: {h[i*2:i*2+4]})",
            "priority": num_agents - i,
        })
    return {
        "topic_analysis": f"Analysis of '{topic}': {num_agents} positions identified covering {', '.join(s['stance'] for s in positions)}",
        "positions": positions,
        "stakes": f"Key stakes in '{topic}': policy impact, public perception, resource allocation",
    }


def assign_roles(topic_analysis: str, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign debate roles to agents based on positions."""
    assignments = []
    for i, pos in enumerate(positions):
        persona = _PERSONAS[i % len(_PERSONAS)]
        seed = f"{topic_analysis}_{i}"
        assignments.append({
            "persona": persona,
            "expertise": ["advocacy", "analysis", "integration"][i % 3],
            "position": pos.get("stance", "neutral"),
            "agent_id": f"agent_{i}",
            "seed": hashlib.md5(seed.encode()).hexdigest()[:8],
        })
    return assignments


def generate_argument(role: dict[str, Any], topic: str, context: str = "") -> dict[str, Any]:
    """Generate an argument for the assigned position."""
    persona = role.get("persona", "neutral") if isinstance(role, dict) else "neutral"
    position = role.get("position", "neutral") if isinstance(role, dict) else "neutral"
    seed = f"{persona}_{topic}_{context}"
    confidence = round(_hash_float(seed, 0.4, 0.95), 2)
    claims = [
        f"Claim {i} by {persona}: {topic} supports {position} (seed: {seed[:12]})"
        for i in range(3)
    ]
    evidence = [
        f"Evidence {i}: empirical data supports claim {i} (hash: {hashlib.md5(f'{seed}_{i}'.encode()).hexdigest()[:6]})"
        for i in range(3)
    ]
    return {
        "agent_role": persona,
        "position": position,
        "claims": claims,
        "evidence": evidence,
        "confidence": confidence,
    }


def generate_rebuttal(role: dict[str, Any], arguments: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a rebuttal to opposing arguments."""
    persona = role.get("persona", "neutral") if isinstance(role, dict) else "neutral"
    target_role = "unknown"
    if arguments:
        first_arg = arguments[0] if isinstance(arguments[0], dict) else {}
        target_role = first_arg.get("agent_role", "unknown")
    seed = f"{persona}_rebuttal_{len(arguments)}"
    strength = round(_hash_float(seed, 0.3, 0.9), 2)
    counter_claims = [
        f"Counter by {persona}: opposing claim {i} has insufficient evidence"
        for i in range(min(len(arguments), 3))
    ]
    if not counter_claims:
        counter_claims = [f"Counter by {persona}: no arguments to rebut"]
    weaknesses = [
        f"Weakness {i}: methodology gap in opponent's evidence"
        for i in range(min(len(arguments), 2))
    ]
    if not weaknesses:
        weaknesses = [f"Weakness: no opposing arguments provided"]
    return {
        "agent_role": persona,
        "target_role": target_role,
        "counter_claims": counter_claims,
        "weaknesses": weaknesses,
        "strength": strength,
    }


def score_arguments(
    arguments: list[dict[str, Any]], rebuttals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Score each argument on clarity, evidence quality, and persuasiveness."""
    scores = []
    for i, arg in enumerate(arguments):
        agent_role = arg.get("agent_role", f"agent_{i}") if isinstance(arg, dict) else f"agent_{i}"
        seed = f"{agent_role}_score_{i}_{len(rebuttals)}"
        clarity = _hash_int(f"{seed}_clarity", 40, 95)
        evidence_quality = _hash_int(f"{seed}_evidence", 35, 90)
        persuasiveness = _hash_int(f"{seed}_persuasion", 30, 95)
        overall = (clarity + evidence_quality + persuasiveness) // 3
        scores.append({
            "agent_role": agent_role,
            "clarity": clarity,
            "evidence_quality": evidence_quality,
            "persuasiveness": persuasiveness,
            "overall": overall,
        })
    return scores


def judge_debate(topic: str, synthesis: str, scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge the debate — pick a winner based on scores."""
    if not scores:
        return {
            "winner": "none",
            "margin": 0.0,
            "rationale": f"No scores available for debate on '{topic}'",
            "dissenting_points": [],
        }
    best = max(scores, key=lambda s: s.get("overall", 0) if isinstance(s, dict) else 0)
    second_best = sorted(
        scores,
        key=lambda s: s.get("overall", 0) if isinstance(s, dict) else 0,
        reverse=True,
    )
    margin = 0.0
    if len(second_best) >= 2:
        top_score = second_best[0].get("overall", 0) if isinstance(second_best[0], dict) else 0
        runner_up = second_best[1].get("overall", 0) if isinstance(second_best[1], dict) else 0
        margin = round((top_score - runner_up) / max(top_score, 1) * 100, 1)
    winner = best.get("agent_role", "unknown") if isinstance(best, dict) else "unknown"
    dissenting = [
        f"Dissent: {s.get('agent_role', '?')} scored within 10 points"
        for s in scores
        if isinstance(s, dict) and s.get("agent_role") != winner
        and abs(s.get("overall", 0) - best.get("overall", 0)) <= 10
    ]
    return {
        "winner": winner,
        "margin": margin,
        "rationale": f"Winner '{winner}' on '{topic}' with strongest overall score ({best.get('overall', 0)}). {synthesis[:60]}",
        "dissenting_points": dissenting,
    }


def synthesize_positions(
    arguments: list[dict[str, Any]],
    rebuttals: list[dict[str, Any]],
    scores: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Synthesize all arguments, rebuttals, and scores into a unified analysis."""
    n_args = len(arguments)
    n_rebuttals = len(rebuttals)
    n_scores = len(scores)

    roles = []
    for a in arguments:
        if isinstance(a, dict):
            roles.append(a.get("agent_role", "unknown"))

    themes = [
        f"Theme: evidence-based reasoning across {n_args} agents",
        f"Theme: rebuttal quality varied across {n_rebuttals} responses",
        f"Theme: scoring consistency with {n_scores} evaluations",
    ]
    if n_args > 2:
        themes.append("Theme: multi-perspective coverage achieved")

    synthesis = (
        f"Debate synthesis: {n_args} arguments from {', '.join(roles) if roles else 'agents'}, "
        f"{n_rebuttals} rebuttals exchanged, {n_scores} score sets evaluated. "
        f"Key themes: {'; '.join(themes[:2])}"
    )
    return synthesis, themes


def build_consensus(
    verdict: dict[str, Any], synthesis: str, themes: list[Any]
) -> dict[str, Any]:
    """Build a consensus result from the verdict and synthesis."""
    winner = verdict.get("winner", "unknown") if isinstance(verdict, dict) else "unknown"
    margin = verdict.get("margin", 0.0) if isinstance(verdict, dict) else 0.0
    seed = f"{winner}_{len(themes)}"
    agreement_level = round(_hash_float(seed, 0.2, 0.9), 2)
    common_ground = [
        f"All agents agree on the importance of evidence-based claims",
        f"Shared recognition of complexity in the debate topic",
    ]
    if agreement_level > 0.6:
        common_ground.append("Convergence on key policy recommendations")
    unresolved = [
        f"Disagreement on methodology: margin was {margin}%",
    ]
    if agreement_level < 0.5:
        unresolved.append("Fundamental disagreement on premises remains")
    return {
        "agreement_level": agreement_level,
        "common_ground": common_ground,
        "unresolved": unresolved,
        "summary": f"Consensus by '{winner}' (agreement: {agreement_level}): {synthesis[:80]}",
    }
