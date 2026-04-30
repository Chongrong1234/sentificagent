from __future__ import annotations

import re
from typing import Any

from .classifier import classify_capture
from .config import AppConfig


STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "using",
    "study",
    "based",
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _extract_query_terms(query: str) -> list[str]:
    normalized = _normalize_text(query)
    phrase_parts = [
        item.strip()
        for item in re.split(r"[,\n;|]+", normalized)
        if item.strip()
    ]
    words = [
        item
        for item in re.split(r"[^a-z0-9\-]+", normalized)
        if len(item) >= 3 and item not in STOPWORDS
    ]
    terms: list[str] = []
    for candidate in phrase_parts + words:
        if candidate and candidate not in terms:
            terms.append(candidate)
    return terms


def _field_texts(paper: dict[str, Any]) -> dict[str, str]:
    authors = " ".join(_listify(paper.get("authors")))
    affiliations = " ".join(_listify(paper.get("affiliations")))
    venue = " ".join(
        [
            str(paper.get("journal") or ""),
            str(paper.get("conference") or ""),
            str(paper.get("venue") or ""),
            str(paper.get("publisher") or ""),
            str(paper.get("source_name") or ""),
        ]
    )
    return {
        "title": _normalize_text(str(paper.get("title") or "")),
        "abstract": _normalize_text(str(paper.get("abstract") or "")),
        "keywords": _normalize_text(" ".join(_listify(paper.get("keywords")))),
        "authors": _normalize_text(f"{authors} {affiliations}"),
        "venue": _normalize_text(venue),
    }


def _config_search_section(config: AppConfig) -> dict[str, Any]:
    return config.raw.get("search", {}) or {}


def _field_weights(config: AppConfig) -> dict[str, float]:
    raw = _config_search_section(config).get("field_weights", {})
    return {
        "title": float(raw.get("title", 5.0)),
        "abstract": float(raw.get("abstract", 3.0)),
        "keywords": float(raw.get("keywords", 4.0)),
        "authors": float(raw.get("authors", 1.8)),
        "venue": float(raw.get("venue", 4.5)),
    }


def _bonus_values(config: AppConfig) -> dict[str, float]:
    raw = _config_search_section(config).get("bonuses", {})
    return {
        "topic_weight_multiplier": float(raw.get("topic_weight_multiplier", 5.0)),
        "venue_weight_multiplier": float(raw.get("venue_weight_multiplier", 6.0)),
        "venue_tier_weight_multiplier": float(raw.get("venue_tier_weight_multiplier", 4.0)),
        "team_match_bonus": float(raw.get("team_match_bonus", 2.0)),
        "pdf_available_bonus": float(raw.get("pdf_available_bonus", 1.0)),
    }


def _venue_tier_weight(config: AppConfig, venue_tier_name: str) -> float:
    for item in config.raw.get("classifier", {}).get("venue_tiers", []):
        if item.get("name") == venue_tier_name:
            return float(item.get("weight", 0.0))
    return 0.0


def score_paper_relevance(
    config: AppConfig,
    paper: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    query_terms = _extract_query_terms(query)
    field_texts = _field_texts(paper)
    weights = _field_weights(config)
    bonuses = _bonus_values(config)
    classification = classify_capture(config, paper)

    score = 0.0
    matched_fields: dict[str, list[str]] = {field: [] for field in field_texts}
    tags: list[str] = []

    for term in query_terms:
        for field_name, text in field_texts.items():
            if term and term in text:
                score += weights[field_name]
                matched_fields[field_name].append(term)

    priority_topics = config.raw.get("research_profile", {}).get("priority_topics", [])
    topic_tags: list[str] = []
    for topic in priority_topics:
        topic_name = str(topic.get("name") or "").strip()
        aliases = [topic_name] + [str(item).strip() for item in topic.get("aliases", [])]
        weight = float(topic.get("weight", 0.0))
        if any(alias and any(alias.lower() in text for text in field_texts.values()) for alias in aliases):
            score += weight * bonuses["topic_weight_multiplier"]
            if topic_name:
                topic_tags.append(topic_name)

    venue_tags: list[str] = []
    venue_text = field_texts["venue"]
    for venue in config.raw.get("research_profile", {}).get("venue_priorities", []):
        venue_name = str(venue.get("name") or "").strip()
        if venue_name and venue_name.lower() in venue_text:
            venue_tags.append(venue_name)
            score += float(venue.get("weight", 0.0)) * bonuses["venue_weight_multiplier"]

    venue_tier_weight = _venue_tier_weight(config, classification["venue_tier"])
    if venue_tier_weight > 0:
        score += venue_tier_weight * bonuses["venue_tier_weight_multiplier"]

    if classification["primary_team"] != config.default_team:
        score += bonuses["team_match_bonus"]

    if paper.get("pdf_url"):
        score += bonuses["pdf_available_bonus"]

    keyword_hits = classification.get("keyword_hits", [])
    tags.extend([f"topic:{item}" for item in topic_tags])
    tags.extend([f"venue:{item}" for item in venue_tags])
    tags.extend([f"keyword:{item}" for item in keyword_hits if item != config.default_keyword_bucket])
    if classification["venue_tier"] != config.default_venue_tier:
        tags.append(f"tier:{classification['venue_tier']}")
    if classification["primary_team"] != config.default_team:
        tags.append(f"team:{classification['primary_team']}")

    cleaned_fields = {
        field: sorted(set(values))
        for field, values in matched_fields.items()
        if values
    }

    return {
        "score": round(score, 3),
        "query_terms": query_terms,
        "matched_fields": cleaned_fields,
        "classification": classification,
        "tags": sorted(set(tags)),
    }
