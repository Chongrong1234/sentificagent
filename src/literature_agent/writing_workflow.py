from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .citation_gate import (
    apply_citations,
    check_cross_chapter_citations,
    detect_citation_need,
    extract_bibtex_for_decisions,
    summarize_pending_citations,
)
from .library_store import search_library
from .template_guardrails import build_guardrails_prompt, load_guardrails, resolve_section_id
from .template_profile import build_template_profile
from .writing_audit import run_full_audit
from .writing_workspace import (
    _load_evidence_memory,
    _load_section_memories,
    _load_sections_manifest,
    _memory_dir,
    compile_project,
    load_project,
    load_project_context,
    load_project_sources,
    load_workspace_index,
    merge_project_bibliography,
    read_project_file,
    record_project_turn,
    save_project_file,
    update_section_memory,
)


WORKFLOW_STATE_FILE = "workflow_state.json"


class WorkflowStage(str, enum.Enum):
    EXPLORATION = "exploration"
    OUTLINE_NEGOTIATION = "outline"
    ORDER_SELECTION = "ordering"
    CHAPTER_WRITING = "writing"
    FINAL_REVIEW = "review"
    COMPLETE = "complete"


class ChapterState(str, enum.Enum):
    PENDING = "pending"
    NEGOTIATING = "negotiating"
    WRITING = "writing"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    NEEDS_REVIEW = "needs_review"


@dataclass
class SectionBrief:
    section_id: str
    title: str
    path: str
    sort_order: int
    suggested_order: int
    negotiation: str
    citation_required: bool
    requires_figures: bool
    min_paragraphs: int
    writing_guide: str
    required_elements: list[str]
    options: list[dict[str, str]]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(project_id: str) -> Path:
    return _memory_dir(project_id) / WORKFLOW_STATE_FILE


def _load_state(project_id: str) -> dict[str, Any]:
    path = _state_path(project_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
    path = _state_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def _slugify(value: str) -> str:
    cleaned = []
    last_sep = False
    for char in str(value or "").strip().lower():
        if char.isalnum():
            cleaned.append(char)
            last_sep = False
            continue
        if "\u4e00" <= char <= "\u9fff":
            cleaned.append(char)
            last_sep = False
            continue
        if not last_sep:
            cleaned.append("-")
            last_sep = True
    slug = "".join(cleaned).strip("-")
    return slug or "section"


def _section_options(title: str) -> list[dict[str, str]]:
    normalized = str(title or "")
    lowered = normalized.lower()
    if "现状" in normalized or "文献" in normalized or "related work" in lowered:
        return [
            {"id": "by_tech_stream", "label": "技术流派型", "description": "按不同技术路线分节对比。"},
            {"id": "by_timeline", "label": "时间演进型", "description": "按发展历程组织文献脉络。"},
            {"id": "by_problem", "label": "问题驱动型", "description": "按核心挑战拆分综述结构。"},
        ]
    if "方案" in normalized or "method" in lowered or "研究内容" in normalized:
        return [
            {"id": "problem_method_eval", "label": "问题-方法-验证", "description": "先交代问题，再写方法与验证方式。"},
            {"id": "pipeline", "label": "流程展开型", "description": "按技术流程逐步解释核心模块。"},
            {"id": "task_split", "label": "任务拆解型", "description": "按子任务或研究子目标分段。"},
        ]
    if "意义" in normalized or "背景" in normalized or "introduction" in lowered:
        return [
            {"id": "problem_gap_value", "label": "背景-缺口-价值", "description": "从背景、空白到研究价值递进。"},
            {"id": "application_first", "label": "应用场景型", "description": "先写工程场景，再落到研究问题。"},
        ]
    if "困难" in normalized or "风险" in normalized or "discussion" in lowered:
        return [
            {"id": "risk_countermeasure", "label": "风险-措施型", "description": "逐条列风险并给出应对。"},
            {"id": "by_dimension", "label": "维度展开型", "description": "按数据、模型、工程、进度几个维度组织。"},
        ]
    if "进度" in normalized or "计划" in normalized or "schedule" in lowered:
        return [
            {"id": "timeline", "label": "时间线型", "description": "按时间阶段列里程碑和交付物。"},
            {"id": "work_package", "label": "任务包型", "description": "按工作包列任务、产出与节点。"},
        ]
    return [
        {"id": "direct", "label": "直接展开", "description": "按模板标题直接展开内容。"},
        {"id": "structured", "label": "结构化展开", "description": "先列子点，再分别写段落。"},
    ]


def _guess_negotiation(title: str, path: str) -> str:
    text = f"{title} {path}".lower()
    if "参考文献" in title or "references" in text:
        return "skip"
    if any(token in text for token in ["进度", "schedule", "经费", "funding", "条件", "budget"]):
        return "light"
    return "full"


def _guess_citation_required(title: str, path: str) -> bool:
    text = f"{title} {path}".lower()
    return any(token in text for token in ["文献", "现状", "related work", "reference", "综述", "literature"])


def _guess_requires_figures(title: str, path: str) -> bool:
    text = f"{title} {path}".lower()
    return any(token in text for token in ["方案", "method", "experiment", "实验", "结果", "技术路线"])


def _guess_required_elements(title: str) -> list[str]:
    normalized = str(title or "")
    lowered = normalized.lower()
    if "意义" in normalized or "背景" in normalized or "introduction" in lowered:
        return ["问题背景", "现有不足", "研究目标", "理论或工程价值"]
    if "现状" in normalized or "related work" in lowered or "文献" in normalized:
        return ["研究方向划分", "代表性工作", "方法比较", "研究空白"]
    if "研究内容" in normalized:
        return ["任务拆解", "关键方法", "预期产出"]
    if "方案" in normalized or "method" in lowered:
        return ["技术路线", "数据来源", "实验设计", "评价方式"]
    if "进度" in normalized or "schedule" in lowered:
        return ["阶段划分", "里程碑", "交付物"]
    if "条件" in normalized or "funding" in lowered or "经费" in normalized:
        return ["已有条件", "缺口条件", "经费或资源安排"]
    if "困难" in normalized or "风险" in normalized or "discussion" in lowered:
        return ["潜在问题", "影响分析", "解决措施"]
    return []


def _guess_writing_guide(title: str) -> str:
    normalized = str(title or "")
    lowered = normalized.lower()
    if "意义" in normalized or "背景" in normalized or "introduction" in lowered:
        return "说明研究背景、任务来源、当前缺口以及研究目的与价值。"
    if "现状" in normalized or "related work" in lowered or "文献" in normalized:
        return "围绕几个主要研究流派整理代表性文献，并归纳研究空白。"
    if "研究内容" in normalized:
        return "把研究任务拆成若干子目标，每项说明做什么、怎么做、产出什么。"
    if "方案" in normalized or "method" in lowered:
        return "解释技术路线、系统流程、实验设计和验证指标。"
    if "进度" in normalized or "schedule" in lowered:
        return "按时间阶段列出里程碑、任务和交付物。"
    if "条件" in normalized or "funding" in lowered or "经费" in normalized:
        return "说明现有基础、需要的资源以及经费或条件保障。"
    if "困难" in normalized or "风险" in normalized or "discussion" in lowered:
        return "逐条列出潜在风险，并给出可执行的缓解措施。"
    if "参考文献" in normalized or "references" in lowered:
        return "该部分由系统维护，不需要协商写作。"
    return "按模板标题职责写清楚本节要解决的问题、方法与结论。"


def _path_title(rel_path: str) -> str:
    name = Path(str(rel_path or "")).stem
    return name.replace("_", " ").replace("-", " ").strip() or "Untitled"


def _sections_from_manifest(project_id: str) -> list[SectionBrief]:
    manifest = _load_sections_manifest(project_id)
    items = manifest.get("sections", []) if isinstance(manifest, dict) else []
    sections: list[SectionBrief] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or _path_title(str(item.get("path") or ""))
        path = str(item.get("path") or "").strip()
        section_id = str(item.get("slug") or item.get("id") or "").strip() or _slugify(title)
        sections.append(
            SectionBrief(
                section_id=section_id,
                title=title,
                path=path,
                sort_order=index + 1,
                suggested_order=index + 1,
                negotiation=_guess_negotiation(title, path),
                citation_required=_guess_citation_required(title, path),
                requires_figures=_guess_requires_figures(title, path),
                min_paragraphs=1 if _guess_negotiation(title, path) == "light" else 3,
                writing_guide=_guess_writing_guide(title),
                required_elements=_guess_required_elements(title),
                options=_section_options(title),
            )
        )
    return sections


def _sections_from_template_profile(project_id: str, project: dict[str, Any]) -> list[SectionBrief]:
    profile = project.get("template_profile") or build_template_profile(
        project_id,
        template_id=str(project.get("template_id") or ""),
        project_dir=Path(project.get("paths", {}).get("dir") or ""),
    )
    hierarchy = (profile or {}).get("section_hierarchy") or {}
    titles = hierarchy.get("titles") or {}
    top_level = str(hierarchy.get("top_level") or "section")
    raw_titles = titles.get(top_level) or []
    if top_level == "chapter":
        raw_titles = hierarchy.get("mainmatter_chapter_titles") or raw_titles

    # Also check input'd files for section titles not found in aggregate content
    if not raw_titles:
        files_dir = Path(project.get("paths", {}).get("files_dir") or "")
        if files_dir.exists():
            input_files = (profile or {}).get("input_structure") or []
            for name in input_files:
                for candidate in [name, name + ".tex", f"{name}.tex"]:
                    candidate_path = files_dir / candidate
                    if candidate_path.exists():
                        try:
                            body = candidate_path.read_text(encoding="utf-8", errors="ignore")
                            found = re.findall(rf"\\{top_level}\*?\{{([^}}]*)\}}", body)
                            for t in found:
                                t = str(t).strip()
                                if t and t not in raw_titles:
                                    raw_titles.append(t)
                        except OSError:
                            pass
                        break

    # Collect frontmatter pseudo-sections (Abstract, etc.)
    fm_titles = hierarchy.get("frontmatter_titles") or []

    sections: list[SectionBrief] = []

    def _add(title_text: str, sort_index: int) -> None:
        path = f"sections/{_slugify(title_text)}.tex"
        sections.append(
            SectionBrief(
                section_id=_slugify(title_text),
                title=title_text,
                path=path,
                sort_order=sort_index,
                suggested_order=sort_index,
                negotiation=_guess_negotiation(title_text, path),
                citation_required=_guess_citation_required(title_text, path),
                requires_figures=_guess_requires_figures(title_text, path),
                min_paragraphs=1 if _guess_negotiation(title_text, path) == "light" else 3,
                writing_guide=_guess_writing_guide(title_text),
                required_elements=_guess_required_elements(title_text),
                options=_section_options(title_text),
            )
        )

    for index, title in enumerate(fm_titles):
        _add(str(title or "").strip(), index + 1)

    offset = len(sections)
    for index, title in enumerate(raw_titles):
        title_text = str(title or "").strip()
        if not title_text:
            continue
        _add(title_text, offset + index + 1)

    # If still no sections, use input filenames as titles (cleaned)
    if not sections:
        input_files = (profile or {}).get("input_structure") or []
        tex_files = [f for f in (project.get("files") or []) if str(f).lower().endswith(".tex")]
        all_files = input_files or tex_files
        for index, rel_path in enumerate(all_files):
            name = Path(str(rel_path)).stem
            clean = name.replace("_", " ").replace("-", " ").strip()
            if clean.lower() in {"main", "report", "thesis", "paper", "manuscript", "document"}:
                continue
            if clean:
                _add(clean, index + 1)

    return sections


def _sections_from_files(project: dict[str, Any]) -> list[SectionBrief]:
    tex_files = [item for item in (project.get("files") or []) if str(item).lower().endswith(".tex")]
    sections: list[SectionBrief] = []
    for index, rel_path in enumerate(tex_files):
        title = _path_title(rel_path)
        sections.append(
            SectionBrief(
                section_id=_slugify(title or rel_path),
                title=title,
                path=str(rel_path),
                sort_order=index + 1,
                suggested_order=index + 1,
                negotiation=_guess_negotiation(title, rel_path),
                citation_required=_guess_citation_required(title, rel_path),
                requires_figures=_guess_requires_figures(title, rel_path),
                min_paragraphs=1 if _guess_negotiation(title, rel_path) == "light" else 3,
                writing_guide=_guess_writing_guide(title),
                required_elements=_guess_required_elements(title),
                options=_section_options(title),
            )
        )
    return sections


def _default_sections(project_id: str) -> list[SectionBrief]:
    project = load_project(project_id)
    sections = _sections_from_manifest(project_id)
    if sections:
        return sections
    sections = _sections_from_template_profile(project_id, project)
    if sections:
        return sections
    return _sections_from_files(project)


def _topic_type(project: dict[str, Any]) -> str:
    title = f"{project.get('title', '')} {project.get('goal', '')} {project.get('requirements', '')}".lower()
    if any(token in title for token in ["综述", "review", "survey"]):
        return "survey"
    if any(token in title for token in ["开题", "proposal", "基金", "申请", "grant"]):
        return "problem_driven"
    if any(token in title for token in ["实验", "benchmark", "结果", "result"]):
        return "experiment"
    return "method_system"


def _recommended_order(sections: list[SectionBrief], topic_type: str) -> list[str]:
    rank_map: dict[str, int] = {}
    if topic_type == "survey":
        keywords = ["现状", "related work", "文献", "综述", "研究内容", "方案", "背景", "困难", "条件", "进度"]
    elif topic_type == "problem_driven":
        keywords = ["背景", "意义", "现状", "研究内容", "方案", "困难", "条件", "进度"]
    elif topic_type == "experiment":
        keywords = ["方案", "研究内容", "结果", "讨论", "背景", "现状", "困难", "进度"]
    else:
        keywords = ["研究内容", "方案", "现状", "困难", "背景", "条件", "进度"]
    for index, keyword in enumerate(keywords):
        rank_map[keyword] = index + 1

    def score(item: SectionBrief) -> tuple[int, int]:
        text = item.title.lower()
        best = 999
        for keyword, rank in rank_map.items():
            if keyword.lower() in text or keyword in item.title:
                best = min(best, rank)
        return best, item.sort_order

    ordered = sorted(sections, key=score)
    return [item.section_id for item in ordered]


def _section_content(project_id: str, rel_path: str) -> str:
    if not rel_path:
        return ""
    try:
        file_data = read_project_file(project_id, rel_path)
    except FileNotFoundError:
        return ""
    if not bool(file_data.get("is_text")):
        return ""
    return str(file_data.get("content") or "")


def _locked_summary(project_id: str, rel_path: str, title: str) -> str:
    memories = _load_section_memories(project_id)
    for item in reversed(memories):
        if str(item.get("path") or "") == str(rel_path or ""):
            memory = str(item.get("memory") or "").strip()
            if memory:
                return memory
    content = _section_content(project_id, rel_path)
    if not content.strip():
        return ""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    summary = " ".join(lines[:3])
    return summary[:220] if summary else f"{title} 已写入内容。"


def _stage1_card(state: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    exploration = state.get("exploration") or {}
    selected = exploration.get("selected_topic") or project.get("goal") or project.get("title") or "未确定"
    completed = bool(exploration.get("completed"))
    summary = exploration.get("summary") or f"选题：{selected}"
    return {
        "id": "stage1",
        "title": "STAGE 1 勘探与选题",
        "status": "locked" if completed else "negotiating",
        "summary": summary,
        "topic": selected,
        "completed": completed,
        "coverage": exploration.get("coverage", []),
        "suggestions": exploration.get("suggestions", []),
    }


def _chapter_card(
    section: SectionBrief,
    item_state: dict[str, Any],
    *,
    current_section_id: str,
    project_id: str,
) -> dict[str, Any]:
    status = str(item_state.get("status") or ChapterState.PENDING.value)
    summary = str(item_state.get("summary") or "").strip()
    if status == ChapterState.LOCKED.value and not summary:
        summary = _locked_summary(project_id, section.path, section.title)
    return {
        "id": section.section_id,
        "title": section.title,
        "path": section.path,
        "sort_order": section.sort_order,
        "write_order": int(item_state.get("write_order") or section.suggested_order),
        "status": status,
        "negotiation": section.negotiation,
        "summary": summary,
        "focus": str(item_state.get("focus") or ""),
        "strategy_label": str(item_state.get("strategy_label") or ""),
        "locked": status == ChapterState.LOCKED.value,
        "active": current_section_id == section.section_id,
        "needs_review": status == ChapterState.NEEDS_REVIEW.value,
        "citation_required": section.citation_required,
        "requires_figures": section.requires_figures,
        "writing_guide": section.writing_guide,
        "required_elements": section.required_elements,
        "options": section.options,
        "min_paragraphs": section.min_paragraphs,
    }


def _initial_section_state(section: SectionBrief, write_order: int) -> dict[str, Any]:
    return {
        "section_id": section.section_id,
        "title": section.title,
        "path": section.path,
        "status": ChapterState.PENDING.value if section.negotiation != "skip" else ChapterState.LOCKED.value,
        "negotiation": section.negotiation,
        "write_order": write_order,
        "strategy_id": "",
        "strategy_label": "",
        "custom_note": "",
        "focus": "",
        "summary": "",
        "locked_at": "",
        "last_updated": _utc_iso(),
    }


def _base_state(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    sections = _default_sections(project_id)
    topic_type = _topic_type(project)
    write_order = _recommended_order(sections, topic_type)
    order_map = {section_id: index + 1 for index, section_id in enumerate(write_order)}
    items = [_initial_section_state(section, order_map.get(section.section_id, section.sort_order)) for section in sections]
    return {
        "schema_version": 1,
        "project_id": project_id,
        "stage": WorkflowStage.EXPLORATION.value,
        "topic_type": topic_type,
        "exploration": {
            "topic": project.get("goal") or project.get("title") or "",
            "completed": False,
            "summary": "",
            "coverage": [],
            "insights": [],
            "suggestions": [],
            "selected_topic": "",
            "updated_at": _utc_iso(),
        },
        "current_section_id": sections[0].section_id if sections else "",
        "sections": items,
        "updated_at": _utc_iso(),
    }


def _ensure_state(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    stored = _load_state(project_id)
    sections = _default_sections(project_id)
    topic_type = _topic_type(project)
    recommended = _recommended_order(sections, topic_type)
    order_map = {section_id: index + 1 for index, section_id in enumerate(recommended)}
    state = stored if stored else _base_state(project_id)
    existing_items = {str(item.get("section_id") or ""): item for item in (state.get("sections") or []) if isinstance(item, dict)}
    merged_items: list[dict[str, Any]] = []
    for section in sections:
        current = dict(existing_items.get(section.section_id) or {})
        if not current:
            current = _initial_section_state(section, order_map.get(section.section_id, section.sort_order))
        current.setdefault("section_id", section.section_id)
        current.setdefault("title", section.title)
        current.setdefault("path", section.path)
        current.setdefault("negotiation", section.negotiation)
        current.setdefault("status", ChapterState.PENDING.value if section.negotiation != "skip" else ChapterState.LOCKED.value)
        current.setdefault("strategy_id", "")
        current.setdefault("strategy_label", "")
        current.setdefault("custom_note", "")
        current.setdefault("focus", "")
        current.setdefault("summary", "")
        current["title"] = section.title
        current["path"] = section.path
        current["negotiation"] = section.negotiation
        current["write_order"] = int(current.get("write_order") or order_map.get(section.section_id, section.sort_order))
        current["last_updated"] = current.get("last_updated") or _utc_iso()
        merged_items.append(current)
    state["schema_version"] = 1
    state["project_id"] = project_id
    state["topic_type"] = topic_type
    if "exploration" not in state or not isinstance(state.get("exploration"), dict):
        state["exploration"] = _base_state(project_id)["exploration"]
    current_section_id = str(state.get("current_section_id") or "")
    valid_ids = {item["section_id"] for item in merged_items}
    if current_section_id not in valid_ids:
        state["current_section_id"] = merged_items[0]["section_id"] if merged_items else ""
    state["sections"] = merged_items
    state["updated_at"] = _utc_iso()
    return _save_state(project_id, state)


def _section_map(project_id: str) -> dict[str, SectionBrief]:
    return {item.section_id: item for item in _default_sections(project_id)}


def _count_status(items: list[dict[str, Any]], status: str) -> int:
    return sum(1 for item in items if str(item.get("status") or "") == status)


def _pending_citation_count(project_id: str) -> int:
    state = _ensure_state(project_id)
    total = 0
    for item in state.get("sections", []) or []:
        rel_path = str(item.get("path") or "")
        if not rel_path:
            continue
        total += len(detect_citation_need(_section_content(project_id, rel_path), str(item.get("section_id") or "")))
    return total


def _search_library_cards(project_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    try:
        from .config import load_config

        config = load_config()
        items = search_library(config, query, limit=limit) if str(query or "").strip() else []
    except Exception:
        items = []
    cards: list[dict[str, Any]] = []
    used: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        authors = item.get("authors") or []
        year_text = str(item.get("year") or "").strip()
        title_tokens = [token for token in title.replace("-", " ").split() if token]
        base = f"{(str(authors[0]).split()[-1].lower() if authors else 'ref')}{year_text or 'nd'}{(title_tokens[0].lower() if title_tokens else 'paper')[:12]}"
        key = "".join(char for char in base if char.isalnum() or ("\u4e00" <= char <= "\u9fff")) or "ref"
        suffix = 2
        candidate_key = key
        while candidate_key in used:
            candidate_key = f"{key}{suffix}"
            suffix += 1
        used.add(candidate_key)
        bibtex_lines = [f"@article{{{candidate_key},"]
        if authors:
            bibtex_lines.append(f"  author = {{{' and '.join(str(author).strip() for author in authors if str(author).strip())}}},")
        bibtex_lines.append(f"  title = {{{title}}},")
        if item.get("venue"):
            bibtex_lines.append(f"  journal = {{{str(item.get('venue') or '').strip()}}},")
        if year_text:
            bibtex_lines.append(f"  year = {{{year_text}}},")
        bibtex_lines.append("}")
        cards.append(
            {
                "key": candidate_key,
                "citation_key": candidate_key,
                "title": title,
                "year": year_text,
                "venue": str(item.get("venue") or ""),
                "authors": authors,
                "abstract": str(item.get("abstract") or ""),
                "summary": str(item.get("summary") or item.get("abstract") or ""),
                "claim": str(item.get("abstract") or "")[:180],
                "bibtex": "\n".join(bibtex_lines),
            }
        )
    return cards


def _locked_summaries_map(project_id: str, state: dict[str, Any]) -> dict[str, str]:
    briefs = _section_map(project_id)
    result: dict[str, str] = {}
    for item in state.get("sections", []) or []:
        if str(item.get("status") or "") != ChapterState.LOCKED.value:
            continue
        section_id = str(item.get("section_id") or "")
        brief = briefs.get(section_id)
        title = brief.title if brief else str(item.get("title") or section_id)
        result[section_id] = str(item.get("summary") or _locked_summary(project_id, brief.path if brief else "", title))
    return result


def _guardrail_summary(project_id: str, state: dict[str, Any], section_id: str) -> dict[str, Any]:
    try:
        guardrails = load_guardrails(project_id)
    except Exception:
        return {}
    section = next(
        (
            item
            for item in (guardrails.get("sections") or [])
            if isinstance(item, dict) and str(item.get("id") or "") == str(section_id or "")
        ),
        {},
    )
    ordered_ids = [str(item.get("section_id") or "") for item in sorted(state.get("sections", []) or [], key=lambda entry: int(entry.get("write_order") or 999))]
    prompt = build_guardrails_prompt(guardrails, section_id, ordered_ids, _locked_summaries_map(project_id, state))
    citation = guardrails.get("citation") or {}
    return {
        "section_id": section_id,
        "section": section,
        "citation": citation,
        "immutable_zone_count": len(guardrails.get("immutable_zones") or []),
        "prompt": prompt,
        "violations": next(
            (
                item.get("last_guardrail_violations") or []
                for item in (state.get("sections") or [])
                if str(item.get("section_id") or "") == str(section_id or "")
            ),
            [],
        ),
    }


def _current_section_payload(project_id: str, state: dict[str, Any]) -> dict[str, Any]:
    section_id = str(state.get("current_section_id") or "")
    briefs = _section_map(project_id)
    brief = briefs.get(section_id)
    if not brief:
        return {}
    item_state = next((item for item in (state.get("sections") or []) if str(item.get("section_id") or "") == section_id), {})
    content = _section_content(project_id, brief.path)
    context = load_project_context(project_id, brief.path if brief.path else "")
    evidence_cards = (((context.get("evidence_memory") or {}).get("cards") or []) if isinstance(context.get("evidence_memory"), dict) else [])[:8]
    source_files = (context.get("source_files") or [])[:8]
    workspace = context.get("workspace_index") or {}
    approved_keys = [key for key in (item_state.get("approved_citations") or []) if str(key).strip()]
    claim_query = " ".join(
        filter(
            None,
            [
                brief.title,
                str(item_state.get("focus") or ""),
                str(item_state.get("strategy_label") or ""),
                str(load_project(project_id).get("goal") or ""),
            ],
        )
    ).strip()
    search_cards = _search_library_cards(project_id, claim_query, limit=10)
    library_cards = search_cards if search_cards else [item for item in evidence_cards if isinstance(item, dict)]
    pending_citations = summarize_pending_citations(content, brief.section_id, library_cards, min_strength=2)
    locked_summaries = _locked_summaries_map(project_id, state)
    cross_chapter = [
        {
            "section_id": item.section_id,
            "title": item.title,
            "keys": item.keys,
            "message": item.message,
        }
        for item in check_cross_chapter_citations(brief.title, locked_summaries)
    ]
    guardrails = _guardrail_summary(project_id, state, brief.section_id)
    return {
        "section_id": brief.section_id,
        "title": brief.title,
        "path": brief.path,
        "status": str(item_state.get("status") or ""),
        "negotiation": brief.negotiation,
        "writing_guide": brief.writing_guide,
        "required_elements": brief.required_elements,
        "options": brief.options,
        "strategy_id": str(item_state.get("strategy_id") or ""),
        "strategy_label": str(item_state.get("strategy_label") or ""),
        "custom_note": str(item_state.get("custom_note") or ""),
        "focus": str(item_state.get("focus") or ""),
        "summary": str(item_state.get("summary") or ""),
        "citation_required": brief.citation_required,
        "requires_figures": brief.requires_figures,
        "min_paragraphs": brief.min_paragraphs,
        "content": content,
        "approved_citations": approved_keys,
        "pending_citations": pending_citations,
        "cross_chapter_hints": cross_chapter,
        "guardrails": guardrails,
        "evidence_cards": [
            {
                "key": str(item.get("key") or ""),
                "title": str(item.get("title") or ""),
                "claim": str(item.get("claim") or ""),
                "strength": int(item.get("strength") or 0) if str(item.get("strength") or "").isdigit() else item.get("strength"),
                "approved": bool(item.get("approved")),
            }
            for item in evidence_cards
            if isinstance(item, dict)
        ],
        "source_files": [
            {
                "name": str(item.get("name") or ""),
                "excerpt": str(item.get("excerpt") or ""),
                "kind": str(item.get("kind") or ""),
            }
            for item in source_files
            if isinstance(item, dict)
        ],
        "workspace_summary": {
            "workspace_name": str(workspace.get("workspace_name") or ""),
            "workspace_path": str(workspace.get("workspace_path") or ""),
            "file_count": int(workspace.get("file_count") or 0),
            "figure_count": int(workspace.get("figure_count") or 0),
            "figures": (workspace.get("figures") or [])[:6],
        },
        "recent_memories": (context.get("section_memories") or [])[-6:],
    }


def _suggestion_cards(project_id: str, topic: str, limit: int = 12) -> list[dict[str, Any]]:
    try:
        from .config import load_config

        config = load_config()
        items = search_library(config, topic, limit=limit) if topic.strip() else []
    except Exception:
        items = []
    cards: list[dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        abstract = str(item.get("abstract") or "").strip()
        venue = str(item.get("venue") or "").strip()
        year = str(item.get("year") or "").strip()
        cards.append(
            {
                "title": title,
                "venue": venue,
                "year": year,
                "summary": abstract[:220],
                "keywords": [str(keyword) for keyword in (item.get("keywords") or [])[:4]],
            }
        )
    return cards


def get_exploration_report(project_id: str, topic: str = "") -> dict[str, Any]:
    state = _ensure_state(project_id)
    project = load_project(project_id)
    active_topic = str(topic or state.get("exploration", {}).get("topic") or project.get("goal") or project.get("title") or "").strip()
    cards = _suggestion_cards(project_id, active_topic)
    buckets = [
        {"label": "直接相关", "count": min(len(cards), 8)},
        {"label": "方法/方案", "count": max(min(len(cards) - 2, 6), 0)},
        {"label": "应用与背景", "count": max(min(len(cards) - 4, 5), 0)},
    ]
    suggestions = [
        {
            "id": "survey",
            "label": "综述型",
            "title": f"{active_topic} 文献综述",
            "fit": "适合先梳理研究脉络和证据覆盖。",
            "strength": "★★★☆" if len(cards) >= 6 else "★★☆☆",
        },
        {
            "id": "problem",
            "label": "问题导向型",
            "title": f"围绕 {active_topic} 的问题拆解与解决路径",
            "fit": "适合开题/申报书，强调问题、方案与可行性。",
            "strength": "★★★☆" if len(cards) >= 5 else "★★☆☆",
        },
        {
            "id": "method",
            "label": "方法/系统型",
            "title": f"面向 {active_topic} 的方法或系统设计",
            "fit": "适合已有代码工作区、实验流程或实现基础的项目。",
            "strength": "★★★☆" if load_workspace_index(project_id).get("file_count") else "★★☆☆",
        },
    ]
    exploration = {
        "topic": active_topic,
        "completed": False,
        "summary": f"勘探中：{active_topic}",
        "coverage": buckets,
        "insights": [
            f"当前本地文献线索 {len(cards)} 条，适合先缩小问题边界再逐章协商。",
            "如果已有代码工作区，可以优先把方法和实验相关章节前置写作。",
        ],
        "suggestions": suggestions,
        "selected_topic": "",
        "evidence_cards": cards[:8],
        "updated_at": _utc_iso(),
    }
    state["exploration"] = exploration
    state["stage"] = WorkflowStage.EXPLORATION.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    record_project_turn(project_id, "assistant", f"生成了选题勘探报告：{active_topic}", kind="workflow:exploration")
    return exploration


def select_exploration_topic(project_id: str, selected_topic: str, *, selection_id: str = "") -> dict[str, Any]:
    state = _ensure_state(project_id)
    exploration = dict(state.get("exploration") or {})
    topic = str(selected_topic or exploration.get("topic") or "").strip()
    exploration["selected_topic"] = topic
    exploration["completed"] = True
    exploration["summary"] = f"STAGE 1 完成 — 选题：{topic}"
    exploration["selection_id"] = str(selection_id or "").strip()
    exploration["updated_at"] = _utc_iso()
    state["exploration"] = exploration
    state["stage"] = WorkflowStage.OUTLINE_NEGOTIATION.value
    selected_first = False
    for item in state.get("sections", []) or []:
        if str(item.get("negotiation") or "") == "skip":
            item["status"] = ChapterState.LOCKED.value
            continue
        if not selected_first and str(item.get("status") or "") in {ChapterState.PENDING.value, ChapterState.UNLOCKED.value}:
            item["status"] = ChapterState.NEGOTIATING.value
            state["current_section_id"] = str(item.get("section_id") or "")
            selected_first = True
        elif str(item.get("status") or "") == ChapterState.NEGOTIATING.value:
            item["status"] = ChapterState.PENDING.value
    if not selected_first and not state.get("current_section_id") and state.get("sections"):
        state["current_section_id"] = str(state["sections"][0].get("section_id") or "")
    _save_state(project_id, state)
    record_project_turn(project_id, "user", f"确认选题：{topic}", kind="workflow:topic")
    return get_workflow_state(project_id)


def start_outline_negotiation(project_id: str) -> dict[str, Any]:
    state = _ensure_state(project_id)
    state["stage"] = WorkflowStage.OUTLINE_NEGOTIATION.value
    for item in state.get("sections", []) or []:
        if str(item.get("negotiation") or "") == "skip":
            continue
        if str(item.get("status") or "") in {ChapterState.PENDING.value, ChapterState.UNLOCKED.value}:
            item["status"] = ChapterState.NEGOTIATING.value
            state["current_section_id"] = str(item.get("section_id") or "")
            break
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    return get_workflow_state(project_id)


def negotiate_section(
    project_id: str,
    section_id: str,
    user_choice: str,
    *,
    strategy_label: str = "",
    custom_note: str = "",
) -> dict[str, Any]:
    state = _ensure_state(project_id)
    briefs = _section_map(project_id)
    brief = briefs.get(section_id)
    if not brief:
        raise ValueError("unknown section_id")
    normalized_choice = str(user_choice or "").strip()
    selected_label = str(strategy_label or "").strip()
    for item in state.get("sections", []) or []:
        if str(item.get("section_id") or "") != section_id:
            continue
        if not selected_label:
            for option in brief.options:
                if normalized_choice == str(option.get("id") or ""):
                    selected_label = str(option.get("label") or "")
                    break
        item["strategy_id"] = normalized_choice
        item["strategy_label"] = selected_label or normalized_choice or "已确认"
        item["custom_note"] = str(custom_note or "").strip()
        item["focus"] = f"{item['strategy_label']} | {brief.writing_guide}"
        item["status"] = ChapterState.PENDING.value
        item["last_updated"] = _utc_iso()
        break
    pending = [item for item in state.get("sections", []) or [] if str(item.get("negotiation") or "") != "skip" and str(item.get("status") or "") == ChapterState.PENDING.value]
    if pending:
        next_item = pending[0]
        next_item["status"] = ChapterState.NEGOTIATING.value
        state["current_section_id"] = str(next_item.get("section_id") or "")
        state["stage"] = WorkflowStage.OUTLINE_NEGOTIATION.value
    else:
        state["stage"] = WorkflowStage.ORDER_SELECTION.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    record_project_turn(project_id, "user", f"确认章节策略：{brief.title} / {selected_label or normalized_choice}", kind="workflow:outline")
    return get_workflow_state(project_id)


def recommend_writing_order(project_id: str, topic_type: str | None = None) -> dict[str, Any]:
    state = _ensure_state(project_id)
    briefs = _section_map(project_id)
    sections = [briefs[str(item.get("section_id") or "")] for item in state.get("sections", []) or [] if str(item.get("section_id") or "") in briefs]
    active_type = str(topic_type or state.get("topic_type") or "")
    order = _recommended_order(sections, active_type)
    labels = []
    for section_id in order:
        brief = briefs.get(section_id)
        if not brief:
            continue
        labels.append({"section_id": section_id, "title": brief.title, "path": brief.path})
    return {"topic_type": active_type, "recommended_order": order, "sections": labels}


def set_writing_order(project_id: str, ordered_section_ids: list[str]) -> dict[str, Any]:
    state = _ensure_state(project_id)
    normalized = [str(item).strip() for item in ordered_section_ids if str(item).strip()]
    if not normalized:
        raise ValueError("ordered_section_ids is required")
    seen = set()
    filtered = []
    for item in normalized:
        if item in seen:
            continue
        seen.add(item)
        filtered.append(item)
    remaining = [str(item.get("section_id") or "") for item in state.get("sections", []) or [] if str(item.get("section_id") or "") not in seen]
    final_order = filtered + remaining
    rank = {section_id: index + 1 for index, section_id in enumerate(final_order)}
    for item in state.get("sections", []) or []:
        item["write_order"] = rank.get(str(item.get("section_id") or ""), int(item.get("write_order") or 999))
        if str(item.get("negotiation") or "") == "skip":
            item["status"] = ChapterState.LOCKED.value
        elif str(item.get("status") or "") == ChapterState.NEGOTIATING.value:
            item["status"] = ChapterState.PENDING.value
    ordered_items = sorted(state.get("sections", []) or [], key=lambda item: int(item.get("write_order") or 999))
    next_section = next(
        (
            item
            for item in ordered_items
            if str(item.get("negotiation") or "") != "skip"
            and str(item.get("status") or "") in {ChapterState.PENDING.value, ChapterState.UNLOCKED.value, ChapterState.NEEDS_REVIEW.value}
        ),
        None,
    )
    if next_section:
        next_section["status"] = ChapterState.WRITING.value
        state["current_section_id"] = str(next_section.get("section_id") or "")
    state["stage"] = WorkflowStage.CHAPTER_WRITING.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    record_project_turn(project_id, "assistant", "已设置写作顺序。", kind="workflow:order")
    return get_workflow_state(project_id)


def start_chapter_writing(project_id: str, section_id: str) -> dict[str, Any]:
    state = _ensure_state(project_id)
    matched = False
    for item in state.get("sections", []) or []:
        if str(item.get("section_id") or "") == section_id:
            if str(item.get("status") or "") != ChapterState.LOCKED.value:
                item["status"] = ChapterState.WRITING.value
            state["current_section_id"] = section_id
            matched = True
        elif str(item.get("status") or "") == ChapterState.WRITING.value:
            item["status"] = ChapterState.PENDING.value
    if not matched:
        raise ValueError("unknown section_id")
    state["stage"] = WorkflowStage.CHAPTER_WRITING.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    return get_workflow_state(project_id)


def save_section_draft(project_id: str, section_id: str, content: str, *, prompt: str = "workflow draft save") -> dict[str, Any]:
    state = _ensure_state(project_id)
    brief = _section_map(project_id).get(section_id)
    if not brief:
        raise ValueError("unknown section_id")
    saved = save_project_file(
        {
            "project_id": project_id,
            "path": brief.path,
            "content": str(content or ""),
            "preserve_structure": True,
        }
    )
    update_section_memory(project_id, brief.path, str(content or ""), prompt=prompt)
    for item in state.get("sections", []) or []:
        if str(item.get("section_id") or "") == section_id:
            item["status"] = ChapterState.WRITING.value
            item["summary"] = _locked_summary(project_id, brief.path, brief.title)
            item["last_guardrail_violations"] = (saved.get("guardrails") or {}).get("violations") or []
            item["last_updated"] = _utc_iso()
            break
    state["current_section_id"] = section_id
    state["stage"] = WorkflowStage.CHAPTER_WRITING.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    return {"file": saved, "workflow": get_workflow_state(project_id)}


def apply_section_citations(
    project_id: str,
    section_id: str,
    citation_decisions: dict[str, list[str]],
) -> dict[str, Any]:
    state = _ensure_state(project_id)
    brief = _section_map(project_id).get(section_id)
    if not brief:
        raise ValueError("unknown section_id")
    file_data = read_project_file(project_id, brief.path)
    existing_content = str(file_data.get("content") or "")
    updated_content = apply_citations(existing_content, citation_decisions)
    current_section = _current_section_payload(project_id, state)
    pending_items = current_section.get("pending_citations") or []
    candidate_rows: list[dict[str, Any]] = []
    for item in pending_items:
        for candidate in item.get("candidates") or []:
            if isinstance(candidate, dict):
                candidate_rows.append(candidate)
    bibliography = extract_bibtex_for_decisions(candidate_rows, citation_decisions)
    if bibliography.strip():
        merge_project_bibliography(
            project_id,
            bibliography,
            suggested_name="reference.bib",
            bibliography_profile=load_project(project_id).get("bibliography_profile") or {},
        )
    saved = save_project_file(
        {
            "project_id": project_id,
            "path": brief.path,
            "content": updated_content,
            "preserve_structure": True,
            "bibliography": bibliography,
        }
    )
    chosen_keys = [str(key).strip() for keys in (citation_decisions or {}).values() for key in (keys or []) if str(key).strip()]
    for item in state.get("sections", []) or []:
        if str(item.get("section_id") or "") != section_id:
            continue
        existing = [str(key).strip() for key in (item.get("approved_citations") or []) if str(key).strip()]
        item["approved_citations"] = list(dict.fromkeys(existing + chosen_keys))
        item["summary"] = _locked_summary(project_id, brief.path, brief.title)
        item["last_guardrail_violations"] = (saved.get("guardrails") or {}).get("violations") or []
        item["last_updated"] = _utc_iso()
        break
    _save_state(project_id, state)
    record_project_turn(
        project_id,
        "user",
        f"批准章节引用：{brief.title}",
        kind="workflow:citation-apply",
        file_path=brief.path,
        metadata={"approved_keys": chosen_keys, "placeholder_count": len(citation_decisions or {})},
    )
    return {"file": saved, "workflow": get_workflow_state(project_id)}


def lock_chapter(project_id: str, section_id: str) -> dict[str, Any]:
    state = _ensure_state(project_id)
    locked_section_title = ""
    locked_path = ""
    items = state.get("sections", []) or []
    for item in items:
        if str(item.get("section_id") or "") == section_id:
            item["status"] = ChapterState.LOCKED.value
            item["locked_at"] = _utc_iso()
            item["last_updated"] = _utc_iso()
            locked_section_title = str(item.get("title") or "")
            locked_path = str(item.get("path") or "")
            item["summary"] = _locked_summary(project_id, locked_path, locked_section_title)
            break
    next_section_id = ""
    for item in sorted(items, key=lambda entry: int(entry.get("write_order") or 999)):
        if str(item.get("status") or "") in {ChapterState.PENDING.value, ChapterState.UNLOCKED.value, ChapterState.NEEDS_REVIEW.value}:
            item["status"] = ChapterState.WRITING.value
            next_section_id = str(item.get("section_id") or "")
            break
    state["current_section_id"] = next_section_id or section_id
    if not next_section_id:
        state["stage"] = WorkflowStage.FINAL_REVIEW.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    compile_result = compile_project(project_id)
    record_project_turn(project_id, "assistant", f"已锁定章节：{locked_section_title or section_id}", kind="workflow:lock", file_path=locked_path)
    return {
        "workflow": get_workflow_state(project_id),
        "compile": compile_result,
    }


def unlock_chapter(project_id: str, section_id: str, cascade: bool = False) -> dict[str, Any]:
    state = _ensure_state(project_id)
    affected: list[dict[str, Any]] = []
    hit = False
    for item in sorted(state.get("sections", []) or [], key=lambda entry: int(entry.get("write_order") or 999)):
        current_id = str(item.get("section_id") or "")
        if current_id == section_id:
            hit = True
            item["status"] = ChapterState.UNLOCKED.value
            item["last_updated"] = _utc_iso()
            affected.append({"section_id": current_id, "title": str(item.get("title") or ""), "effect": "当前章节已解锁"})
            continue
        if hit and str(item.get("status") or "") == ChapterState.LOCKED.value:
            if cascade:
                item["status"] = ChapterState.UNLOCKED.value
                affected.append({"section_id": current_id, "title": str(item.get("title") or ""), "effect": "后续章节已解锁"})
            else:
                item["status"] = ChapterState.NEEDS_REVIEW.value
                affected.append({"section_id": current_id, "title": str(item.get("title") or ""), "effect": "标记为需复核"})
            item["last_updated"] = _utc_iso()
    state["current_section_id"] = section_id
    state["stage"] = WorkflowStage.CHAPTER_WRITING.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    record_project_turn(project_id, "user", f"解锁章节：{section_id}", kind="workflow:unlock", metadata={"cascade": cascade})
    return {"workflow": get_workflow_state(project_id), "affected": affected}


def compress_context(project_id: str, current_section_id: str) -> dict[str, Any]:
    state = _ensure_state(project_id)
    briefs = _section_map(project_id)
    locked_summaries = []
    for item in state.get("sections", []) or []:
        if str(item.get("status") or "") != ChapterState.LOCKED.value:
            continue
        section_id = str(item.get("section_id") or "")
        brief = briefs.get(section_id)
        if not brief or section_id == current_section_id:
            continue
        locked_summaries.append(
            {
                "section_id": section_id,
                "title": brief.title,
                "summary": str(item.get("summary") or _locked_summary(project_id, brief.path, brief.title)),
            }
        )
    brief = briefs.get(current_section_id)
    source_files = load_project_sources(project_id, include_text=False)[:6]
    workspace = load_workspace_index(project_id)
    return {
        "current_section": {
            "section_id": current_section_id,
            "title": brief.title if brief else "",
            "path": brief.path if brief else "",
            "guide": brief.writing_guide if brief else "",
        },
        "locked_summaries": locked_summaries[:6],
        "source_files": source_files,
        "workspace": {
            "workspace_name": str(workspace.get("workspace_name") or ""),
            "file_count": int(workspace.get("file_count") or 0),
            "figure_count": int(workspace.get("figure_count") or 0),
        },
    }


def run_final_review(project_id: str) -> dict[str, Any]:
    state = _ensure_state(project_id)
    project = load_project(project_id)
    compile_result = compile_project(project_id)
    audit = run_full_audit(
        project_id,
        profile=project.get("template_profile") or build_template_profile(
            project_id,
            template_id=str(project.get("template_id") or ""),
            project_dir=Path(project.get("paths", {}).get("dir") or ""),
        ),
        api_key="",
        model="",
    )
    result = {
        "compile": compile_result,
        "audit": {
            "verdict": audit.verdict,
            "overall_score": audit.overall_score,
            "issue_count": len(audit.issues),
            "issues": [
                {
                    "mode": issue.mode,
                    "severity": issue.severity,
                    "category": issue.category,
                    "location": issue.location,
                    "description": issue.description,
                    "fix_suggestion": issue.fix_suggestion,
                }
                for issue in audit.issues
            ],
        },
    }
    state["stage"] = WorkflowStage.COMPLETE.value if compile_result.get("status") == "compiled" and audit.verdict == "ACCEPT" else WorkflowStage.FINAL_REVIEW.value
    state["updated_at"] = _utc_iso()
    _save_state(project_id, state)
    record_project_turn(project_id, "assistant", f"完成终审：{audit.verdict}", kind="workflow:final-review")
    return result


def get_workflow_state(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    state = _ensure_state(project_id)
    items = state.get("sections", []) or []
    current_section_id = str(state.get("current_section_id") or "")
    stage = str(state.get("stage") or WorkflowStage.EXPLORATION.value)
    cards = [
        _chapter_card(
            _section_map(project_id).get(str(item.get("section_id") or "")) or SectionBrief(
                section_id=str(item.get("section_id") or ""),
                title=str(item.get("title") or ""),
                path=str(item.get("path") or ""),
                sort_order=0,
                suggested_order=0,
                negotiation=str(item.get("negotiation") or "full"),
                citation_required=False,
                requires_figures=False,
                min_paragraphs=1,
                writing_guide="",
                required_elements=[],
                options=[],
            ),
            item,
            current_section_id=current_section_id,
            project_id=project_id,
        )
        for item in sorted(items, key=lambda entry: int(entry.get("write_order") or 999))
    ]
    counts = {
        "locked": _count_status(items, ChapterState.LOCKED.value),
        "needs_review": _count_status(items, ChapterState.NEEDS_REVIEW.value),
        "writing": _count_status(items, ChapterState.WRITING.value),
        "negotiating": _count_status(items, ChapterState.NEGOTIATING.value),
        "pending": _count_status(items, ChapterState.PENDING.value) + _count_status(items, ChapterState.UNLOCKED.value),
        "citation_pending": _pending_citation_count(project_id),
    }
    exploration = state.get("exploration") or {}
    return {
        "project_id": project_id,
        "project": {
            "title": project.get("title", ""),
            "template_name": project.get("template_name", ""),
            "writing_type": project.get("writing_type", ""),
            "writing_language": project.get("writing_language", ""),
            "project_mode": project.get("project_mode", ""),
            "goal": project.get("goal", ""),
            "requirements": project.get("requirements", ""),
        },
        "stage": stage,
        "topic_type": str(state.get("topic_type") or ""),
        "stage_card": _stage1_card(state, project),
        "counts": counts,
        "sections": cards,
        "current_section": _current_section_payload(project_id, state),
        "order_recommendation": recommend_writing_order(project_id, str(state.get("topic_type") or "")),
        "exploration": exploration,
        "status_bar": f"已锁定 {counts['locked']}/{len(cards)} | 需复核 {counts['needs_review']} | 待审核引用 {counts['citation_pending']}",
        "updated_at": str(state.get("updated_at") or ""),
    }
