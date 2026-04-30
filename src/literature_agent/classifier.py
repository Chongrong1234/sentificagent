from __future__ import annotations

import re
from typing import Any

from .config import AppConfig


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict):
                if item.get("name"):
                    items.append(str(item["name"]))
                if item.get("affiliation"):
                    items.append(str(item["affiliation"]))
        return items
    return [str(value)]


def _paper_venue_candidates(paper: dict[str, Any]) -> list[str]:
    keys = ["journal", "conference", "venue", "publisher", "site_name"]
    return [str(paper.get(key, "")) for key in keys if paper.get(key)]


def _paper_year(paper: dict[str, Any]) -> str:
    value = str(paper.get("year", "")).strip()
    if re.fullmatch(r"\d{4}", value):
        return value
    match = re.search(r"(19|20)\d{2}", value)
    if match:
        return match.group(0)
    return "unknown-year"


def _author_and_affiliation_text(paper: dict[str, Any]) -> str:
    author_chunks = _listify(paper.get("authors"))
    affiliation_chunks = _listify(paper.get("affiliations"))
    return _normalize_text(" ".join(author_chunks + affiliation_chunks))


def _keyword_text(paper: dict[str, Any]) -> str:
    parts = [
        str(paper.get("title", "")),
        str(paper.get("abstract", "")),
        " ".join(_listify(paper.get("keywords"))),
    ]
    return _normalize_text(" ".join(parts))


def _match_venue_tier(config: AppConfig, paper: dict[str, Any], overrides: dict[str, Any]) -> str:
    manual_value = str(overrides.get("venue_tier", "")).strip()
    if manual_value:
        return manual_value

    venue_text = _normalize_text(" ".join(_paper_venue_candidates(paper)))
    rules = config.raw.get("classifier", {}).get("venue_tiers", [])
    for rule in rules:
        rule_name = str(rule.get("name", "")).strip()
        tokens = rule.get("match", {}).get("venues", [])
        if not rule_name:
            continue
        if any(_normalize_text(token) in venue_text for token in tokens):
            return rule_name
    return config.default_venue_tier


def _match_research_teams(config: AppConfig, paper: dict[str, Any], overrides: dict[str, Any]) -> list[str]:
    teams: list[str] = []
    manual_value = str(overrides.get("team", "")).strip()
    if manual_value:
        teams.append(manual_value)

    author_text = _author_and_affiliation_text(paper)
    rules = config.raw.get("classifier", {}).get("research_teams", [])
    for rule in rules:
        rule_name = str(rule.get("name", "")).strip()
        match = rule.get("match", {})
        author_tokens = match.get("authors", [])
        affiliation_tokens = match.get("affiliations", [])
        if not rule_name:
            continue
        matched_author = any(_normalize_text(token) in author_text for token in author_tokens)
        matched_affiliation = any(
            _normalize_text(token) in author_text for token in affiliation_tokens
        )
        if matched_author or matched_affiliation:
            teams.append(rule_name)

    return _unique_preserve_order(teams) or [config.default_team]


def _match_keyword_hits(config: AppConfig, paper: dict[str, Any], overrides: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    manual_values = overrides.get("keywords", [])
    if isinstance(manual_values, str):
        manual_values = [manual_values]
    hits.extend([str(item).strip() for item in manual_values if str(item).strip()])

    keyword_text = _keyword_text(paper)
    rules = config.raw.get("classifier", {}).get("keyword_buckets", [])
    for rule in rules:
        rule_name = str(rule.get("name", "")).strip()
        tokens = rule.get("keywords", [])
        if not rule_name:
            continue
        if any(_normalize_text(token) in keyword_text for token in tokens):
            hits.append(rule_name)

    return _unique_preserve_order(hits) or [config.default_keyword_bucket]


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def classify_capture(
    config: AppConfig,
    paper: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    venue_tier = _match_venue_tier(config, paper, overrides)
    research_teams = _match_research_teams(config, paper, overrides)
    keyword_hits = _match_keyword_hits(config, paper, overrides)

    return {
        "venue_tier": venue_tier,
        "research_teams": research_teams,
        "keyword_hits": keyword_hits,
        "primary_team": research_teams[0],
        "primary_keyword": keyword_hits[0],
        "year": _paper_year(paper),
    }
