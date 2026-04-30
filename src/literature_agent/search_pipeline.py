from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .config import AppConfig
from .publisher_rules import canonical_pdf_url, detect_publisher
from .relevance import score_paper_relevance
from .search_sources import search_literature
from .storage import (
    download_pdf_to_record,
    persist_library_paper,
    persist_search_run,
    queue_download_batch,
)


def _threshold(config: AppConfig) -> float:
    raw = config.raw.get("search", {}).get("thresholds", {})
    return float(raw.get("download_min_score", 8.0))


def _sort_key(item: dict[str, Any]) -> tuple[float, str]:
    score = float(item.get("relevance", {}).get("score", 0.0))
    year = str(item.get("paper", {}).get("year", ""))
    return (score, year)


def run_search_pipeline(
    config: AppConfig,
    query: str,
    max_results: int,
    auto_download: bool = False,
    min_score: float | None = None,
    fetcher: Callable[[str, int, list[str] | None], list[dict[str, Any]]] = search_literature,
) -> dict[str, Any]:
    sources = config.raw.get("search", {}).get("sources", []) or None
    papers = fetcher(query, max_results, sources)
    ranked_items: list[dict[str, Any]] = []
    effective_min_score = float(min_score if min_score is not None else _threshold(config))

    for paper in papers:
        relevance = score_paper_relevance(config, paper, query)
        ranked_items.append(
            {
                "paper": paper,
                "relevance": relevance,
                "downloadable": bool(paper.get("pdf_url")),
            }
        )

    ranked_items.sort(key=_sort_key, reverse=True)
    downloaded: list[dict[str, Any]] = []
    queue_items: list[dict[str, Any]] = []

    if auto_download:
        for item in ranked_items:
            if float(item["relevance"]["score"]) < effective_min_score:
                continue
            item["paper"]["pdf_url"] = canonical_pdf_url(item["paper"])
            persisted = persist_library_paper(
                config,
                item["paper"],
                source={"trigger": "batch-search", "query": query},
            )
            download_status = {
                "status": "skipped",
                "reason": "No PDF URL available.",
            }
            if item["paper"].get("pdf_url"):
                download_status = download_pdf_to_record(
                    item["paper"]["pdf_url"],
                    persisted["paths"]["pdf"],
                )
            downloaded.append(
                {
                    "paper_title": item["paper"].get("title", ""),
                    "score": item["relevance"]["score"],
                    "download": download_status,
                    "paths": persisted["paths"],
                    "classification": persisted["classification"],
                }
            )
    else:
        for item in ranked_items:
            if float(item["relevance"]["score"]) < effective_min_score:
                continue
            paper = dict(item["paper"])
            paper["pdf_url"] = canonical_pdf_url(paper)
            queue_items.append(
                {
                    "paper": paper,
                    "score": item["relevance"]["score"],
                    "tags": item["relevance"]["tags"],
                    "publisher": detect_publisher(paper.get("page_url", "")),
                }
            )

    search_run = {
        "query": query,
        "max_results": max_results,
        "auto_download": auto_download,
        "min_score": effective_min_score,
        "searched_at": datetime.now(timezone.utc).isoformat(),
        "results": ranked_items,
        "downloaded": downloaded,
    }
    search_run_path = persist_search_run(config, search_run)
    queue_path = None
    if queue_items:
        queue_path = queue_download_batch(
            config,
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "min_score": effective_min_score,
                "items": queue_items,
            },
        )

    return {
        "query": query,
        "result_count": len(ranked_items),
        "downloaded_count": len(downloaded),
        "min_score": effective_min_score,
        "results": ranked_items,
        "downloaded": downloaded,
        "search_run_path": str(search_run_path),
        "queue_path": str(queue_path) if queue_path else "",
        "queued_count": len(queue_items),
    }
