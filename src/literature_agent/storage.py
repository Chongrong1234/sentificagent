from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib import request

from .classifier import classify_capture
from .config import AppConfig
from .library_store import record_discovered_papers


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized or "paper"


def _safe_path_part(value: str) -> str:
    return slugify(value) or "unknown"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def _render_route(
    config: AppConfig,
    classification: dict[str, Any],
    paper: dict[str, Any],
    slug: str,
) -> str:
    source_domain = _first_non_empty(
        paper.get("source_domain"),
        paper.get("site_name"),
        "unknown-source",
    )
    variables = {
        "venue_tier": _safe_path_part(classification["venue_tier"]),
        "primary_team": _safe_path_part(classification["primary_team"]),
        "keyword_bucket": _safe_path_part(classification["primary_keyword"]),
        "year": _safe_path_part(classification["year"]),
        "slug": slug,
        "source": _safe_path_part(source_domain),
    }
    route = config.path_template.format(**variables)
    return str(PurePosixPath(*[part for part in route.split("/") if part]))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _build_record_paths(
    config: AppConfig,
    paper: dict[str, Any],
    classification: dict[str, Any],
    slug: str,
) -> dict[str, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    route = _render_route(config, classification, paper, slug)
    record_dir = config.records_dir / Path(route) / slug
    return {
        "inbox": config.inbox_dir / f"{timestamp}-{slug}.json",
        "record_dir": record_dir,
        "metadata": record_dir / "metadata.json",
        "pdf": record_dir / "paper.pdf",
        "route": Path(route),
    }


def build_download_plan(
    config: AppConfig,
    paper: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = classify_capture(config, paper, overrides or {})
    title_seed = _first_non_empty(paper.get("title"), paper.get("doi"), paper.get("page_url"), "paper")
    slug = slugify(title_seed)
    paths = _build_record_paths(config, paper, classification, slug)
    route = str(paths["route"]).replace("\\", "/")
    return {
        "classification": classification,
        "relative_dir": route,
        "suggested_filename": str(
            PurePosixPath(config.browser_download_root) / route / f"{slug}.pdf"
        ),
        "record_pdf_path": str(paths["pdf"]),
    }


def persist_capture(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    paper = payload.get("paper", {})
    overrides = payload.get("overrides", {}) or {}
    classification = classify_capture(config, paper, overrides)
    captured_at = payload.get("captured_at") or datetime.now(timezone.utc).isoformat()

    title_seed = _first_non_empty(paper.get("title"), paper.get("doi"), paper.get("page_url"), "paper")
    slug = slugify(title_seed)
    paths = _build_record_paths(config, paper, classification, slug)
    route = str(paths["route"]).replace("\\", "/")

    download_plan = build_download_plan(config, paper, overrides)

    record = {
        "captured_at": captured_at,
        "capture_source": payload.get("source", {}),
        "paper": paper,
        "overrides": overrides,
        "classification": classification,
        "download_plan": download_plan,
    }

    _write_json(paths["inbox"], payload)
    _write_json(paths["metadata"], record)
    record_discovered_papers(config, "browser-capture", [paper])

    return {
        "status": "ok",
        "classification": classification,
        "download_plan": download_plan,
        "paths": {
            "inbox": str(paths["inbox"]),
            "metadata": str(paths["metadata"]),
        },
    }


def persist_library_paper(
    config: AppConfig,
    paper: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_overrides = overrides or {}
    classification = classify_capture(config, paper, resolved_overrides)
    title_seed = _first_non_empty(paper.get("title"), paper.get("doi"), paper.get("page_url"), "paper")
    slug = slugify(title_seed)
    paths = _build_record_paths(config, paper, classification, slug)
    record = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_source": source or {"trigger": "local-service"},
        "paper": paper,
        "overrides": resolved_overrides,
        "classification": classification,
        "download_plan": build_download_plan(config, paper, resolved_overrides),
    }
    _write_json(paths["metadata"], record)
    record_discovered_papers(config, str((source or {}).get("trigger") or "local-service"), [paper])
    return {
        "classification": classification,
        "paths": {
            "metadata": str(paths["metadata"]),
            "record_dir": str(paths["record_dir"]),
            "pdf": str(paths["pdf"]),
        },
    }


def download_pdf_to_record(pdf_url: str, destination: str | Path) -> dict[str, Any]:
    target_path = Path(destination)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(pdf_url, headers={"User-Agent": "scientific-agent/0.1"})
    try:
        with request.urlopen(req, timeout=120) as response, target_path.open("wb") as handle:
            handle.write(response.read())
    except Exception as exc:
        return {
            "status": "failed",
            "reason": str(exc),
            "pdf_url": pdf_url,
        }
    return {
        "status": "downloaded",
        "path": str(target_path),
        "pdf_url": pdf_url,
    }


def persist_search_run(config: AppConfig, payload: dict[str, Any]) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.search_runs_dir / f"{timestamp}.json"
    _write_json(path, payload)
    return path


def queue_download_batch(config: AppConfig, payload: dict[str, Any]) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.queue_dir / f"{timestamp}.json"
    _write_json(path, payload)
    return path


def list_download_batches(config: AppConfig) -> list[dict[str, Any]]:
    if not config.queue_dir.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(config.queue_dir.glob("*.json"), reverse=True):
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            continue
        payload["queue_path"] = str(path)
        results.append(payload)
    return results
