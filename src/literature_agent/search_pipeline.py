from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from .config import AppConfig
from .library_store import (
    paper_id_for,
    record_discovered_papers,
    record_ranked_items,
    record_run_finish,
    record_run_start,
)
from .publisher_rules import canonical_pdf_url, detect_publisher
from .relevance import score_paper_relevance
from .search_sources import search_literature
from .storage import (
    build_download_plan,
    download_pdf_to_record,
    persist_library_paper,
    persist_search_run,
    queue_download_batch,
)


_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _translate_query(config: AppConfig, query: str) -> str:
    from .chat import chat_with_kimi

    prompt = (
        "Translate the following Chinese research topic into concise English search keywords "
        "suitable for querying academic databases like OpenAlex, arXiv, and Semantic Scholar. "
        "Output ONLY the translated keywords on a single line, nothing else — "
        "no quotes, no explanations, no greetings.\n\n"
        f"Chinese: {query}\nEnglish:"
    )
    try:
        result = chat_with_kimi(config, prompt)
        translated = result.content.strip()
        # Strip common LLM chatter prefixes
        for prefix in ["English:", "english:", "Keywords:", "keywords:", "Sure", "Here", "The translation"]:
            if translated.startswith(prefix):
                translated = translated[len(prefix):].strip()
        # Strip surrounding quotes
        translated = translated.strip("'\"`")
        if translated and len(translated) > 3:
            return translated
    except Exception:
        pass
    return query


def _threshold(config: AppConfig) -> float:
    raw = config.raw.get("search", {}).get("thresholds", {})
    return float(raw.get("download_min_score", 3.0))


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
    searched_at = datetime.now(timezone.utc).isoformat()
    run_id = f"search-{searched_at.replace(':', '').replace('+', 'Z')}"
    search_query = query
    if _has_cjk(query):
        translated = _translate_query(config, query)
        if translated and translated != query:
            search_query = translated
    record_run_start(
        config,
        run_id,
        "search",
        query,
        {
            "query": query,
            "search_query": search_query,
            "max_results": max_results,
            "auto_download": auto_download,
            "min_score": min_score,
        },
    )
    sources = config.raw.get("search", {}).get("sources", []) or None
    papers = fetcher(search_query, max_results, sources)
    record_discovered_papers(config, run_id, papers)
    ranked_items: list[dict[str, Any]] = []
    effective_min_score = float(min_score if min_score is not None else _threshold(config))

    for paper in papers:
        relevance = score_paper_relevance(config, paper, search_query)
        ranked_items.append(
            {
                "paper": paper,
                "relevance": relevance,
                "downloadable": bool(paper.get("pdf_url")),
            }
        )

    ranked_items.sort(key=_sort_key, reverse=True)
    selected_paper_ids = {
        paper_id_for(item["paper"])
        for item in ranked_items
        if float(item["relevance"]["score"]) >= effective_min_score
    }
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
            download_plan = build_download_plan(config, paper)
            queue_items.append(
                {
                    "paper": paper,
                    "score": item["relevance"]["score"],
                    "tags": item["relevance"]["tags"],
                    "publisher": detect_publisher(paper.get("page_url", "")),
                    "download_plan": download_plan,
                }
            )

    search_run = {
        "query": query,
        "max_results": max_results,
        "auto_download": auto_download,
        "min_score": effective_min_score,
        "searched_at": searched_at,
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

    record_ranked_items(config, run_id, ranked_items, selected_paper_ids)
    artifacts = {
        "search_run_path": str(search_run_path),
        "queue_path": str(queue_path) if queue_path else "",
        "library_db_path": str(config.library_db_path),
    }
    record_run_finish(
        config,
        run_id,
        "completed",
        {
            "result_count": len(ranked_items),
            "downloaded_count": len(downloaded),
            "queued_count": len(queue_items),
            "min_score": effective_min_score,
        },
        artifacts,
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
        "library_db_path": str(config.library_db_path),
        "queued_count": len(queue_items),
    }
