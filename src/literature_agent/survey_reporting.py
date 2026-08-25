from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig
from .library_store import get_library_paper_detail, library_stats, search_library


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or ""), flags=re.U).strip("_")
    return cleaned[:80] or "report"


def _normalize_language(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"en", "english", "en-us", "en-gb"}:
        return "en"
    return "zh"


def _listify_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else []


def _summary_json(summary: dict[str, Any]) -> dict[str, Any]:
    payload = summary.get("summary_json") if isinstance(summary, dict) else {}
    if isinstance(payload, dict):
        return payload
    return {}


def _record_from_detail(item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary") if isinstance(item, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    summary_json = _summary_json(summary)
    return {
        "paper_id": str(item.get("paper_id") or ""),
        "title": str(item.get("title") or ""),
        "year": str(item.get("year") or ""),
        "venue": str(item.get("venue") or item.get("source_name") or ""),
        "score": float(item.get("score") or 0.0),
        "priority": str(item.get("priority") or ""),
        "tags": _listify_strings(item.get("tags")),
        "page_url": str(item.get("page_url") or ""),
        "pdf_url": str(item.get("pdf_url") or ""),
        "summary": str(summary.get("summary") or summary.get("summary_text") or item.get("abstract") or ""),
        "why_it_matters": str(summary.get("why_it_matters") or ""),
        "schedule_suggestion": str(summary.get("schedule_suggestion") or ""),
        "methods": _listify_strings(summary_json.get("methods") or summary.get("methods")),
        "datasets": _listify_strings(summary_json.get("datasets") or summary.get("datasets")),
        "limitations": _listify_strings(summary_json.get("limitations") or summary.get("limitations")),
        "next_actions": _listify_strings(summary_json.get("next_actions") or summary.get("next_actions")),
        "problem": str(summary_json.get("problem") or ""),
        "novelty": str(summary_json.get("novelty") or ""),
        "hypothesis": str(summary_json.get("hypothesis") or ""),
        "related_work": str(summary_json.get("related_work") or ""),
        "key_solution": str(summary_json.get("key_solution") or ""),
        "experimental_design": str(summary_json.get("experimental_design") or ""),
        "datasets_code": str(summary_json.get("datasets_code") or ""),
        "evidence_support": str(summary_json.get("evidence_support") or ""),
        "contributions": _listify_strings(summary_json.get("contributions")),
        "future_work": _listify_strings(summary_json.get("future_work")),
        "authors": _listify_strings(item.get("authors")),
        "last_seen_at": str(item.get("last_seen_at") or ""),
    }


def build_survey_records_from_library_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_record_from_detail(item) for item in items if isinstance(item, dict)]


def build_survey_records_from_attention(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        paper = item.get("paper") or {}
        summary = item.get("summary") or {}
        methods = _listify_strings(summary.get("methods"))
        datasets = _listify_strings(summary.get("datasets"))
        limitations = _listify_strings(summary.get("limitations"))
        next_actions = _listify_strings(summary.get("next_actions"))
        contributions = _listify_strings(summary.get("contributions"))
        future_work = _listify_strings(summary.get("future_work"))
        records.append(
            {
                "paper_id": str(paper.get("paper_id") or ""),
                "title": str(paper.get("title") or ""),
                "year": str(paper.get("year") or ""),
                "venue": str(paper.get("venue") or paper.get("source_name") or ""),
                "score": float((item.get("relevance") or {}).get("score") or 0.0),
                "priority": str(item.get("priority") or ""),
                "tags": _listify_strings((item.get("relevance") or {}).get("tags")),
                "page_url": str(paper.get("page_url") or ""),
                "pdf_url": str(paper.get("pdf_url") or ""),
                "summary": str(summary.get("summary") or paper.get("abstract") or ""),
                "why_it_matters": str(summary.get("why_it_matters") or ""),
                "schedule_suggestion": str(summary.get("schedule_suggestion") or ""),
                "methods": methods,
                "datasets": datasets,
                "limitations": limitations,
                "next_actions": next_actions,
                "problem": str(summary.get("problem") or ""),
                "novelty": str(summary.get("novelty") or ""),
                "hypothesis": str(summary.get("hypothesis") or ""),
                "related_work": str(summary.get("related_work") or ""),
                "key_solution": str(summary.get("key_solution") or ""),
                "experimental_design": str(summary.get("experimental_design") or ""),
                "datasets_code": str(summary.get("datasets_code") or ""),
                "evidence_support": str(summary.get("evidence_support") or ""),
                "contributions": contributions,
                "future_work": future_work,
                "authors": _listify_strings(paper.get("authors")),
                "last_seen_at": str(paper.get("last_seen_at") or ""),
            }
        )
    return records


def build_survey_records_from_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "paper_id": str(item.get("key") or item.get("paper_id") or ""),
                "title": str(item.get("title") or ""),
                "year": str(item.get("year") or ""),
                "venue": str(item.get("venue") or item.get("source_name") or ""),
                "score": float(item.get("score") or 0.0),
                "priority": "",
                "tags": [],
                "page_url": str(item.get("url") or ""),
                "pdf_url": "",
                "summary": str(item.get("summary") or item.get("abstract") or ""),
                "why_it_matters": str(item.get("why_it_matters") or ""),
                "schedule_suggestion": "",
                "methods": _listify_strings(item.get("methods")),
                "datasets": _listify_strings(item.get("datasets")),
                "limitations": _listify_strings(item.get("limitations")),
                "next_actions": _listify_strings(item.get("next_actions")),
                "problem": str(item.get("problem") or ""),
                "novelty": str(item.get("novelty") or ""),
                "hypothesis": str(item.get("hypothesis") or ""),
                "related_work": str(item.get("related_work") or ""),
                "key_solution": str(item.get("key_solution") or ""),
                "experimental_design": str(item.get("experimental_design") or ""),
                "datasets_code": str(item.get("datasets_code") or ""),
                "evidence_support": str(item.get("evidence_support") or ""),
                "contributions": _listify_strings(item.get("contributions")),
                "future_work": _listify_strings(item.get("future_work")),
                "authors": _listify_strings(item.get("authors")),
                "last_seen_at": "",
            }
        )
    return records


def _counter(records: list[dict[str, Any]], field: str, limit: int = 8) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for record in records:
        for item in _listify_strings(record.get(field)):
            cleaned = item.strip()
            if cleaned:
                counts[cleaned] += 1
    return [{"name": name, "count": count} for name, count in counts.most_common(limit)]


def _top_records(records: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(
        records,
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("year") or ""),
            str(item.get("title") or "").lower(),
        ),
    )
    return ordered[:limit]


def _recent_years(records: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for record in records:
        year = str(record.get("year") or "").strip()
        if year:
            counts[year] += 1
    return [{"year": year, "count": count} for year, count in counts.most_common(limit)]


def _report_title(payload: dict[str, Any], language: str) -> str:
    explicit = str(payload.get("title") or "").strip()
    if explicit:
        return explicit
    kind = str(payload.get("report_kind") or "survey").strip()
    query = str(payload.get("query") or "").strip()
    if language == "en":
        base = "Literature Survey Report" if kind == "survey" else "Literature Digest"
        return f"{base}: {query}" if query else base
    base = "文献调研报告" if kind == "survey" else "文献汇总简报"
    return f"{base}：{query}" if query else base


def _report_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(record.get("score") or 0.0) for record in records]
    return {
        "paper_count": len(records),
        "avg_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "top_methods": _counter(records, "methods"),
        "top_datasets": _counter(records, "datasets"),
        "top_limitations": _counter(records, "limitations"),
        "top_tags": _counter(records, "tags"),
        "top_venues": _counter(records, "venue"),
        "year_distribution": _recent_years(records),
    }


def _zh_line_from_counter(items: list[dict[str, Any]]) -> str:
    if not items:
        return "暂无明显集中趋势。"
    return "；".join(f"{item['name']} ({item['count']})" for item in items)


def _en_line_from_counter(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No clear concentration pattern was observed."
    return "; ".join(f"{item['name']} ({item['count']})" for item in items)


def _render_top_cards(records: list[dict[str, Any]], language: str, limit: int = 3) -> str:
    blocks: list[str] = []
    for index, record in enumerate(_top_records(records, limit=limit), start=1):
        if language == "en":
            block = "\n".join(
                [
                    f"### {index}. {record.get('title', '')}",
                    f"- Venue/Year: {record.get('venue', '')} / {record.get('year', '')}",
                    f"- Score: {record.get('score', 0.0):.2f}",
                    f"- Why it matters: {record.get('why_it_matters', '') or record.get('summary', '')}",
                    f"- Methods: {', '.join(record.get('methods') or []) or 'N/A'}",
                    f"- Datasets: {', '.join(record.get('datasets') or []) or 'N/A'}",
                    f"- Limitations: {', '.join(record.get('limitations') or []) or 'N/A'}",
                ]
            )
        else:
            block = "\n".join(
                [
                    f"### {index}. {record.get('title', '')}",
                    f"- 期刊/会议与年份：{record.get('venue', '')} / {record.get('year', '')}",
                    f"- 相关性分数：{record.get('score', 0.0):.2f}",
                    f"- 价值：{record.get('why_it_matters', '') or record.get('summary', '')}",
                    f"- 方法：{', '.join(record.get('methods') or []) or '暂无'}",
                    f"- 数据集/场景：{', '.join(record.get('datasets') or []) or '暂无'}",
                    f"- 局限：{', '.join(record.get('limitations') or []) or '暂无'}",
                ]
            )
        blocks.append(block)
    return "\n\n".join(blocks)


def _render_research_cards(records: list[dict[str, Any]], language: str, limit: int = 3) -> str:
    cards: list[str] = []
    for index, record in enumerate(_top_records(records, limit=limit), start=1):
        if language == "en":
            card = "\n".join(
                [
                    f"### Paper Card {index}: {record.get('title', '')}",
                    f"1. Problem: {record.get('problem') or record.get('summary') or 'N/A'}",
                    f"2. Novelty / prior work relation: {record.get('novelty') or record.get('related_work') or 'N/A'}",
                    f"3. Hypothesis: {record.get('hypothesis') or 'N/A'}",
                    f"4. Key related work: {record.get('related_work') or 'N/A'}",
                    f"5. Core solution: {record.get('key_solution') or ', '.join(record.get('methods') or []) or 'N/A'}",
                    f"6. Experimental design: {record.get('experimental_design') or 'N/A'}",
                    f"7. Datasets / code: {record.get('datasets_code') or ', '.join(record.get('datasets') or []) or 'N/A'}",
                    f"8. Evidence strength: {record.get('evidence_support') or record.get('why_it_matters') or 'N/A'}",
                    f"9. Contributions: {', '.join(record.get('contributions') or []) or record.get('summary') or 'N/A'}",
                    f"10. Next step: {', '.join(record.get('future_work') or record.get('next_actions') or []) or 'N/A'}",
                ]
            )
        else:
            card = "\n".join(
                [
                    f"### 单篇调研卡 {index}：{record.get('title', '')}",
                    f"1. 这篇工作解决什么问题：{record.get('problem') or record.get('summary') or '暂无'}",
                    f"2. 新意与前人关系：{record.get('novelty') or record.get('related_work') or '暂无'}",
                    f"3. 科学假设/核心判断：{record.get('hypothesis') or '暂无'}",
                    f"4. 相关工作与位置：{record.get('related_work') or '暂无'}",
                    f"5. 核心方案：{record.get('key_solution') or ', '.join(record.get('methods') or []) or '暂无'}",
                    f"6. 实验设计：{record.get('experimental_design') or '暂无'}",
                    f"7. 数据集与代码：{record.get('datasets_code') or ', '.join(record.get('datasets') or []) or '暂无'}",
                    f"8. 证据是否充分：{record.get('evidence_support') or record.get('why_it_matters') or '暂无'}",
                    f"9. 主要贡献：{', '.join(record.get('contributions') or []) or record.get('summary') or '暂无'}",
                    f"10. 下一步怎么跟进：{', '.join(record.get('future_work') or record.get('next_actions') or []) or '暂无'}",
                ]
            )
        cards.append(card)
    return "\n\n".join(cards)


def build_survey_report(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    language = _normalize_language(payload.get("language"))
    title = _report_title(payload, language)
    stats = _report_stats(records)
    generated_at = _iso_now()
    query = str(payload.get("query") or "").strip()
    report_kind = str(payload.get("report_kind") or "survey").strip()
    period_days = int(payload.get("days") or 7)
    top_cards = _render_top_cards(records, language, limit=int(payload.get("top_n") or 5))
    research_cards = _render_research_cards(records, language, limit=int(payload.get("card_limit") or 3))
    if language == "en":
        markdown = "\n".join(
            [
                f"# {title}",
                "",
                f"- Generated at: {generated_at}",
                f"- Query: {query or 'N/A'}",
                f"- Report kind: {report_kind}",
                f"- Window days: {period_days}",
                f"- Paper count: {stats['paper_count']}",
                f"- Average score: {stats['avg_score']}",
                "",
                "## Executive Digest",
                f"- Method landscape: {_en_line_from_counter(stats['top_methods'])}",
                f"- Dataset landscape: {_en_line_from_counter(stats['top_datasets'])}",
                f"- Recurrent limitations: {_en_line_from_counter(stats['top_limitations'])}",
                f"- Active venues: {_en_line_from_counter(stats['top_venues'])}",
                "",
                "## Top Watchlist",
                top_cards or "No top papers available.",
                "",
                "## Suggested Follow-up",
                _en_line_from_counter(_counter(records, "next_actions", limit=10)),
                "",
                "## Deep Research Cards",
                research_cards or "No detailed paper cards available.",
                "",
            ]
        )
    else:
        markdown = "\n".join(
            [
                f"# {title}",
                "",
                f"- 生成时间：{generated_at}",
                f"- 检索主题：{query or '未指定'}",
                f"- 报告类型：{report_kind}",
                f"- 统计窗口：近 {period_days} 天",
                f"- 覆盖论文数：{stats['paper_count']}",
                f"- 平均分：{stats['avg_score']}",
                "",
                "## 管理摘要",
                f"- 方法热点：{_zh_line_from_counter(stats['top_methods'])}",
                f"- 数据集/应用场景：{_zh_line_from_counter(stats['top_datasets'])}",
                f"- 反复出现的局限：{_zh_line_from_counter(stats['top_limitations'])}",
                f"- 活跃期刊/会议：{_zh_line_from_counter(stats['top_venues'])}",
                "",
                "## 本期重点跟踪论文",
                top_cards or "暂无重点论文。",
                "",
                "## 建议动作",
                _zh_line_from_counter(_counter(records, "next_actions", limit=10)),
                "",
                "## 单篇深度调研卡",
                research_cards or "暂无可展开的单篇调研卡。",
                "",
            ]
        )
    return {
        "title": title,
        "query": query,
        "language": language,
        "report_kind": report_kind,
        "days": period_days,
        "generated_at": generated_at,
        "stats": stats,
        "records": records,
        "markdown": markdown.strip() + "\n",
    }


def write_survey_report(config: AppConfig, report: dict[str, Any], prefix: str = "survey") -> dict[str, Any]:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{stamp}-{prefix}-{_slug(str(report.get('query') or report.get('title') or prefix))}"
    markdown_path = config.reports_dir / f"{stem}.md"
    json_path = config.reports_dir / f"{stem}.json"
    markdown_path.write_text(str(report.get("markdown") or ""), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md = config.reports_dir / f"latest-{prefix}.md"
    latest_json = config.reports_dir / f"latest-{prefix}.json"
    latest_md.write_text(str(report.get("markdown") or ""), encoding="utf-8")
    latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "report_id": stem,
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "latest_markdown_path": str(latest_md),
        "latest_json_path": str(latest_json),
        "title": str(report.get("title") or ""),
        "generated_at": str(report.get("generated_at") or ""),
        "stats": report.get("stats") or {},
    }


def generate_attention_survey_report(
    config: AppConfig,
    payload: dict[str, Any],
    attention_result: dict[str, Any],
) -> dict[str, Any]:
    summarized = attention_result.get("summaries") or []
    ranked = attention_result.get("ranked") or []
    records = build_survey_records_from_attention(summarized or ranked)
    report = build_survey_report(
        records,
        {
            "query": attention_result.get("query") or payload.get("query") or "",
            "language": payload.get("report_language") or payload.get("language") or "zh",
            "report_kind": payload.get("report_kind") or "attention_digest",
            "days": payload.get("report_days") or 7,
            "top_n": payload.get("report_top_n") or 5,
            "card_limit": payload.get("report_card_limit") or 3,
            "title": payload.get("report_title") or "",
        },
    )
    paths = write_survey_report(config, report, prefix="attention")
    return {
        **paths,
        "preview": str(report.get("markdown") or "")[:2400],
        "records_preview": records[:5],
    }


def generate_library_survey_report(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    limit = int(payload.get("limit") or 16)
    items = search_library(
        config, query, limit=limit,
        api_key=str(payload.get("api_key") or ""),
        model_provider=str(payload.get("model_provider") or "ds"),
        model=str(payload.get("model") or ""),
    )
    details: list[dict[str, Any]] = []
    for item in items:
        paper_id = str(item.get("paper_id") or "")
        detail = get_library_paper_detail(config, paper_id) if paper_id else None
        details.append(detail or item)
    records = build_survey_records_from_library_items(details)
    report = build_survey_report(records, payload)
    paths = write_survey_report(config, report, prefix="library")
    graph = library_stats(config)
    return {
        **paths,
        "query": query,
        "library_stats": graph,
        "preview": str(report.get("markdown") or "")[:2400],
        "records_preview": records[:5],
    }


def list_survey_reports(config: AppConfig, limit: int = 20) -> list[dict[str, Any]]:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for path in sorted(config.reports_dir.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        items.append(
            {
                "report_id": path.stem,
                "path": str(path),
                "name": path.name,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                "size": path.stat().st_size,
            }
        )
    return items


def read_survey_report(config: AppConfig, report_id: str) -> dict[str, Any] | None:
    ref = str(report_id or "").strip()
    if not ref:
        return None
    candidate = Path(ref)
    if candidate.is_absolute():
        path = candidate
    else:
        path = config.reports_dir / (ref if ref.endswith(".md") else f"{ref}.md")
    if not path.exists():
        return None
    json_path = path.with_suffix(".json")
    payload: dict[str, Any] = {}
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    return {
        "report_id": path.stem,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
        "metadata": payload,
    }
