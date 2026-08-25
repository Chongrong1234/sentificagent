from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from .attention_pipeline import run_attention_pipeline
from .chat import load_provider_api_key, normalize_model_provider, provider_api_base, provider_label
from .config import PROJECT_ROOT, AppConfig, load_config
from .library_store import search_library
from .survey_reporting import (
    build_survey_records_from_attention,
    build_survey_records_from_evidence,
    build_survey_report,
)
from .template_library import get_template, get_template_structure, render_template_starter
from .template_profile import build_template_profile, template_comprehension_prompt


WRITING_SKILLS_DIR = PROJECT_ROOT / "tools" / "writing-skills"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "library" / "writing_runs"

TEMPLATE_GUARDIAN_PROMPT = (
    "你是学术写作智能体，核心身份是「模板守护者」。\n"
    "\n"
    "你必须严格遵循以下规则，违反即为严重错误：\n"
    "\n"
    "1. **模板结构不可侵犯**：绝不修改 documentclass、导言区、\\maketitle 区域、"
    "参考文献尾部结构。绝不新增、删除、重排序或重命名章节。"
    "仅当用户在本轮对话中明确要求「增加章节」「删除章节」「调整结构」「修改框架」时才可变更。\n"
    "2. **引用系统不可切换**：必须沿用模板已有引用命令（\\citep/\\citet/\\parencite/\\autocite 等），"
    "不得擅自改为 \\cite。模板用 biblatex 则保持 biblatex，用 bibtex 则保持 bibtex。\n"
    "3. **章节层级必须匹配**：模板用 \\chapter 则输出 \\chapter，模板用 \\section 则输出 \\section，"
    "不得自行改变层级。\n"
    "4. **只输出可编译正文**：输出放在 \\begin{document} 之后、参考文献区之前的 LaTeX 内容，"
    "不输出 markdown 代码块。\n"
    "5. **去AI味、学术规范**：使用具体、有信息量的学术语言。禁止空洞套话（如 \"It is worth noting that\"、"
    "\"Furthermore\" 机械堆砌、\"in this paper we\" 泛滥），禁止无实质内容的过渡句。"
    "每句话应承载具体信息：定义问题、引用证据、比较方法、指出差距、提出路线、分析风险。\n"
    "6. **先读懂模板再写**：收到任务后，先识别模板中已有的所有章节标题和顺序，"
    "确认每个章节的写作职责，然后严格按照该框架填充内容。"
)


@dataclass
class WorkflowState:
    goal: str
    writing_type: str = "academic"
    writing_language: str = "en"
    template_id: str = ""
    project_mode: str = ""
    force_sectional: bool = False
    requirements: str = ""
    query: str = ""
    use_literature_pipeline: bool = False
    max_literature_results: int = 12
    summarize_limit: int = 4
    rag_limit: int = 0
    exclude_preprints: bool = False
    api_key: str = ""
    model_provider: str = "kimi"
    planner_model: str = ""
    runner_model: str = ""
    run_id: str = ""
    plan: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_memory: dict[str, Any] = field(default_factory=dict)
    section_memories: list[dict[str, Any]] = field(default_factory=list)
    source_materials: list[dict[str, Any]] = field(default_factory=list)
    workspace_index: dict[str, Any] = field(default_factory=dict)
    workspace_analysis: dict[str, Any] = field(default_factory=dict)
    writing_profile: dict[str, Any] = field(default_factory=dict)
    survey_report: dict[str, Any] = field(default_factory=dict)
    agent_outputs: dict[str, Any] = field(default_factory=dict)
    review_report: dict[str, Any] = field(default_factory=dict)
    bibliography_profile: dict[str, Any] = field(default_factory=dict)
    template_profile: dict[str, Any] = field(default_factory=dict)
    literature_result: dict[str, Any] = field(default_factory=dict)
    latex: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    compile_result: dict[str, Any] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    error: str = ""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run_id() -> str:
    return f"writing-{_utc_stamp()}"


def _load_api_key(provider: str, explicit: str = "") -> str:
    return load_provider_api_key(provider, explicit)


def _normalize_writing_language(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"en", "english", "en-us", "en-gb"}:
        return "en"
    return "zh"


def _template_heading_command(template_id: str) -> str:
    if not str(template_id or "").strip():
        return r"\section"
    try:
        structure = get_template_structure(template_id)
        if structure.get("is_book_like"):
            return r"\chapter"
    except (KeyError, OSError):
        pass
    return r"\section"


def _template_structure_hint(template_id: str, template_profile: dict[str, Any] = None) -> str:
    """Build template comprehension for LLM prompts, prefering full profile over minimal hint."""
    if template_profile and template_profile.get("document_class"):
        return template_comprehension_prompt(template_profile)
    if not str(template_id or "").strip():
        return "无模板，使用默认文章结构。"
    try:
        s = get_template_structure(template_id)
        heading = s.get("heading_command", r"\section")
        chapters = s.get("chapters") or []
        front = s.get("frontmatter_files") or []
        back = s.get("backmatter_files") or []
        chapters_text = "\n  ".join(chapters) if chapters else "（无）"
        front_text = "\n  ".join(front) if front else "（无）"
        back_text = "\n  ".join(back) if back else "（无）"
        n = len(chapters)
        return (
            f"文档类：{s.get('document_class', '?')}（顶级标题：{heading}）\n"
            f"章节文件（{n}个，需一一填充）：\n  {chapters_text}\n"
            f"前置文件：\n  {front_text}\n"
            f"后置文件：\n  {back_text}\n"
            f"提示：你的 sections 计划是顶层章节，每个将写入一个章节文件。有 {n} 个章节文件，请规划恰好 {n} 个顶层章节。"
        )
    except (KeyError, OSError):
        return "无法解析模板结构，使用默认文章结构。"


def _template_language(template_id: str) -> str:
    if not str(template_id or "").strip():
        return ""
    try:
        return _normalize_writing_language(get_template(template_id).get("language") or "")
    except Exception:
        return ""


def _language_name(language: str) -> str:
    return "English" if _normalize_writing_language(language) == "en" else "Chinese"


def _default_academic_sections(language: str) -> list[str]:
    if _normalize_writing_language(language) == "en":
        return [
            "Abstract",
            "Introduction",
            "Related Work",
            "Methodology",
            "Experiments",
            "Results and Discussion",
            "Conclusion",
            "References",
        ]
    return [
        "摘要",
        "引言",
        "相关工作",
        "方法",
        "实验与结果分析",
        "讨论",
        "结论",
        "参考文献",
    ]


def _source_material_text(item: dict[str, Any]) -> str:
    return str(item.get("text") or item.get("excerpt") or "").strip()


def _source_material_role(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").lower()
    kind = str(item.get("kind") or "").lower()
    text = _source_material_text(item).lower()
    probe = "\n".join([name, kind, text[:4000]])
    if any(token in probe for token in ["guideline", "requirement", "requirements", "模板", "格式", "投稿", "基金指南", "申报要求"]):
        return "requirement"
    if any(token in probe for token in ["result", "results", "experiment", "ablation", "benchmark", "table", "figure", "metric", "实验", "结果", "消融", "对比", "性能"]):
        return "result"
    if kind in {"py", "ipynb"} or any(token in probe for token in ["train.py", "model.py", "predict.py", "script", "代码", "源码", "算法实现"]):
        return "code"
    if any(token in probe for token in ["method", "approach", "framework", "pipeline", "algorithm", "模型", "方法", "技术路线", "方案", "网络结构"]):
        return "method"
    if any(token in probe for token in ["dataset", "data", "corpus", "benchmark", "数据集", "样本", "采样"]):
        return "dataset"
    return "general"


def _build_source_material_cards(state: WorkflowState) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for index, item in enumerate(state.source_materials, start=1):
        if not isinstance(item, dict):
            continue
        text = _source_material_text(item)
        if not text:
            continue
        role = _source_material_role(item)
        keywords = _extract_keywords(" ".join([str(item.get("name") or ""), text]), limit=12)
        cards.append(
            {
                "id": f"M{index}",
                "name": str(item.get("name") or f"material-{index}"),
                "kind": str(item.get("kind") or ""),
                "role": role,
                "keywords": keywords,
                "excerpt": _truncate(text, 420),
            }
        )
    return cards


def _infer_execution_mode(state: WorkflowState) -> str:
    material_roles = {_source_material_role(item) for item in state.source_materials if isinstance(item, dict)}
    if material_roles & {"method", "result", "code", "dataset"}:
        return "results_first"
    if (state.workspace_index or {}).get("entries") or (state.workspace_index or {}).get("figures"):
        return "results_first"
    return "literature_first"


def _resolve_writing_profile(state: WorkflowState) -> dict[str, Any]:
    # Thesis/book templates define chapter structure — force academic regardless
    # of writing_type hints that may have been inferred from requirements text.
    tp = state.template_profile or {}
    top_level = (tp.get("section_hierarchy") or {}).get("top_level", "")
    doc_name = (tp.get("document_class") or {}).get("name", "")
    is_thesis_template = top_level == "chapter" or doc_name in {"book", "ctexbook", "report", "ctexrep", "hithesisbook"}
    effective_type = "academic" if (is_thesis_template and state.writing_type == "grant") else state.writing_type
    language = _normalize_writing_language(
        state.writing_language or _template_language(state.template_id) or ("zh" if effective_type == "grant" else "en")
    )
    execution_mode = _infer_execution_mode(state)
    source_cards = _build_source_material_cards(state)
    if effective_type == "grant" and language == "zh":
        return {
            "profile_id": "cn_grant",
            "label": "Chinese Grant Proposal",
            "execution_mode": execution_mode,
            "language": language,
            "default_sections": _grant_sections(),
            "planner_hint": "按中文基金申报书组织章节，突出立项依据、关键科学问题、研究内容、技术路线、创新点、研究基础与风险对策。",
            "material_policy": "若存在用户材料、代码或预实验结果，应将其作为研究基础、可行性分析和技术路线的直接依据，不得把预实验包装成已完成最终成果。",
            "results_policy": "只能把已有结果写成预实验观察、研究基础或可行性支撑，不得编造最终指标、显著性结论或超额承诺。",
            "citation_policy": "优先引用综述、奠基方法、数据集/评价指标原始论文和最相关竞争方法，支撑立项依据与技术路线。",
            "section_style": "正式、具体、面向评审专家，强调问题牵引、技术可行、任务拆解和风险控制。",
            "source_cards": source_cards,
        }
    if effective_type == "academic" and language == "en":
        return {
            "profile_id": "en_paper",
            "label": "English Academic Paper",
            "execution_mode": execution_mode,
            "language": language,
            "default_sections": _default_academic_sections(language),
            "planner_hint": "Use an English academic paper structure and keep the narrative close to IMRaD plus related work.",
            "material_policy": "If user materials, code, or real results are available, Methodology and Experiments must be anchored to them rather than invented from literature alone.",
            "results_policy": "Use real observations, plots, and implementation details when available. If exact metrics are missing, describe trends and experimental setup without fabricating numbers.",
            "citation_policy": "RAG should focus on related work, baseline methods, datasets, metrics, and foundational papers that situate the user's method.",
            "section_style": "IEEE-like academic prose with explicit problem framing, methodological precision, empirical analysis, and concise claims.",
            "source_cards": source_cards,
        }
    return {
        "profile_id": f"{effective_type}_{language}",
        "label": f"{effective_type}-{language}",
        "execution_mode": execution_mode,
        "language": language,
        "default_sections": _grant_sections() if effective_type == "grant" else _default_academic_sections(language),
        "planner_hint": "Follow the requested language and genre, preserving template constraints first.",
        "material_policy": "Prefer user-provided materials and workspace evidence whenever they exist.",
        "results_policy": "Do not fabricate unsupported quantitative results.",
        "citation_policy": "Use local real literature and keep references aligned with the template bibliography system.",
        "section_style": "Stable scholarly prose that matches the requested template and language.",
        "source_cards": source_cards,
    }


def _ensure_writing_profile(state: WorkflowState) -> dict[str, Any]:
    if not state.writing_profile:
        state.writing_profile = _resolve_writing_profile(state)
    return state.writing_profile


def _source_cards_for_section(state: WorkflowState, section: str, limit: int = 4) -> list[dict[str, Any]]:
    profile = _ensure_writing_profile(state)
    role = _section_role(section, state)
    preferred_roles = {
        "abstract": ["method", "result", "code", "dataset", "general"],
        "introduction": ["general", "requirement", "method", "result"],
        "related_work": ["method", "result", "dataset", "general"],
        "method": ["method", "code", "dataset", "general"],
        "experiments": ["result", "dataset", "code", "method"],
        "results": ["result", "dataset", "code", "method"],
        "discussion": ["result", "method", "general"],
        "conclusion": ["result", "method", "general"],
        "foundation": ["result", "method", "code", "dataset"],
        "plan": ["method", "code", "result", "dataset", "general"],
        "risk": ["method", "result", "code", "general"],
        "references": ["general"],
        "general": ["general", "method", "result", "dataset", "code"],
    }
    preferred = preferred_roles.get(role, ["general"])
    weights = {name: len(preferred) - index for index, name in enumerate(preferred)}
    selected: list[tuple[int, dict[str, Any]]] = []
    section_terms = {term.lower() for term in _section_query_terms(section, state)}
    for card in profile.get("source_cards", []):
        role_bonus = 20 if str(card.get("role") or "") in preferred else 0
        keyword_hits = sum(1 for term in section_terms if term and term in " ".join(str(item) for item in card.get("keywords", [])).lower())
        score = role_bonus + keyword_hits * 6 + weights.get(str(card.get("role") or ""), 0)
        selected.append((score, card))
    selected.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    return [card for score, card in selected[:limit] if score > 0]


def _source_material_context(state: WorkflowState, section: str = "", limit: int = 4) -> str:
    cards = _source_cards_for_section(state, section, limit=limit) if section else _ensure_writing_profile(state).get("source_cards", [])[:limit]
    if not cards:
        return "暂无用户上传的补充方法/结果材料。"
    rows = []
    for card in cards:
        rows.append(
            f"- [{card.get('id', '')}/{card.get('role', '')}] {card.get('name', '')}\n"
            f"  关键词: {', '.join(card.get('keywords') or [])}\n"
            f"  摘要: {card.get('excerpt', '')}"
        )
    return "\n".join(rows)


def _section_role(section: str, state: WorkflowState) -> str:
    text = str(section or "").strip()
    lowered = text.lower()
    if lowered in {"abstract", "摘要"} or "摘要" in text:
        return "abstract"
    if "related work" in lowered or "相关工作" in text or "现状" in text or "研究基础" in text:
        return "related_work" if "基础" not in text else "foundation"
    if "introduction" in lowered or "引言" in text or "背景" in text or "立项依据" in text or "意义" in text:
        return "introduction"
    if any(token in lowered for token in ["method", "methodology", "approach"]) or any(token in text for token in ["方法", "研究内容", "技术路线", "研究方案", "模型", "方案"]):
        return "method" if "实验" not in text else "experiments"
    if any(token in lowered for token in ["experiment", "results", "evaluation", "discussion"]) or any(token in text for token in ["实验", "结果", "分析", "评估", "可行性"]):
        return "results" if "讨论" not in text and "可行性" not in text else "discussion"
    if any(token in lowered for token in ["conclusion", "summary"]) or any(token in text for token in ["结论", "总结"]):
        return "conclusion"
    if any(token in text for token in ["关键科学问题", "研究目标", "创新点"]):
        return "method"
    if any(token in text for token in ["进度", "年度", "计划", "目标"]):
        return "plan"
    if any(token in text for token in ["困难", "风险", "对策"]):
        return "risk"
    if any(token in text for token in ["参考", "references"]):
        return "references"
    return "general"


def _section_brief(state: WorkflowState, section: str) -> dict[str, Any]:
    profile = _ensure_writing_profile(state)
    role = _section_role(section, state)
    profile_id = str(profile.get("profile_id") or "")
    execution_mode = str(profile.get("execution_mode") or "literature_first")
    facet_weights: dict[str, int]
    mission = "Explain the section clearly and support claims with local evidence."
    if role == "abstract":
        facet_weights = {"problem": 8, "method": 16, "result": 16, "application": 6}
        mission = "Summarize the problem, the core method, the most defensible findings, and the practical contribution."
    elif role == "introduction":
        facet_weights = {"problem": 18, "review": 8, "application": 8, "risk": 6}
        mission = "Frame the problem, motivate the task, and explain why the proposed line of work is necessary."
    elif role == "related_work":
        facet_weights = {"review": 18, "method": 14, "baseline": 12, "dataset": 6}
        mission = "Position the user's work against the closest methods, baselines, datasets, and open gaps."
    elif role == "method":
        facet_weights = {"method": 20, "foundation": 12, "dataset": 8, "baseline": 6}
        mission = "Describe the actual method, pipeline, and implementation logic with enough detail to be reproducible."
    elif role in {"experiments", "results", "discussion"}:
        facet_weights = {"result": 20, "dataset": 12, "metric": 10, "baseline": 10, "risk": 6}
        mission = "Explain the experimental design, analyze the observed outcomes, and avoid unsupported quantitative claims."
    elif role == "foundation":
        facet_weights = {"result": 16, "method": 12, "application": 8, "problem": 8}
        mission = "Use existing code, data, or preliminary findings as research basis and feasibility evidence."
    elif role == "plan":
        facet_weights = {"method": 12, "problem": 8, "risk": 8, "application": 6}
        mission = "Translate the proposed work into concrete tasks, milestones, and expected outputs."
    elif role == "risk":
        facet_weights = {"risk": 20, "problem": 12, "method": 8}
        mission = "Identify realistic risks and connect each one to a mitigation strategy."
    elif role == "conclusion":
        facet_weights = {"result": 16, "method": 12, "application": 8}
        mission = "Close the argument by restating the contribution, evidence, limitations, and next step."
    else:
        facet_weights = {"problem": 8, "method": 8, "result": 8, "review": 8}
    forbidden = [
        "Do not invent papers, datasets, metrics, or implementation details.",
        "Do not use placeholder wording or generic section filler.",
    ]
    if execution_mode == "results_first" and role in {"method", "experiments", "results", "discussion", "foundation"}:
        forbidden.append("Do not contradict user materials, code, figures, or existing results.")
    if role in {"experiments", "results", "discussion"}:
        forbidden.append("Do not fabricate exact numbers when the workspace or source materials do not provide them.")
    if profile_id == "cn_grant":
        forbidden.append("Do not present preliminary findings as already-completed final project deliverables.")
    return {
        "section": section,
        "role": role,
        "execution_mode": execution_mode,
        "mission": mission,
        "facet_weights": facet_weights,
        "source_materials": [card.get("id", "") for card in _source_cards_for_section(state, section)],
        "forbidden": forbidden,
        "style": str(profile.get("section_style") or ""),
    }


def _format_section_brief(brief: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"section: {brief.get('section', '')}",
            f"role: {brief.get('role', '')}",
            f"execution_mode: {brief.get('execution_mode', '')}",
            f"mission: {brief.get('mission', '')}",
            "forbidden: " + " | ".join(str(item) for item in brief.get("forbidden", []) or []),
            f"style: {brief.get('style', '')}",
        ]
    )


def _multi_agent_blueprint(state: WorkflowState) -> list[dict[str, str]]:
    profile = _ensure_writing_profile(state)
    execution_mode = str((state.plan or {}).get("execution_mode") or profile.get("execution_mode") or "literature_first")
    stages = [
        {
            "agent": "leader",
            "mission": "Normalize the user goal, writing mode, template constraints, and retrieval scope.",
        },
        {
            "agent": "surveyor",
            "mission": "Fuse local RAG evidence, fresh crawling results, and user materials into a literature survey packet.",
        },
    ]
    if execution_mode == "literature_first":
        stages.append(
            {
                "agent": "ideator",
                "mission": "Convert the survey packet into a problem framing, gap statement, and paper/proposal positioning.",
            }
        )
    stages.extend(
        [
            {
                "agent": "architect",
                "mission": "Turn the survey packet and writing profile into outline responsibilities and section contracts.",
            },
            {
                "agent": "writer",
                "mission": "Draft each section with section-level evidence allocation and strict template/language control.",
            },
            {
                "agent": "reviewer",
                "mission": "Check section coverage, unsupported claims, placeholders, and citation/structure quality before compile.",
            },
        ]
    )
    return stages


def _dedupe_report_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        key = re.sub(
            r"\s+",
            " ",
            " ".join(
                [
                    str(record.get("title") or ""),
                    str(record.get("year") or ""),
                    str(record.get("venue") or ""),
                ]
            ).strip().lower(),
        )
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _survey_records_for_workflow(state: WorkflowState) -> list[dict[str, Any]]:
    records = build_survey_records_from_evidence(state.evidence)
    literature_records = build_survey_records_from_attention(list(state.literature_result.get("summaries") or []))
    return _dedupe_report_records(literature_records + records)


def _architect_hints_from_report(report: dict[str, Any], brief: dict[str, Any]) -> list[str]:
    stats = report.get("stats") if isinstance(report, dict) else {}
    role = str(brief.get("role") or "")
    hints: list[str] = []
    if role in {"related_work", "introduction"}:
        hints.extend(str(item.get("name") or "") for item in (stats.get("top_methods") or [])[:4])
    if role in {"method", "experiments", "results", "discussion", "foundation"}:
        hints.extend(str(item.get("name") or "") for item in (stats.get("top_datasets") or [])[:4])
    if role in {"discussion", "risk", "conclusion"}:
        hints.extend(str(item.get("name") or "") for item in (stats.get("top_limitations") or [])[:4])
    return [item for item in dict.fromkeys(hints) if item]


_SUPPORTED_CITE_COMMANDS = (
    "parencite",
    "textcite",
    "autocite",
    "smartcite",
    "footcite",
    "footcitetext",
    "citep",
    "citet",
    "citeauthor",
    "citeyearpar",
    "citeyear",
    "cite",
)


def _preferred_cite_command(profile: dict[str, Any] | None = None) -> str:
    commands = [
        str(item).strip()
        for item in ((profile or {}).get("cite_commands") or [])
        if str(item).strip()
    ]
    preferred_generics = [
        "parencite",
        "citep",
        "autocite",
        "smartcite",
        "footcite",
        "textcite",
        "citet",
        "cite",
    ]
    for candidate in commands:
        if candidate in preferred_generics:
            return candidate
    for candidate in preferred_generics:
        if candidate in commands:
            return candidate
    return "cite"


def _normalize_bibliography_profile(value: Any) -> dict[str, Any]:
    profile = value if isinstance(value, dict) else {}
    backend = str(profile.get("backend") or "").strip().lower()
    cite_commands = [str(item).strip() for item in (profile.get("cite_commands") or []) if str(item).strip()]
    bib_files = [str(item).strip() for item in (profile.get("bib_files") or []) if str(item).strip()]
    tail = str(profile.get("tail") or "").strip()
    source_paths = [str(item).strip() for item in (profile.get("source_paths") or []) if str(item).strip()]
    project_mode = str(profile.get("project_mode") or "").strip()
    return {
        "backend": backend,
        "cite_commands": list(dict.fromkeys(cite_commands)),
        "bib_files": list(dict.fromkeys(bib_files)),
        "tail": tail,
        "source_paths": list(dict.fromkeys(source_paths)),
        "project_mode": project_mode,
        "preferred_cite_command": _preferred_cite_command({"cite_commands": cite_commands}),
    }


def _bibliography_command_hint(profile: dict[str, Any]) -> str:
    backend = str(profile.get("backend") or "").strip().lower()
    cite_commands = [str(item).strip() for item in (profile.get("cite_commands") or []) if str(item).strip()]
    bib_files = [str(item).strip() for item in (profile.get("bib_files") or []) if str(item).strip()]
    preferred_command = _preferred_cite_command(profile)
    cite_text = ", ".join(f"\\{item}" for item in cite_commands[:6]) if cite_commands else "未检测到"
    bib_text = ", ".join(bib_files[:4]) if bib_files else "未检测到"
    project_mode = str(profile.get("project_mode") or "").strip()
    tail = str(profile.get("tail") or "").strip()
    if backend == "biblatex":
        return (
            "模板使用 biblatex。请沿用 \\addbibresource / \\printbibliography，"
            "不要改成 \\bibliographystyle / \\bibliography。"
            f"优先使用模板已有命令（{cite_text}），新增引用默认使用 \\{preferred_command}。"
            "不要把原模板里的引用命令重写成标准 \\cite。"
            f"Bib 文件：{bib_text}。"
        )
    if backend in {"natbib", "bibtex"}:
        return (
            f"模板使用 {backend}。请沿用 \\bibliographystyle / \\bibliography，"
            "不要改成 biblatex。"
            f"优先使用模板已有命令（{cite_text}），新增引用默认使用 \\{preferred_command}。"
            "不要把原模板里的引用命令重写成标准 \\cite。"
            f"Bib 文件：{bib_text}。"
        )
    if tail:
        return "模板已有明确的参考文献尾部，请原样沿用，不要改写成另一套系统。"
    if project_mode == "manual_upload":
        return "这是手动上传模板；如果模板没有明确的参考文献入口，不要擅自新增一套引用系统，只沿用原模板中的命令和文件。"
    if cite_commands or bib_files:
        return f"已检测到引用命令（{cite_text}）和 Bib 文件（{bib_text}）；请沿用模板已有格式，不要自行切换参考文献系统。"
    return "未检测到明确参考文献系统；请沿用模板中已有的引用命令和 .bib 文件，不要擅自改写参考文献格式。"


def _language_instruction(language: str) -> str:
    if _normalize_writing_language(language) == "en":
        return (
            "All narrative text, section titles, abstract text, figure captions, and discussion must be written in English. "
            "Do not output Chinese prose unless the template itself requires fixed Chinese boilerplate."
        )
    return (
        "All narrative text, section titles, abstract text, figure captions, and discussion must be written in Chinese. "
        "Do not output English prose except standard bibliography commands or fixed template boilerplate."
    )


def _chat_completion(
    api_key: str,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: int = 180,
    retries: int | None = None,
    max_tokens: int | None = None,
) -> str:
    if not api_key:
        raise RuntimeError(f"Missing {provider_label(provider)} API key.")
    body = {
        "model": model,
        "messages": messages,
    }
    if max_tokens and max_tokens > 0:
        body["max_tokens"] = int(max_tokens)
    attempts = max(1, retries if retries is not None else int(os.environ.get("KIMI_MAX_RETRIES", "2")))
    payload: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        req = request.Request(
            f"{provider_api_base(provider)}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code not in {408, 409, 429, 500, 502, 503, 504} or attempt == attempts:
                raise RuntimeError(f"{provider_label(provider)} API error: {exc.code} {detail}") from exc
            time.sleep(min(30, 3 * attempt * attempt))
        except socket.timeout as exc:
            if attempt == attempts:
                raise RuntimeError(f"{provider_label(provider)} read timeout after {timeout}s") from exc
            time.sleep(min(30, 3 * attempt * attempt))
        except TimeoutError as exc:
            if attempt == attempts:
                raise RuntimeError(f"{provider_label(provider)} timeout after {timeout}s") from exc
            time.sleep(min(30, 3 * attempt * attempt))
        except error.URLError as exc:
            if attempt == attempts:
                raise RuntimeError(f"{provider_label(provider)} network error: {exc}") from exc
            time.sleep(min(30, 3 * attempt * attempt))
        except Exception as exc:
            if attempt == attempts:
                raise RuntimeError(f"{provider_label(provider)} unexpected error: {exc}") from exc
            time.sleep(min(30, 3 * attempt * attempt))
    return (
        payload.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _require_text(value: str, message: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(message)
    return text


def _skill_text(writing_type: str) -> str:
    skill_name = "grant-writing" if writing_type == "grant" else "academic-writing"
    path = WRITING_SKILLS_DIR / skill_name / "SKILL.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _normalized_query_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _is_redundant_query_hint(query: Any, goal: str) -> bool:
    normalized_query = _normalized_query_text(query)
    normalized_goal = _normalized_query_text(goal)
    if not normalized_query or not normalized_goal:
        return False
    return normalized_query == normalized_goal or normalized_query in normalized_goal or normalized_goal in normalized_query


def _latex_escape(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _bibtex_escape(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
    return re.sub(r"\s+", " ", text)


def _normalized_year(value: Any) -> str:
    match = re.search(r"(19|20)\d{2}", str(value or ""))
    return match.group(0) if match else "n.d."


def _citation_author_token(author: str) -> str:
    cleaned = re.sub(r"[^A-Za-z\u4e00-\u9fff ]+", " ", str(author or "")).strip()
    if not cleaned:
        return "ref"
    if re.search(r"[\u4e00-\u9fff]", cleaned):
        compact = re.sub(r"\s+", "", cleaned)
        return compact[:4] or "ref"
    tokens = [token for token in cleaned.split() if token]
    return (tokens[-1] if tokens else cleaned).lower()[:16] or "ref"


def _citation_key_base(item: dict[str, Any]) -> str:
    authors = item.get("authors") or []
    first_author = str(authors[0] if authors else "")
    author_token = _citation_author_token(first_author)
    year = _normalized_year(item.get("year"))
    title_tokens = re.findall(r"[A-Za-z0-9]+", str(item.get("title") or ""))
    title_token = (title_tokens[0].lower() if title_tokens else "paper")[:12]
    base = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", f"{author_token}{year}{title_token}")
    return base or f"ref{year}"


def _ensure_unique_citation_key(base: str, used: set[str]) -> str:
    key = base
    suffix = 2
    while key in used:
        key = f"{base}{suffix}"
        suffix += 1
    used.add(key)
    return key


def _bibtex_entry_type(item: dict[str, Any]) -> str:
    venue = str(item.get("venue") or "").lower()
    if any(token in venue for token in ["conference", "proceedings", "symposium", "workshop"]):
        return "inproceedings"
    if venue:
        return "article"
    return "misc"


def _bibtex_entry(item: dict[str, Any]) -> str:
    key = str(item.get("citation_key") or "").strip()
    if not key:
        return ""
    entry_type = _bibtex_entry_type(item)
    authors = " and ".join(str(author).strip() for author in item.get("authors") or [] if str(author).strip())
    fields: list[tuple[str, str]] = []
    if authors:
        fields.append(("author", authors))
    title = _bibtex_escape(item.get("title"))
    if title:
        fields.append(("title", title))
    venue = _bibtex_escape(item.get("venue"))
    if venue:
        venue_field = "booktitle" if entry_type == "inproceedings" else "journal"
        fields.append((venue_field, venue))
    year = _normalized_year(item.get("year"))
    if year != "n.d.":
        fields.append(("year", year))
    doi = _bibtex_escape(item.get("doi"))
    if doi:
        fields.append(("doi", doi))
    url = _bibtex_escape(item.get("url"))
    if url:
        fields.append(("url", url))
    note = _bibtex_escape(item.get("source_name"))
    if note and entry_type == "misc":
        fields.append(("note", note))
    if not fields:
        return ""
    rows = [f"@{entry_type}{{{key},"]
    for name, value in fields:
        rows.append(f"  {name} = {{{value}}},")
    rows.append("}")
    return "\n".join(rows)


def _bibliography_bibtex(state: WorkflowState) -> str:
    entries = [str(item.get("bibtex") or "").strip() for item in state.evidence]
    entries = [entry for entry in entries if entry]
    return "\n\n".join(entries).strip() + ("\n" if entries else "")


def _default_bib_name() -> str:
    return "reference.bib"


def _is_report_like_template(template_id: str) -> bool:
    return template_id.startswith("hithesis-") and (
        template_id.endswith("-opening") or template_id.endswith("-midterm")
    )


def _template_section_outline(template_id: str, template_profile: dict[str, Any] | None = None) -> list[str]:
    if template_id.startswith("hithesis-") and template_id.endswith("-opening"):
        return [
            "课题来源及研究的目的和意义",
            "国内外在该方向的研究现状及分析",
            "主要研究内容",
            "研究方案",
            "进度安排，预期达到的目标",
            "课题已具备和所需的条件、经费",
            "研究过程中可能遇到的困难和问题，解决的措施",
            "主要参考文献",
        ]
    if template_profile:
        sec = template_profile.get("section_hierarchy") or {}
        if sec.get("top_level") == "chapter":
            # Use mainmatter-only chapter titles to avoid contamination from
            # appendix, back matter, or leftover sections/ files.
            chapter_titles = [str(t).strip() for t in (sec.get("mainmatter_chapter_titles") or []) if str(t).strip()]
            if chapter_titles:
                return chapter_titles
    return []


def _should_include_abstract(state: WorkflowState, sections: list[str]) -> bool:
    if _is_report_like_template(state.template_id):
        return False
    lowered = {str(section).strip().lower() for section in sections}
    return not lowered or "abstract" in lowered or "摘要" in lowered


def _workspace_root(state: WorkflowState) -> Path | None:
    workspace_path = str((state.workspace_index or {}).get("workspace_path") or "").strip()
    if not workspace_path:
        return None
    root = Path(workspace_path).expanduser()
    return root if root.exists() else None


def _workspace_entry_path(state: WorkflowState, rel_path: str) -> Path | None:
    root = _workspace_root(state)
    if not root or not rel_path:
        return None
    path = (root / rel_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path if path.exists() else None


def _workspace_python_summary(text: str) -> str:
    defs = re.findall(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.M)
    classes = re.findall(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.M)
    constants = re.findall(r"^([A-Z][A-Z0-9_]{2,})\s*=", text, flags=re.M)
    comments = [line.strip("# ").strip() for line in text.splitlines() if line.strip().startswith("#")]
    rows: list[str] = []
    if classes:
        rows.append("类: " + ", ".join(classes[:4]))
    if defs:
        rows.append("函数: " + ", ".join(defs[:8]))
    if constants:
        rows.append("关键配置: " + ", ".join(constants[:10]))
    if comments:
        rows.append("注释: " + _truncate("；".join(comments[:4]), 180))
    return " | ".join(rows)


def _workspace_file_summary(state: WorkflowState, entry: dict[str, Any]) -> str:
    rel_path = str(entry.get("path") or "")
    excerpt = str(entry.get("excerpt") or "")
    path = _workspace_entry_path(state, rel_path)
    text = ""
    if path and path.suffix.lower() in {".py", ".md", ".txt", ".tex", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"}:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:6000]
        except OSError:
            text = ""
    if rel_path.lower().endswith(".py"):
        summary = _workspace_python_summary(text)
        if summary:
            return f"{rel_path}: {summary}"
    return f"{rel_path}: {_truncate(text or excerpt, 280)}"


def _workspace_key_entries(state: WorkflowState) -> list[dict[str, Any]]:
    entries = list((state.workspace_index or {}).get("entries") or [])
    focus_names = ["label.py", "model.py", "train.py", "predict.py"]
    selected: list[dict[str, Any]] = []
    for name in focus_names:
        for item in entries:
            if str(item.get("path") or "").endswith(name):
                selected.append(item)
                break
    for item in entries:
        if len(selected) >= 6:
            break
        if item not in selected and str(item.get("is_text", False)).lower() != "false":
            selected.append(item)
    return selected[:6]


def _figure_priority(item: dict[str, Any]) -> tuple[int, str]:
    path = str(item.get("path") or "").lower()
    score = 0
    if any(token in path for token in ["cnn", "benchmark", "result", "reg", "compare", "analysis"]):
        score += 5
    if any(token in path for token in ["arch", "framework", "pipeline", "system", "overview"]):
        score += 4
    if "detailed" in path:
        score += 2
    return (-score, path)


def _mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return ""


def _fallback_figure_analysis(figure: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(figure.get("path") or "")
    lowered = rel_path.lower()
    section_label = "结果与分析"
    visual_type = "结果图"
    caption = f"工作区图像 {Path(rel_path).stem}"
    summary = "该图来自实验工作区，可作为方法说明或结果分析的配图。"
    claim = summary
    if any(token in lowered for token in ["arch", "framework", "pipeline", "system", "overview"]):
        section_label = "方法与实现"
        visual_type = "系统框架图"
        caption = "系统总体框架与感知决策流程"
        summary = "该图展示了系统从输入图像、特征工程、回归预测到决策生成的整体流程。"
        claim = "该图适合用于方法章节说明系统流程、模块关系与数据流向。"
    elif "detailed_reg_cnn" in lowered:
        visual_type = "预测-实测散点对比图"
        caption = "CNN 基线模型在 LAI、干重、鲜重任务上的预测-实测一致性"
        summary = "该图通常包含 LAI、干重、鲜重等目标的预测值与实测值散点、参考线和误差统计，用于评估 CNN 基线模型的回归性能。"
        claim = "该图可作为基线模型结果，用于与改进模型比较预测一致性与误差分布。"
    elif "detailed_reg_m1" in lowered or "detailed_reg_m2" in lowered or "detailed_reg_m3" in lowered:
        variant = Path(rel_path).stem.split("_")[-1].upper()
        visual_type = "预测-实测散点对比图"
        caption = f"{variant} 改进模型在 LAI、干重、鲜重任务上的预测-实测一致性"
        summary = f"该图通常展示 {variant} 改进模型在多个农业参数回归任务上的预测值与实测值散点分布，用于和基线模型比较拟合趋势与误差变化。"
        claim = f"该图适合用于说明 {variant} 改进模型相对基线模型在多目标农业参数回归上的趋势性提升。"
    elif any(token in lowered for token in ["cnn", "benchmark", "reg", "result", "compare", "analysis"]):
        section_label = "结果与分析"
        visual_type = "回归对比图"
        caption = "预测值与实测值的一致性对比结果"
        summary = "该图通常用于展示多个目标变量的预测值与实测值一致性以及误差指标。"
        claim = "该图适合用于结果分析章节概括不同模型或不同目标变量的预测一致性表现。"
    return {
        "path": rel_path,
        "latex_path": str(figure.get("latex_path") or ""),
        "section_label": section_label,
        "visual_type": visual_type,
        "caption": caption,
        "summary": summary,
        "claim": claim,
    }


def _vision_model_candidates(state: WorkflowState) -> list[str]:
    candidates = [
        str(os.environ.get("KIMI_VISION_MODEL") or "").strip(),
        str(state.runner_model or "").strip(),
    ]
    unique: list[str] = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    return unique


def _image_data_url_for_analysis(path: Path) -> str:
    mime = _mime_type_for_path(path)
    if not mime:
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if not raw:
        return ""
    if len(raw) <= 3_500_000:
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    try:
        from PIL import Image
    except Exception:
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
    try:
        image = Image.open(io.BytesIO(raw))
        image = image.convert("RGB")
        max_side = 2200
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return "data:image/jpeg;base64," + encoded
    except Exception:
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _analyze_workspace_figure(config: AppConfig, state: WorkflowState, figure: dict[str, Any]) -> dict[str, Any]:
    fallback = _fallback_figure_analysis(figure)
    if not state.api_key:
        return fallback
    path = _workspace_entry_path(state, str(figure.get("path") or ""))
    if not path:
        return fallback
    image_url = _image_data_url_for_analysis(path)
    if not image_url:
        return fallback
    prompt = """
请分析这张科研图像，返回 JSON object，不要 markdown。
字段：
- section_label: 只允许"方法与实现"或"结果与分析"
- visual_type: 图像类型
- caption: 适合论文/报告的图题
- summary: 2-3句中文摘要，必须说明图中展示的对象、指标或趋势
- claim: 1句可直接写入正文的结论性描述
""".strip()
    for model in _vision_model_candidates(state):
        try:
            raw_text = _chat_completion(
                state.api_key,
                state.model_provider,
                model,
                [
                    {"role": "system", "content": "你只输出 JSON object。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": image_url},
                        ],
                    },
                ],
                timeout=45,
                retries=0,
                max_tokens=500,
            )
            payload = _extract_json_object(raw_text)
            if payload:
                return {
                    **fallback,
                    "section_label": str(payload.get("section_label") or fallback["section_label"]),
                    "visual_type": str(payload.get("visual_type") or fallback["visual_type"]),
                    "caption": str(payload.get("caption") or fallback["caption"]),
                    "summary": str(payload.get("summary") or fallback["summary"]),
                    "claim": str(payload.get("claim") or payload.get("summary") or fallback["claim"]),
                }
        except Exception:
            continue
    return fallback


def _ensure_workspace_analysis(config: AppConfig, state: WorkflowState) -> dict[str, Any]:
    if state.workspace_analysis:
        return state.workspace_analysis
    entries = _workspace_key_entries(state)
    figures = sorted(list((state.workspace_index or {}).get("figures") or []), key=_figure_priority)[:4]
    entry_summaries = [
        {
            "path": str(item.get("path") or ""),
            "section_label": str(item.get("section") or ""),
            "summary": _workspace_file_summary(state, item),
        }
        for item in entries
    ]
    figure_summaries = [_analyze_workspace_figure(config, state, item) for item in figures]
    state.workspace_analysis = {
        "entries": entry_summaries,
        "figures": figure_summaries,
    }
    return state.workspace_analysis


def _workspace_prompt_context(config: AppConfig, state: WorkflowState) -> str:
    if not state.workspace_index:
        return "暂无导入代码工作区。"
    entries = _workspace_key_entries(state)
    figures = sorted(list((state.workspace_index or {}).get("figures") or []), key=_figure_priority)[:4]
    rows = [
        f"工作区: {state.workspace_index.get('workspace_name', '')}",
        f"路径: {state.workspace_index.get('workspace_path', '')}",
    ]
    for item in entries[:6]:
        rows.append(f"- [{item.get('section', '')}] {_workspace_file_summary(state, item)}")
    for item in figures[:4]:
        figure = _fallback_figure_analysis(item)
        rows.append(
            f"- 图像[{figure.get('section_label', '')}] {figure.get('path', '')} -> {figure.get('latex_path', '')} | "
            f"图题建议: {figure.get('caption', '')} | 解读: {figure.get('summary', '')}"
        )
    return "\n".join(rows)


def _section_focus(section: str) -> str:
    text = str(section or "")
    if any(token in text for token in ["研究方案", "研究内容", "技术路线", "方法", "实验设计", "模型"]):
        return "method"
    if any(token in text for token in ["进度", "目标", "条件", "经费", "基础", "可行性", "结果", "分析", "预实验"]):
        return "result"
    return "general"


def _workspace_figure_blocks(config: AppConfig, state: WorkflowState, section: str) -> list[dict[str, str]]:
    if not state.workspace_index:
        return []
    analysis = _ensure_workspace_analysis(config, state)
    focus = _section_focus(section)
    method_figures = [
        item
        for item in analysis.get("figures", [])
        if str(item.get("section_label") or "") == "方法与实现" and str(item.get("latex_path") or "").strip()
    ]
    result_figures = [
        item
        for item in analysis.get("figures", [])
        if str(item.get("section_label") or "") == "结果与分析" and str(item.get("latex_path") or "").strip()
    ]
    blocks: list[dict[str, str]] = []
    if method_figures and focus in {"method", "general"}:
        figure = method_figures[0]
        caption = _truncate(str(figure.get("caption") or figure.get("summary") or "工作区方法图"), 120)
        block = "\n".join(
            [
                r"\begin{figure}[htbp]",
                r"\centering",
                rf"\includegraphics[width=0.82\textwidth]{{{str(figure.get('latex_path') or '')}}}",
                rf"\caption{{{_latex_escape(caption)}}}",
                r"\label{fig:workspace_method}",
                r"\end{figure}",
            ]
        )
        blocks.append({"label": "fig:workspace_method", "block": block})
    if result_figures and focus in {"method", "result", "general"}:
        grid = result_figures[:4]
        block_rows = [r"\begin{figure}[htbp]", r"\centering"]
        for figure in grid:
            block_rows.append(rf"\includegraphics[width=0.48\textwidth]{{{str(figure.get('latex_path') or '')}}}")
        caption_parts = []
        for figure in grid:
            name = Path(str(figure.get("path") or "")).stem
            claim = str(figure.get("claim") or figure.get("summary") or "").strip()
            if claim:
                caption_parts.append(f"{name}: {claim}")
        caption = "工作区预实验结果图。"
        if caption_parts:
            caption += _truncate("；".join(caption_parts), 180)
        block_rows.extend(
            [
                rf"\caption{{{_latex_escape(caption)}}}",
                r"\label{fig:workspace_results}",
                r"\end{figure}",
            ]
        )
        blocks.append({"label": "fig:workspace_results", "block": "\n".join(block_rows)})
    return blocks


def _section_workspace_context(config: AppConfig, state: WorkflowState, section: str) -> str:
    if not state.workspace_index:
        return "暂无导入代码工作区。"
    analysis = _ensure_workspace_analysis(config, state)
    focus = _section_focus(section)
    entries = list(analysis.get("entries", []))
    figures = list(analysis.get("figures", []))
    if focus == "method":
        entries = sorted(
            entries,
            key=lambda item: (0 if str(item.get("section_label") or "") == "方法与实现" else 1, str(item.get("path") or "")),
        )
        figures = sorted(
            figures,
            key=lambda item: (0 if str(item.get("section_label") or "") == "方法与实现" else 1, str(item.get("path") or "")),
        )
    elif focus == "result":
        entries = sorted(
            entries,
            key=lambda item: (0 if str(item.get("section_label") or "") == "实验设计" else 1, str(item.get("path") or "")),
        )
        figures = sorted(
            figures,
            key=lambda item: (0 if str(item.get("section_label") or "") == "结果与分析" else 1, str(item.get("path") or "")),
        )
    rows = [
        f"工作区: {state.workspace_index.get('workspace_name', '')}",
        f"路径: {state.workspace_index.get('workspace_path', '')}",
        "优先代码与配置：",
    ]
    for item in entries[:5]:
        rows.append(f"- [{item.get('section_label', '')}] {item.get('summary', '')}")
    if figures:
        rows.append("优先图像与图意：")
    for item in figures[:4]:
        rows.append(
            f"- 图像[{item.get('section_label', '')}] {item.get('path', '')} -> {item.get('latex_path', '')} | "
            f"图题: {item.get('caption', '')} | 图意: {item.get('summary', '')} | 可写结论: {item.get('claim', '')}"
        )
    blocks = _workspace_figure_blocks(config, state, section)
    if blocks:
        rows.append("如需插图，只能原样复用以下 LaTeX 图块并使用给定 label：")
        for item in blocks:
            rows.append(item["block"])
    return "\n".join(rows)


def _keywords_from_goal(goal: str) -> str:
    mapped: list[str] = []
    mapping = {
        "遥感": "remote sensing",
        "智慧农业": "smart agriculture precision agriculture crop monitoring",
        "多模态": "multimodal vision language",
        "视觉语言": "vision language",
        "基金": "NSFC grant research proposal",
        "作物": "crop",
        "综述": "survey review",
        "脑机": "brain computer interface brain machine interface",
        "神经接口": "neural interface intracortical electrocorticography",
        "类脑": "brain inspired neural interface neurotechnology",
        "深脑刺激": "deep brain stimulation adaptive deep brain stimulation",
        "皮层": "cortical electrocorticography posterior parietal cortex",
    }
    lowered = goal.lower()
    if "vlm" in lowered:
        mapped.append("vision language model")
    if "llm" in lowered:
        mapped.append("large language model")
    for key, value in mapping.items():
        if key in goal:
            mapped.append(value)
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}", goal)
    stop = {
        "基于",
        "本地",
        "撰写",
        "一份",
        "草稿",
        "论文",
        "综述",
        "基金",
        "申请",
    }
    kept = []
    for token in tokens:
        if token in stop:
            continue
        if re.search(r"[\u4e00-\u9fff]", token) and len(token) > 8:
            continue
        kept.append(token)
    if mapped or kept:
        return " ".join((mapped + kept)[:32])
    return goal[:220]


def planner_node(config: AppConfig, state: WorkflowState) -> WorkflowState:
    model = state.planner_model or config.planner_model
    state.api_key = _load_api_key(state.model_provider, state.api_key)
    skill = _skill_text(state.writing_type)
    workspace_context = _truncate(_workspace_prompt_context(config, state), 2200)
    source_material_context = _truncate(_source_material_context(state, limit=6), 2200)
    if not state.template_profile and state.run_id:
        try:
            state.template_profile = build_template_profile(state.run_id, template_id=state.template_id)
        except Exception:
            pass
    template_sections = _template_section_outline(state.template_id, state.template_profile)
    state.writing_language = _normalize_writing_language(state.writing_language or _template_language(state.template_id) or ("zh" if state.writing_type == "grant" else "en"))
    state.writing_profile = _resolve_writing_profile(state)
    state.bibliography_profile = _normalize_bibliography_profile(state.bibliography_profile)
    bibliography_hint = _bibliography_command_hint(state.bibliography_profile)
    profile = state.writing_profile
    default_sections = template_sections or list(profile.get("default_sections") or [])
    prompt = f"""
你是科研工作流 planner。请把用户目标拆成可执行计划，输出 JSON object，不要 markdown。

用户目标：
{state.goal}

写作类型：{state.writing_type}
目标语言：{_language_name(state.writing_language)}
写作画像：{profile.get("label", "")}
执行模式：{profile.get("execution_mode", "")}
当前模板：{state.template_id or "未指定"}
是否需要先爬文献：{state.use_literature_pipeline}
用户附加要求：{state.requirements or "无"}
参考文献系统：{bibliography_hint}

代码工作区摘要：
{workspace_context}

用户补充材料摘要：
{source_material_context}

模板结构分析：
{_template_structure_hint(state.template_id, state.template_profile)}

模板优先章节：
{json.dumps(default_sections, ensure_ascii=False) if default_sections else "无，允许你自行规划"}

写作画像要求：
- {profile.get("planner_hint", "")}
- {profile.get("material_policy", "")}
- {profile.get("results_policy", "")}
- {profile.get("citation_policy", "")}

可用技能说明：
{skill}

JSON 字段：
- query: 你根据用户目标自动生成的本地文献库 RAG 检索词，必须具体、覆盖核心主题，不要要求用户手动提供
- writing_type: academic 或 grant
- writing_language: en 或 zh
- execution_mode: literature_first 或 results_first
- title: 文稿标题
- sections: 完整章节标题数组。若已给出模板优先章节，必须严格保持该顺序；否则优先沿用写作画像默认章节
- needs_literature_pipeline: boolean
- evidence_questions: 需要 RAG 回答的问题数组
- runner_instructions: 给 runner 的写作要求
- evidence_policy: 文献使用策略，必须说明使用真实文献引用和标准参考文献格式，不默认限制证据数
""".strip()
    raw = _chat_completion(
        state.api_key,
        state.model_provider,
        model,
        [
            {"role": "system", "content": "你只输出 JSON object。"},
            {"role": "user", "content": prompt},
        ],
    )
    plan = _extract_json_object(raw)
    if not plan:
        raise RuntimeError("planner returned invalid JSON")
    if template_sections:
        plan["sections"] = template_sections
        instructions = str(plan.get("runner_instructions") or "").strip()
        extra = "必须严格遵守模板章节标题与顺序；工作区代码与结果图优先用于方法、实验与结果分析。"
        plan["runner_instructions"] = f"{instructions}\n{extra}".strip()
    elif not isinstance(plan.get("sections"), list) or not plan.get("sections"):
        plan["sections"] = default_sections
    plan["execution_mode"] = str(plan.get("execution_mode") or profile.get("execution_mode") or "literature_first")
    plan["writing_language"] = _normalize_writing_language(plan.get("writing_language") or state.writing_language)
    plan["writing_type"] = str(plan.get("writing_type") or state.writing_type or "academic")
    state.writing_language = str(plan.get("writing_language") or state.writing_language)
    state.writing_profile = {
        **profile,
        "execution_mode": plan["execution_mode"],
        "language": state.writing_language,
    }
    plan["section_briefs"] = [
        _section_brief(state, str(section))
        for section in plan.get("sections") or []
        if str(section).strip()
    ]
    state.plan = plan
    hint_query = _split_plan_query(state.query)
    planned_query = _split_plan_query(state.plan.get("query") or "")
    if hint_query and hint_query != planned_query and not _is_redundant_query_hint(hint_query, state.goal):
        merged_parts = [part for part in [planned_query, hint_query] if part]
        state.plan["query"] = " ".join(dict.fromkeys(merged_parts))
    state.query = _require_text(
        _split_plan_query(state.plan.get("query") or state.goal),
        "planner did not return a valid retrieval query",
    )
    state.messages.append("planner completed")
    return state


def literature_node(config: AppConfig, state: WorkflowState) -> WorkflowState:
    if not bool(state.plan.get("needs_literature_pipeline", state.use_literature_pipeline)):
        state.messages.append("literature crawl skipped")
        return state
    payload = {
        "query": state.query,
        "include_search": True,
        "max_results": state.max_literature_results,
        "summarize_limit": state.summarize_limit,
        "force_refresh": False,
        "use_ai": bool(state.api_key),
        "api_key": state.api_key,
        "model": state.runner_model or config.runner_model,
        "job_id": f"{state.run_id}-literature",
    }
    state.literature_result = run_attention_pipeline(config, payload)
    state.messages.append("literature pipeline completed")
    return state


def rag_node(config: AppConfig, state: WorkflowState) -> WorkflowState:
    query = state.query or state.goal
    limit = state.rag_limit if state.rag_limit > 0 else int(os.environ.get("RAG_DEFAULT_LIMIT", "48"))
    results = search_library(
        config, query, limit=limit,
        api_key=state.api_key or "",
        model_provider=state.model_provider or "ds",
        model=state.runner_model or config.runner_model,
    )
    if state.exclude_preprints:
        filtered = []
        for item in results:
            venue = str(item.get("venue") or "").strip().lower()
            page_url = str(item.get("page_url") or "").strip().lower()
            source_name = str(item.get("source_name") or "").strip().lower()
            source_domain = str(item.get("source_domain") or "").strip().lower()
            if "arxiv" in venue or "arxiv" in page_url or "arxiv" in source_name or "arxiv" in source_domain:
                continue
            if not str(item.get("doi") or "").strip() and "doi.org" not in page_url and "pmc" not in page_url and "pubmed" not in page_url:
                continue
            filtered.append(item)
        results = filtered
    results = _rerank_and_filter_results(state, results)
    if not results:
        state.messages.append(f"RAG: no local evidence found for query '{query[:120]}', continuing without literature")
        return state
    keep_limit = state.rag_limit if state.rag_limit > 0 else int(os.environ.get("RAG_RETAIN_LIMIT", "16"))
    results = results[: max(keep_limit, 1)]
    used_keys: set[str] = set()
    evidence: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        citation_key = _ensure_unique_citation_key(_citation_key_base(item), used_keys)
        evidence_item = {
            "key": citation_key,
            "legacy_key": f"P{index}",
            "citation_key": citation_key,
            "title": item.get("title", ""),
            "year": item.get("year", ""),
            "venue": item.get("venue", ""),
            "authors": item.get("authors", []),
            "url": item.get("page_url", ""),
            "doi": item.get("doi", ""),
            "source_name": item.get("source_name", ""),
            "score": item.get("score"),
            "summary": (item.get("summary") or {}).get("summary", ""),
            "why_it_matters": (item.get("summary") or {}).get("why_it_matters", ""),
            "abstract": item.get("abstract", ""),
        }
        evidence_item["bibtex"] = _bibtex_entry(evidence_item)
        evidence.append(evidence_item)
    state.evidence = evidence
    state.evidence_memory = _build_evidence_memory(state)
    state.messages.append(f"rag retrieved {len(state.evidence)} items")
    state.messages.append(f"evidence memory built {len(state.evidence_memory.get('cards', []))} cards")
    return state


def surveyor_node(_config: AppConfig, state: WorkflowState) -> WorkflowState:
    profile = _ensure_writing_profile(state)
    records = _survey_records_for_workflow(state)
    report = build_survey_report(
        records,
        {
            "query": state.query or state.goal,
            "language": state.writing_language,
            "report_kind": "workflow_survey",
            "days": 30,
            "top_n": 5,
            "card_limit": 3,
            "title": f"{profile.get('label', '')} Survey Packet".strip() or "",
        },
    )
    state.survey_report = report
    state.plan["multi_agent"] = _multi_agent_blueprint(state)
    state.agent_outputs["surveyor"] = {
        "title": report.get("title", ""),
        "record_count": len(records),
        "stats": report.get("stats", {}),
        "preview": str(report.get("markdown") or "")[:2200],
    }
    state.messages.append("surveyor completed")
    return state


def architect_node(_config: AppConfig, state: WorkflowState) -> WorkflowState:
    profile = _ensure_writing_profile(state)
    section_briefs = []
    for section in state.plan.get("sections") or []:
        brief = _section_brief(state, str(section))
        brief["survey_hints"] = _architect_hints_from_report(state.survey_report, brief)
        section_briefs.append(brief)
    state.plan["section_briefs"] = section_briefs
    state.plan["architect_notes"] = {
        "profile": profile.get("label", ""),
        "execution_mode": profile.get("execution_mode", ""),
        "survey_focus": {
            "methods": [str(item.get("name") or "") for item in ((state.survey_report.get("stats") or {}).get("top_methods") or [])[:6]],
            "datasets": [str(item.get("name") or "") for item in ((state.survey_report.get("stats") or {}).get("top_datasets") or [])[:6]],
            "limitations": [str(item.get("name") or "") for item in ((state.survey_report.get("stats") or {}).get("top_limitations") or [])[:6]],
        },
    }
    instructions = str(state.plan.get("runner_instructions") or "").strip()
    survey_text = str(state.survey_report.get("markdown") or "")[:1600]
    if survey_text:
        state.plan["runner_instructions"] = (
            f"{instructions}\n"
            "写作前必须先吸收 Surveyor 的调研结论，把 related work、baseline、dataset、limitation 融入章节职责。\n"
            f"Surveyor 摘要:\n{survey_text}"
        ).strip()
    state.agent_outputs["architect"] = {
        "section_count": len(section_briefs),
        "notes": state.plan.get("architect_notes", {}),
    }
    state.messages.append("architect completed")
    return state


def _review_generated_draft(state: WorkflowState) -> dict[str, Any]:
    latex = str(state.latex or "")
    sections = [str(item) for item in state.plan.get("sections") or [] if str(item).strip()]
    warnings: list[str] = []
    if not latex.strip():
        warnings.append("empty_latex")
    if any(token in latex for token in ["TODO", "待扩展", "This section should be expanded"]):
        warnings.append("placeholder_text")
    if state.writing_type == "academic" and "\\cite{" not in latex:
        warnings.append("missing_citations")
    for section in sections:
        lowered = section.lower()
        if lowered in {"references", "abstract"} or "参考" in section or section == "摘要":
            continue
        if section not in latex:
            warnings.append(f"missing_section:{section}")
    return {
        "status": "needs_attention" if warnings else "pass",
        "warnings": warnings,
        "section_count": len(sections),
        "citation_count": latex.count("\\cite{"),
        "char_count": len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", latex)),
    }


def reviewer_node(_config: AppConfig, state: WorkflowState) -> WorkflowState:
    state.review_report = _review_generated_draft(state)
    state.agent_outputs["reviewer"] = dict(state.review_report)
    state.messages.append("reviewer completed")
    return state


def _evidence_markdown(evidence: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in evidence:
        lines.append(
            "\n".join(
                [
                    f"[{item.get('citation_key', item['key'])}] {item.get('title', '')}",
                    f"Year/Venue: {item.get('year', '')} / {item.get('venue', '')}",
                    f"Authors: {', '.join(item.get('authors') or [])}",
                    f"Citation key: {item.get('citation_key', '')}",
                    f"URL: {item.get('url', '')}",
                    f"Summary: {item.get('summary') or item.get('abstract', '')}",
                    f"Why it matters: {item.get('why_it_matters', '')}",
                ]
            )
        )
    return "\n\n".join(lines)


def _compact_evidence_markdown(evidence: list[dict[str, Any]], limit: int = 8) -> str:
    return _evidence_markdown(evidence[:limit])


DOMAIN_TERMS = [
    "large language model",
    "language model",
    "llm",
    "rag",
    "retrieval augmented generation",
    "agent",
    "knowledge graph",
    "ontology",
    "smart agriculture",
    "precision agriculture",
    "crop model",
    "crop growth model",
    "process-based model",
    "mechanistic model",
    "hybrid model",
    "digital twin",
    "data assimilation",
    "remote sensing",
    "multimodal",
    "foundation model",
    "reinforcement learning",
    "dssat",
    "wofost",
    "apsim",
    "yield prediction",
    "crop management",
    "irrigation",
    "nitrogen",
    "greenhouse",
    "phenotyping",
    "time series",
    "uncertainty",
    "simulation",
    "智慧农业",
    "精准农业",
    "大语言模型",
    "机理模型",
    "过程模型",
    "作物模型",
    "作物生长模型",
    "数字孪生",
    "知识图谱",
    "数据同化",
    "遥感",
    "多模态",
    "基础模型",
    "强化学习",
    "产量预测",
    "作物管理",
    "灌溉",
    "氮肥",
    "温室",
    "表型",
    "不确定性",
]

SECTION_TERMS = {
    "摘要": ["研究问题", "目标", "技术路线", "创新", "成果", "summary"],
    "立项": ["意义", "需求", "粮食安全", "智慧农业", "gap", "challenge"],
    "现状": ["survey", "review", "研究现状", "发展趋势", "foundation model"],
    "科学问题": ["challenge", "limitation", "hallucination", "uncertainty", "domain gap"],
    "目标": ["objective", "benchmark", "evaluation", "prediction", "decision"],
    "研究内容": ["method", "framework", "model", "system", "data assimilation", "hybrid"],
    "技术路线": ["framework", "pipeline", "architecture", "digital twin", "simulation"],
    "创新": ["novel", "first", "hybrid", "agent", "mechanistic", "reflection"],
    "年度": ["plan", "milestone", "benchmark", "validation", "field"],
    "成果": ["output", "software", "dataset", "benchmark", "decision support"],
    "基础": ["evidence", "experiment", "dataset", "model", "validation"],
    "风险": ["risk", "uncertainty", "hallucination", "domain gap", "noise"],
    "参考": ["reference", "evidence", "literature"],
}

STOPWORDS = {
    "with",
    "from",
    "using",
    "into",
    "this",
    "that",
    "have",
    "has",
    "are",
    "was",
    "were",
    "for",
    "and",
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "to",
    "by",
    "as",
    "is",
    "be",
    "or",
    "we",
    "our",
    "their",
    "study",
    "paper",
    "model",
    "models",
    "method",
    "methods",
}


def _truncate(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _split_plan_query(query: Any) -> str:
    if isinstance(query, list):
        return " ".join(str(item) for item in query)
    return str(query or "")


def _extract_keywords(text: str, limit: int = 12) -> list[str]:
    lower = text.lower()
    keywords: list[str] = []
    for term in DOMAIN_TERMS:
        probe = term.lower()
        if (probe in lower or term in text) and term not in keywords:
            keywords.append(term)
    counts: dict[str, int] = {}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", lower):
        if token in STOPWORDS:
            continue
        counts[token] = counts.get(token, 0) + 1
    for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords[:limit]


def _goal_domain(goal: str, query: str) -> str:
    merged = f"{goal} {query}".lower()
    brain_terms = [
        "脑机",
        "神经接口",
        "类脑",
        "深脑刺激",
        "皮层",
        "brain computer interface",
        "brain machine interface",
        "neural interface",
        "intracortical",
        "electrocorticography",
        "deep brain stimulation",
        "posterior parietal cortex",
        "neurotechnology",
    ]
    agriculture_terms = [
        "农业",
        "作物",
        "遥感",
        "智慧农业",
        "smart agriculture",
        "crop",
        "remote sensing",
    ]
    if any(term in merged for term in brain_terms):
        return "brain_interface"
    if any(term in merged for term in agriculture_terms):
        return "agriculture"
    return "generic"


def _relevance_terms(state: WorkflowState) -> dict[str, list[str]]:
    plan_text = " ".join(
        [
            state.goal,
            state.query,
            _split_plan_query(state.plan.get("query", "")),
            " ".join(str(item) for item in state.plan.get("sections", []) or []),
            state.requirements,
        ]
    )
    general = _extract_keywords(plan_text, limit=24)
    domain = _goal_domain(state.goal, state.query)
    if domain == "brain_interface":
        required = [
            "brain computer interface",
            "brain machine interface",
            "neural interface",
            "intracortical",
            "electrocorticography",
            "deep brain stimulation",
            "cortical",
            "neural",
            "brain",
            "prostheses",
            "stimulation",
            "posterior parietal cortex",
            "hydrogel",
            "microelectrode",
        ]
        blocked = [
            "remote sensing",
            "smart agriculture",
            "crop",
            "yield prediction",
            "greenhouse",
            "digital twin",
            "phenotyping",
            "satellite",
            "遥感",
            "农业",
            "作物",
            "温室",
            "表型",
        ]
    elif domain == "agriculture":
        required = [
            "smart agriculture",
            "crop",
            "remote sensing",
            "phenotyping",
            "yield",
        ]
        blocked = [
            "brain computer interface",
            "brain machine interface",
            "neural interface",
            "intracortical",
            "electrocorticography",
            "deep brain stimulation",
        ]
    else:
        required = general[:10]
        blocked = []
    return {
        "domain": [domain],
        "general": general,
        "required": required,
        "blocked": blocked,
    }


def _paper_relevance_score(state: WorkflowState, item: dict[str, Any]) -> tuple[float, int, int]:
    terms = _relevance_terms(state)
    haystack = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("venue", "")),
            str(item.get("abstract", "")),
            str((item.get("summary") or {}).get("summary", "")),
            str((item.get("summary") or {}).get("why_it_matters", "")),
            " ".join(str(author) for author in item.get("authors", []) or []),
            " ".join(str(keyword) for keyword in item.get("keywords", []) or []),
        ]
    ).lower()
    required_hits = sum(1 for term in terms["required"] if term and term.lower() in haystack)
    general_hits = sum(1 for term in terms["general"] if term and term.lower() in haystack)
    blocked_hits = sum(1 for term in terms["blocked"] if term and term.lower() in haystack)
    base_score = float(item.get("score") or 0)
    rank_bonus = 0.0
    rank = item.get("rank")
    if isinstance(rank, (int, float)):
        rank_bonus = max(0.0, 25.0 - abs(float(rank)))
    total = base_score + rank_bonus + required_hits * 20 + general_hits * 4 - blocked_hits * 30
    if str(item.get("doi") or "").strip():
        total += 6
    if str(item.get("venue") or "").strip():
        total += 3
    return total, required_hits, blocked_hits


def _brain_interface_anchor_hits(item: dict[str, Any]) -> tuple[int, int]:
    haystack = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("venue", "")),
            str(item.get("abstract", "")),
            str((item.get("summary") or {}).get("summary", "")),
            str((item.get("summary") or {}).get("why_it_matters", "")),
            " ".join(str(keyword) for keyword in item.get("keywords", []) or []),
        ]
    ).lower()
    strong_anchors = [
        "brain computer interface",
        "brain-computer interface",
        "brain machine interface",
        "brain-machine interface",
        "neural interface",
        "intracortical",
        "micro-electrocorticography",
        "microelectrocorticography",
        "electrocorticography",
        "deep brain stimulation",
        "posterior parietal cortex",
        "cortical visual prosthe",
        "neural probe",
        "recording interface",
        "bioelectronic recording",
        "microelectrode",
        "hydrogel interface",
    ]
    soft_anchors = [
        "brain",
        "cortical",
        "stimulation",
        "electrode",
        "recording",
        "prosthe",
        "probe",
        "hydrogel",
        "neurotech",
    ]
    strong_hits = sum(1 for term in strong_anchors if term in haystack)
    soft_hits = sum(1 for term in soft_anchors if term in haystack)
    return strong_hits, soft_hits


def _is_relevant_paper(state: WorkflowState, item: dict[str, Any], required_hits: int, blocked_hits: int) -> bool:
    domain = _goal_domain(state.goal, state.query)
    if blocked_hits >= 2:
        return False
    if domain == "brain_interface":
        strong_hits, soft_hits = _brain_interface_anchor_hits(item)
        return strong_hits >= 1 or (strong_hits == 0 and soft_hits >= 2 and required_hits >= 2)
    if domain == "agriculture":
        return required_hits >= 2
    return required_hits >= 1 or blocked_hits == 0


def _rerank_and_filter_results(state: WorkflowState, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[float, int, int, dict[str, Any]]] = []
    for item in results:
        total, required_hits, blocked_hits = _paper_relevance_score(state, item)
        if not _is_relevant_paper(state, item, required_hits, blocked_hits):
            continue
        scored.append((total, required_hits, blocked_hits, item))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2], str(row[3].get("title", "")).lower()))
    return [item for _total, _required, _blocked, item in scored]


def _evidence_claim(item: dict[str, Any]) -> str:
    text = item.get("summary") or item.get("why_it_matters") or item.get("abstract") or ""
    sentences = re.split(r"(?<=[。！？.!?])\s+", str(text).strip())
    claim = " ".join(sentence for sentence in sentences[:2] if sentence).strip()
    return _truncate(claim or item.get("title", ""), 520)


def _evidence_facets(item: dict[str, Any]) -> list[str]:
    haystack = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("why_it_matters", "")),
            str(item.get("abstract", "")),
            str(item.get("venue", "")),
            " ".join(str(author) for author in item.get("authors", []) or []),
        ]
    ).lower()
    facets: list[str] = []
    probes = {
        "review": ["review", "survey", "综述", "现状"],
        "method": ["method", "framework", "architecture", "pipeline", "算法", "方法", "模型"],
        "baseline": ["baseline", "comparison", "benchmark", "sota", "对比", "基线"],
        "dataset": ["dataset", "corpus", "benchmark", "data set", "数据集"],
        "metric": ["accuracy", "f1", "auc", "rmse", "mae", "metric", "指标", "评价"],
        "result": ["result", "analysis", "performance", "improve", "结果", "性能", "提升"],
        "problem": ["challenge", "problem", "limitation", "gap", "问题", "挑战", "局限"],
        "foundation": ["classic", "foundational", "origin", "early", "首次", "奠基"],
        "application": ["application", "deployment", "decision", "应用", "决策"],
        "risk": ["uncertainty", "robust", "risk", "hallucination", "偏差", "风险", "不确定性"],
    }
    for facet, terms in probes.items():
        if any(term in haystack for term in terms):
            facets.append(facet)
    if not facets:
        facets.append("general")
    return facets


def _build_evidence_memory(state: WorkflowState) -> dict[str, Any]:
    profile = _ensure_writing_profile(state)
    plan_text = " ".join(
        [
            state.goal,
            str(state.plan.get("title", "")),
            _split_plan_query(state.plan.get("query", state.query)),
            " ".join(str(item) for item in state.plan.get("evidence_questions", []) or []),
        ]
    )
    global_keywords = _extract_keywords(plan_text, limit=20)
    cards: list[dict[str, Any]] = []
    for item in state.evidence:
        source_text = " ".join(
            [
                str(item.get("title", "")),
                str(item.get("summary", "")),
                str(item.get("why_it_matters", "")),
                str(item.get("abstract", "")),
            ]
        )
        keywords = _extract_keywords(source_text, limit=14)
        overlap = len(set(keyword.lower() for keyword in keywords) & set(keyword.lower() for keyword in global_keywords))
        score = float(item.get("score") or 0)
        priority = score + overlap * 8 + min(len(keywords), 10)
        cards.append(
            {
                "key": item.get("key", ""),
                "citation_key": item.get("citation_key", item.get("key", "")),
                "title": item.get("title", ""),
                "year": item.get("year", ""),
                "venue": item.get("venue", ""),
                "keywords": keywords,
                "facets": _evidence_facets(item),
                "priority": round(priority, 2),
                "claim": _evidence_claim(item),
                "why_it_matters": str(item.get("why_it_matters", "")),
                "url": item.get("url", ""),
            }
        )
    cards.sort(key=lambda card: (-float(card.get("priority") or 0), str(card.get("key", ""))))
    section_index: dict[str, list[str]] = {}
    for brief in state.plan.get("section_briefs", []) or []:
        if not isinstance(brief, dict):
            continue
        section = str(brief.get("section") or "").strip()
        if not section:
            continue
        weights = brief.get("facet_weights") if isinstance(brief.get("facet_weights"), dict) else {}
        ranking: list[tuple[float, str]] = []
        section_terms = {term.lower() for term in _section_query_terms(section, state)}
        for card in cards:
            facets = [str(item) for item in card.get("facets", []) or []]
            facet_score = sum(int(weights.get(facet) or 0) for facet in facets)
            keyword_score = sum(1 for term in section_terms if term and term in " ".join(str(item) for item in card.get("keywords", [])).lower()) * 5
            ranking.append((float(card.get("priority") or 0) + facet_score + keyword_score, str(card.get("key") or "")))
        ranking.sort(key=lambda item: (-item[0], item[1]))
        section_index[section] = [key for _score, key in ranking[:12]]
    return {
        "global_keywords": global_keywords,
        "cards": cards,
        "section_index": section_index,
        "source_cards": profile.get("source_cards", []),
        "writing_profile": {
            "profile_id": profile.get("profile_id", ""),
            "execution_mode": profile.get("execution_mode", ""),
            "language": profile.get("language", ""),
        },
        "policy": "每章只传入与章节最相关的高优先级文献卡片；正文统一使用真实 \\cite{key} 引用，参考文献使用标准 BibTeX；若存在用户材料或工作区结果，则方法与实验相关章节优先以真实材料为主、文献为辅。",
    }


def _section_query_terms(section: str, state: WorkflowState) -> list[str]:
    terms = _extract_keywords(section + " " + state.goal, limit=14)
    for key, values in SECTION_TERMS.items():
        if key in section:
            for value in values:
                if value not in terms:
                    terms.append(value)
    return terms[:22]


def _select_section_evidence(
    state: WorkflowState,
    section: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    memory = state.evidence_memory or _build_evidence_memory(state)
    cards = memory.get("cards", [])
    section_index = memory.get("section_index", {}) if isinstance(memory, dict) else {}
    terms = [term.lower() for term in _section_query_terms(section, state)]
    brief = _section_brief(state, section)
    facet_weights = brief.get("facet_weights") if isinstance(brief.get("facet_weights"), dict) else {}
    preferred_keys = [str(item) for item in (section_index.get(section, []) if isinstance(section_index, dict) else [])]
    selected: list[tuple[float, dict[str, Any]]] = []
    for card in cards:
        haystack = " ".join(
            [
                str(card.get("title", "")),
                " ".join(str(item) for item in card.get("keywords", []) or []),
                str(card.get("claim", "")),
            ]
        ).lower()
        match_score = sum(1 for term in terms if term and term in haystack)
        priority = float(card.get("priority") or 0)
        facets = [str(item) for item in card.get("facets", []) or []]
        facet_score = sum(int(facet_weights.get(facet) or 0) for facet in facets)
        key_bonus = 30 if str(card.get("key") or "") in preferred_keys[:8] else 0
        selected.append((priority + match_score * 15 + facet_score + key_bonus, card))
    selected.sort(key=lambda item: (-item[0], str(item[1].get("key", ""))))
    max_cards = limit or int(os.environ.get("RAG_SECTION_EVIDENCE_LIMIT", "8"))
    return [card for _score, card in selected[:max_cards]]


def _format_evidence_cards(cards: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for card in cards:
        lines.append(
            "\n".join(
                [
                    f"[{card.get('key')}] {card.get('title', '')}",
                    f"Year/Venue: {card.get('year', '')} / {card.get('venue', '')}",
                    f"Citation key: {card.get('citation_key', card.get('key', ''))}",
                    f"Keywords: {', '.join(card.get('keywords') or [])}",
                    f"Facets: {', '.join(card.get('facets') or [])}",
                    f"Priority: {card.get('priority', '')}",
                    f"Core claim: {card.get('claim', '')}",
                    f"Why it matters: {card.get('why_it_matters', '')}",
                ]
            )
        )
    return "\n\n".join(lines)


def _plan_brief(state: WorkflowState) -> str:
    sections = state.plan.get("sections") or []
    profile = _ensure_writing_profile(state)
    return "\n".join(
        [
            f"题目：{state.plan.get('title') or state.goal}",
            f"检索主题：{_split_plan_query(state.plan.get('query', state.query))}",
            f"执行模式：{state.plan.get('execution_mode') or profile.get('execution_mode', '')}",
            f"写作画像：{profile.get('label', '')}",
            f"章节：{'；'.join(str(section) for section in sections[:16])}",
            f"写作要求：{_truncate(state.plan.get('runner_instructions', ''), 900)}",
        ]
    )


def _history_memory_text(state: WorkflowState) -> str:
    if not state.section_memories:
        return "暂无已完成章节。"
    lines = []
    for item in state.section_memories[-6:]:
        evidence_keys = ", ".join(item.get("evidence_keys") or [])
        lines.append(
            f"- {item.get('section')}: {item.get('memory')} 已用证据：{evidence_keys}"
        )
    return "\n".join(lines)


def _section_memory_from_fragment(section: str, fragment: str, evidence_keys: list[str]) -> dict[str, Any]:
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", fragment)
    text = re.sub(r"\s+", " ", text).strip()
    return {
        "section": section,
        "memory": _truncate(text, 420),
        "evidence_keys": evidence_keys,
    }
def _wrap_latex_document(title: str, body: str, writing_language: str = "en") -> str:
    if _normalize_writing_language(writing_language) == "zh":
        return rf"""
\documentclass[11pt]{{ctexart}}
\usepackage[margin=1in]{{geometry}}
\usepackage{{hyperref}}
\usepackage{{enumitem}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{xcolor}}
\title{{{title}}}
\author{{Scientific Agent}}
\date{{\today}}
\begin{{document}}
\maketitle
{body}
\end{{document}}
""".strip()
    return rf"""
\documentclass[11pt]{{article}}
\usepackage[margin=1in]{{geometry}}
\usepackage{{hyperref}}
\usepackage{{enumitem}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{xcolor}}
\title{{{title}}}
\author{{Scientific Agent}}
\date{{\today}}
\begin{{document}}
\maketitle
{body}
\end{{document}}
""".strip()


def _wrap_latex_with_template(state: WorkflowState, body: str) -> str:
    title = state.plan.get("title") or state.goal or "Research Report"
    starter = render_template_starter(state.template_id, title=title, author="Scientific Agent")
    if r"\begin{document}" not in starter or r"\end{document}" not in starter:
        return _wrap_latex_document(_latex_escape(title), body, state.writing_language)
    prefix, remainder = starter.split(r"\begin{document}", 1)
    if r"\end{document}" not in remainder:
        return _wrap_latex_document(_latex_escape(title), body, state.writing_language)
    _middle, suffix = remainder.rsplit(r"\end{document}", 1)
    # Preserve front matter (cover, makecover, front/ inputs) from the template
    # by extracting everything before the first body-level \input or \section
    front_cut = re.search(
        r"\\(?:input|include)\{[^}]*body/[^}]*\}|"
        r"\\(?:section|chapter|part)\b",
        _middle,
    )
    if front_cut:
        front_matter = _middle[:front_cut.start()].strip()
    else:
        front_matter = ""
    assembled_body = (front_matter + "\n\n" + body.strip()).strip() if front_matter else body.strip()
    document = prefix.strip() + "\n\\begin{document}\n" + assembled_body + "\n\\end{document}"
    if suffix.strip():
        document += "\n" + suffix.strip()
    return document


def _extract_document_body(latex: str) -> str:
    if r"\begin{document}" not in latex or r"\end{document}" not in latex:
        return latex.strip()
    _prefix, remainder = latex.split(r"\begin{document}", 1)
    body, _suffix = remainder.rsplit(r"\end{document}", 1)
    return body.strip()


def _finalize_latex(state: WorkflowState, latex: str) -> str:
    cleaned = _strip_latex_fence(latex)
    if not cleaned:
        raise RuntimeError("runner returned empty latex")
    if not state.template_id:
        if "\\documentclass" in cleaned:
            return cleaned
        raise RuntimeError("runner returned non-document latex without template")
    body = _extract_document_body(cleaned)
    return _wrap_latex_with_template(state, body)


def runner_node(config: AppConfig, state: WorkflowState) -> WorkflowState:
    if _should_run_sectional(state):
        return sectional_runner_node(config, state)
    model = state.runner_model or config.runner_model
    skill = _skill_text(state.writing_type)
    template = get_template(state.template_id) if state.template_id else {}
    state.writing_language = _normalize_writing_language(state.plan.get("writing_language") or state.writing_language or _template_language(state.template_id) or ("zh" if state.writing_type == "grant" else "en"))
    state.writing_profile = _resolve_writing_profile(state)
    profile = state.writing_profile
    state.bibliography_profile = _normalize_bibliography_profile(state.bibliography_profile)
    bibliography_hint = _bibliography_command_hint(state.bibliography_profile)
    if not state.template_profile and state.run_id:
        try:
            state.template_profile = build_template_profile(state.run_id, template_id=state.template_id)
        except Exception:
            pass
    template_comprehension = _template_structure_hint(state.template_id, state.template_profile)
    evidence = _compact_evidence_markdown(state.evidence, limit=min(8, max(4, state.rag_limit or 8)))
    has_evidence = bool(state.evidence)
    workspace_context = _truncate(_workspace_prompt_context(config, state), 2600)
    source_material_context = _truncate(_source_material_context(state, limit=6), 2600)
    survey_report_context = _truncate(str((state.survey_report or {}).get("markdown") or "暂无 Surveyor 调研报告。"), 3200)
    evidence_section = (
        "本地真实文献：\n" + evidence
        if has_evidence
        else "本地真实文献：无（文献库中未检索到匹配论文，严禁编造引用键！）"
    )
    cite_rule = (
        "引用必须遵循上面的参考文献系统，优先使用模板已有的引用命令；禁止输出 [P1]、[P2] 这类伪证据标记"
        if has_evidence
        else "文献库无匹配论文，需要引用处请使用 LaTeX 注释占位符 %[cite: 需要引用支持的主题，如：某某方法的奠基性工作] 替代，严禁编造 \\\\cite{XXX} 形式的虚假引用键"
    )
    prompt = f"""
## 写作协议：先理解模板，再填充内容

### 第一步：分析模板（在脑中完成，不要输出）
1. 仔细阅读「模板理解」和 Planner 中的 sections 数组——这就是你的章节清单
2. 确认每个章节的标题和顺序，这是不可变更的框架
3. 确认模板的章节层级（\\chapter 还是 \\section）、引用命令、参考文献系统
4. 确认每个章节的写作职责

### 第二步：按章节顺序逐一生成内容
严格按 sections 数组的顺序和标题填充正文。绝不新增、删除、重排序任何章节。
禁止修改 documentclass、导言区、\\maketitle 和参考文献尾部。
除非用户明确要求改结构，否则模板框架神圣不可侵犯。

---

写作技能：
{skill}

模板理解：
{template_comprehension}

Planner JSON：
{json.dumps(state.plan, ensure_ascii=False, indent=2)}

目标语言：
{_language_name(state.writing_language)}

写作画像：
{json.dumps({
    "label": profile.get("label", ""),
    "execution_mode": profile.get("execution_mode", ""),
    "material_policy": profile.get("material_policy", ""),
    "results_policy": profile.get("results_policy", ""),
    "citation_policy": profile.get("citation_policy", ""),
}, ensure_ascii=False, indent=2)}

代码工作区：
{workspace_context}

用户补充材料：
{source_material_context}

Surveyor 调研报告：
{survey_report_context}

{evidence_section}

参考文献系统：
{bibliography_hint}

要求：
- 只输出 LaTeX 源码，不要 markdown 代码块
- **必须遵守"模板理解"中的全部硬性约束**，不得修改 documentclass、导言区、\\maketitle 区域和参考文献尾部结构
- 必须使用模板已检测到的引用命令（如 \\citep/\\citet/\\parencite），不得擅自切换为 \\cite
- 文末参考文献必须沿用模板已有的 `.bib` 文件和 \\bibliographystyle，不要默认改成 `reference.bib` / `plain`
- 如果模板定义了 \\chapter 层级则使用 \\chapter；如果是 \\section 层级则用 \\section。严格匹配模板的章节层级
- {_language_instruction(state.writing_language)}
- {profile.get("material_policy", "")}
- {profile.get("results_policy", "")}
- {cite_rule}
- 方法、实验设计与结果分析必须优先结合工作区中的代码文件和结果图；若使用图片，只能引用 `assets/workspace/...`
- 若存在用户补充的 method/results/requirements 文档，方法、实验、结果分析必须以这些材料为一手依据，文献仅用于定位、比较和解释
- 必须吸收 Surveyor 调研报告中的方法谱系、数据集、局限与重点论文，并把这些信息体现在 related work、method positioning、experiments 和 discussion 中
- 不要编造不存在的论文、数据集或结果
- 必须输出完整清晰结构，不得缺少 planner 给出的任何章节
- 每个章节必须有实质内容，不允许 TODO、占位符、"待扩展" 或 "This section should be expanded"
- 每个主要章节至少写 4-6 个自然段；长综述或基金申请应围绕"问题-证据-分析-小结"展开
- 每个段落必须有明确功能：定义问题、综述证据、比较方法、指出差距、提出路线、讨论风险之一
- 文章长度要足以支撑完整结构；不要生成只有提纲和短句的摘要式文稿
- 如果证据不足，在 Limitations 中明确说明
""".strip()
    raw = _chat_completion(
        state.api_key,
        state.model_provider,
        model,
        [
            {"role": "system", "content": TEMPLATE_GUARDIAN_PROMPT},
            {"role": "user", "content": prompt},
        ],
        timeout=240,
    )
    state.latex = _finalize_latex(state, raw)
    state.messages.append("runner completed")
    return state


def _should_run_sectional(state: WorkflowState) -> bool:
    if str(os.environ.get("SCIENTIFIC_AGENT_FORCE_MONOLITHIC", "")).strip().lower() in {"1", "true", "yes"}:
        return False
    if bool(state.force_sectional):
        return True
    sections = [str(item).strip() for item in (state.plan.get("sections") or []) if str(item).strip()]
    if len(sections) > 1:
        return True
    if state.template_id or state.project_mode:
        return True
    goal = state.goal
    return (
        _is_report_like_template(state.template_id)
        or state.writing_type == "grant"
        or "综述" in goal
        or "长综述" in goal
        or "20页" in goal
        or "20 页" in goal
        or "申报书" in goal
        or "长文" in goal
    )


def _is_long_form_goal(state: WorkflowState) -> bool:
    text = " ".join([state.goal, state.requirements, str(state.plan.get("runner_instructions", ""))]).lower()
    tokens = [
        "综述",
        "长综述",
        "长文",
        "20页",
        "20 页",
        "review",
        "survey",
        "long-form",
    ]
    return any(token in text for token in tokens)


def _manifest_entry(section: str, index: int) -> dict[str, Any]:
    slug = _safe_slug(section)
    return {
        "index": index,
        "title": section,
        "slug": slug,
        "path": f"sections/{slug}.tex",
    }


def _compose_sectional_body_from_manifest(state: WorkflowState, sections_manifest: list[dict[str, Any]]) -> str:
    abstract_path = ""
    references_path = ""
    main_paths: list[str] = []
    for item in sections_manifest:
        title = str(item.get("title") or "")
        path = str(item.get("path") or "")
        if not path:
            continue
        lowered = title.lower()
        if title in {"摘要", "Abstract"} or lowered == "abstract":
            abstract_path = path
        elif "参考" in title or lowered == "references":
            references_path = path
        else:
            main_paths.append(path)

    is_book_like = _template_heading_command(state.template_id) == r"\chapter"
    if is_book_like:
        parts = ["\\maketitle", "\\frontmatter"]
        if abstract_path:
            parts.append(f"\\input{{{abstract_path}}}")
        parts.append("\\tableofcontents")
        parts.append("\\mainmatter")
        parts.extend(f"\\input{{{path}}}" for path in main_paths)
        if references_path:
            parts.append("\\backmatter")
            parts.append(f"\\input{{{references_path}}}")
        return "\n\n".join(parts)

    parts = []
    if abstract_path:
        parts.append(f"\\input{{{abstract_path}}}")
    parts.extend(f"\\input{{{path}}}" for path in main_paths)
    if references_path:
        parts.append(f"\\input{{{references_path}}}")
    return "\n\n".join(parts)


def sectional_runner_node(config: AppConfig, state: WorkflowState) -> WorkflowState:
    title = _latex_escape(state.plan.get("title") or state.goal or "Research Draft")
    state.writing_language = _normalize_writing_language(state.plan.get("writing_language") or state.writing_language or _template_language(state.template_id) or ("zh" if state.writing_type == "grant" else "en"))
    template_sections = _template_section_outline(state.template_id, state.template_profile)
    sections = template_sections or (_grant_sections() if state.writing_type == "grant" else state.plan.get("sections") or [])
    long_form = _is_long_form_goal(state)
    if not state.evidence_memory:
        state.evidence_memory = _build_evidence_memory(state)
    run_dir = DEFAULT_OUTPUT_DIR / state.run_id
    fragments_dir = run_dir / "section_fragments"
    run_dir.mkdir(parents=True, exist_ok=True)
    fragments_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence_memory.json").write_text(
        json.dumps(state.evidence_memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sections_manifest: list[dict[str, Any]] = []
    body_parts: list[str] = []
    section_failure_path = run_dir / "section_failure.json"
    include_abstract = _should_include_abstract(state, [str(item) for item in sections])
    abstract_title = "Abstract" if state.writing_language == "en" else "摘要"
    if include_abstract:
        try:
            abstract = _generate_section_latex(config, state, abstract_title, target_paragraphs=4)
            if not abstract:
                raise RuntimeError(f"{abstract_title} generation failed")
            abstract = _normalize_evidence_citations(
                abstract,
                _citation_lookup(state, _select_section_evidence(state, abstract_title)),
                state.bibliography_profile,
            )
            abstract = _fix_bare_citations(abstract)
            state.section_memories.append(
                _section_memory_from_fragment(
                    abstract_title,
                    abstract,
                    [str(card.get("key", "")) for card in _select_section_evidence(state, abstract_title)],
                )
            )
            starter = render_template_starter(state.template_id, title="x", author="y") if state.template_id else ""
            abstract_body = _strip_section_wrapper(abstract)
            abstract_body = re.sub(r"\\begin\{abstract\}", "", abstract_body)
            abstract_body = re.sub(r"\\end\{abstract\}", "", abstract_body)
            abstract_fragment = (
                f"\\chapter*{{{abstract_title}}}\n" + abstract_body
                if _template_heading_command(state.template_id) == r"\chapter" else "\\begin{abstract}\n" + abstract_body + "\n\\end{abstract}"
            )
            sections_manifest.append(_manifest_entry(abstract_title, len(sections_manifest)))
            body_parts.append(abstract_fragment)
            _write_section_progress(run_dir, fragments_dir, state, abstract_title, abstract_fragment, body_parts, title)
        except Exception as exc:
            state.error = f"sectional runner failed at {abstract_title}: {exc}"
            state.messages.append(state.error)
            section_failure_path.write_text(
                json.dumps({"section": abstract_title, "error": str(exc)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    for section in sections:
        if state.error:
            break
        lowered = str(section).lower()
        if lowered in {"abstract", "摘要", "references", "参考文献"} or (state.writing_language != "en" and "参考" in str(section)):
            continue
        entry = _manifest_entry(str(section), len(sections_manifest))
        sections_manifest.append(entry)
        target_paragraphs = _section_target_paragraphs(state, str(section), long_form)
        try:
            fragment = _generate_section_latex(
                config,
                state,
                str(section),
                target_paragraphs=target_paragraphs,
            )
            if not fragment:
                raise RuntimeError(f"{section} generation failed")
            heading = _template_heading_command(state.template_id)
            if not re.search(rf"\\{heading[1:]}\{{", fragment):
                fragment = f"{heading}{{{_latex_escape(section)}}}\n{fragment}"
            selected = _select_section_evidence(state, str(section))
            fragment = _normalize_evidence_citations(fragment, _citation_lookup(state, selected), state.bibliography_profile)
            fragment = _fix_bare_citations(fragment)
            state.section_memories.append(
                _section_memory_from_fragment(
                    str(section),
                    fragment,
                    [str(card.get("key", "")) for card in selected],
                )
            )
            body_parts.append(fragment)
            _write_section_progress(run_dir, fragments_dir, state, str(section), fragment, body_parts, title)
        except Exception as exc:
            state.error = f"sectional runner failed at {section}: {exc}"
            state.messages.append(state.error)
            section_failure_path.write_text(
                json.dumps({"section": str(section), "error": str(exc)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            break
    references_fragment = _references_section_latex(state)
    if state.writing_language == "en":
        reference_title = "References"
    else:
        reference_title = "主要参考文献" if _is_report_like_template(state.template_id) else "参考文献"
    references_entry = _manifest_entry(reference_title, len(sections_manifest))
    sections_manifest.append(references_entry)
    body_parts.append(references_fragment)
    _write_section_progress(run_dir, fragments_dir, state, reference_title, references_fragment, body_parts, title)
    (run_dir / "sections_manifest.json").write_text(
        json.dumps({"sections": sections_manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    body = _compose_sectional_body_from_manifest(state, sections_manifest)
    if state.template_id:
        state.latex = _wrap_latex_with_template(state, body)
    else:
        state.latex = _wrap_latex_document(title, body, state.writing_language)
    state.messages.append("sectional runner completed")
    return state


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.U).strip("_")
    return cleaned[:80] or "section"


def _write_section_progress(
    run_dir: Path,
    fragments_dir: Path,
    state: WorkflowState,
    section: str,
    fragment: str,
    body_parts: list[str],
    title: str,
) -> None:
    slug = _safe_slug(section)
    fragment_path = fragments_dir / f"{slug}.tex"
    fragment_path.write_text(fragment, encoding="utf-8")
    sections_dir = run_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    (sections_dir / f"{slug}.tex").write_text(fragment, encoding="utf-8")
    # Also sync to project_files/sections/ so the web UI can display the content
    ui_sections_dir = run_dir / "project_files" / "sections"
    ui_sections_dir.mkdir(parents=True, exist_ok=True)
    (ui_sections_dir / f"{slug}.tex").write_text(fragment, encoding="utf-8")
    (run_dir / "section_memory.json").write_text(
        json.dumps(state.section_memories, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    context_summary = "\n".join(
        [
            f"# Context Summary",
            "",
            f"- latest_section: {section}",
            f"- section_count: {len(state.section_memories)}",
            "",
            "## Rolling Memories",
            _history_memory_text(state),
        ]
    ).strip() + "\n"
    (run_dir / "context_summary.md").write_text(context_summary, encoding="utf-8")
    partial = "\n\n".join(body_parts)
    (run_dir / "manuscript.partial.tex").write_text(
        _wrap_latex_document(title, partial),
        encoding="utf-8",
    )


def _grant_sections() -> list[str]:
    return [
        "一、项目摘要",
        "二、立项依据与研究意义",
        "三、国内外研究现状与发展趋势",
        "四、拟解决的关键科学问题",
        "五、研究目标",
        "六、研究内容",
        "七、技术路线与研究方案",
        "八、创新点",
        "九、年度研究计划",
        "十、预期研究成果",
        "十一、研究基础与可行性分析",
        "十二、风险分析与对策",
        "十三、参考文献",
    ]


def _section_target_paragraphs(state: WorkflowState, section: str, long_form: bool) -> int:
    if _is_report_like_template(state.template_id):
        mapping = {
            "课题来源及研究的目的和意义": 7,
            "国内外在该方向的研究现状及分析": 8,
            "主要研究内容": 8,
            "研究方案": 8,
            "进度安排，预期达到的目标": 6,
            "课题已具备和所需的条件、经费": 7,
            "研究过程中可能遇到的困难和问题，解决的措施": 7,
        }
        return mapping.get(section, 7)
    if state.writing_type == "grant":
        return 7
    return 8 if long_form else 5


def _content_signal_length(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", str(text or "")))


def _balanced_braces(text: str) -> bool:
    balance = 0
    escaped = False
    for char in str(text or ""):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
            if balance < 0:
                return False
    return balance == 0


def _sanitize_generated_fragment(fragment: str) -> str:
    text = _strip_latex_fence(fragment)
    text = re.sub(r"\\begin\{figure\}\s*\\cite\{[^}]*\}", r"\\begin{figure}[htbp]", text)
    text = re.sub(r"\\begin\{figure\}\s*\[([^\]]*)\]", r"\\begin{figure}[\1]", text)
    text = re.sub(
        r"\\text(?!tt\b|bf\b|it\b|rm\b|sf\b|sc\b|width\b|height\b|backslash\b|asciitilde\b|asciicircum\b|\{)",
        "",
        text,
    )
    text = re.sub(r"\\section\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\(?:texttt|cite|ref|caption|label)\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\includegraphics(?:\[[^\]\n]*\])?\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"[（(]\s*$", "", text, flags=re.M)
    rows = text.splitlines()
    while rows:
        tail = rows[-1].rstrip()
        if not tail:
            rows.pop()
            continue
        if re.search(r"(\\end\{[^}]+\}|[。！？；：\}\]])$", tail):
            break
        if re.search(r"(\\section\{|\\(?:texttt|cite|ref|caption|includegraphics|label)\{|[（(])", tail):
            rows.pop()
            continue
        if _content_signal_length(tail) < 80:
            rows.pop()
            continue
        break
    text = "\n".join(rows).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _workspace_label_map(config: AppConfig, state: WorkflowState, section: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    labels = {item["label"] for item in _workspace_figure_blocks(config, state, section) if item.get("label")}
    if "fig:workspace_results" in labels:
        for legacy in ["fig:preliminary", "fig:regression", "fig:reg_cnn", "fig:reg_m1", "fig:reg_m2", "fig:reg_m3"]:
            mapping[legacy] = "fig:workspace_results"
    if "fig:workspace_method" in labels:
        for legacy in ["fig:pipeline", "fig:framework", "fig:system", "fig:overview"]:
            mapping[legacy] = "fig:workspace_method"
    return mapping


def _normalize_workspace_refs(
    config: AppConfig,
    state: WorkflowState,
    section: str,
    fragment: str,
) -> str:
    mapping = _workspace_label_map(config, state, section)
    cleaned = fragment
    for source, target in mapping.items():
        cleaned = cleaned.replace(rf"\ref{{{source}}}", rf"\ref{{{target}}}")
    return cleaned


def _fragment_quality_issues(fragment: str, section: str, target_paragraphs: int) -> list[str]:
    cleaned = _strip_latex_fence(fragment).strip()
    issues: list[str] = []
    if not cleaned:
        return ["empty"]
    section_lower = str(section or "").strip().lower()
    is_abstract = section_lower in {"abstract", "摘要"}
    if not is_abstract and not re.search(rf"\\section\{{\s*{re.escape(section)}\s*\}}", cleaned):
        issues.append("missing_section_title")
    if re.search(r"\\section\{[^}\n]*$", cleaned, flags=re.M):
        issues.append("truncated_section_title")
    if re.search(r"\\(?:texttt|cite|ref|caption|label)\{[^}\n]*$", cleaned, flags=re.M):
        issues.append("truncated_command")
    if re.search(r"\\includegraphics(?:\[[^\]\n]*\])?\{[^}\n]*$", cleaned, flags=re.M):
        issues.append("truncated_figure_command")
    if cleaned.count(r"\begin{figure") != cleaned.count(r"\end{figure}"):
        issues.append("unclosed_figure")
    if not _balanced_braces(cleaned):
        issues.append("unbalanced_braces")
    body = _strip_section_wrapper(cleaned)
    paragraphs = [item for item in re.split(r"\n\s*\n", body) if _content_signal_length(item) >= 60]
    if len(paragraphs) < max(3, target_paragraphs - 2):
        issues.append("too_few_paragraphs")
    if _content_signal_length(body) < max(380, target_paragraphs * 110):
        issues.append("too_short")
    lines = [item.rstrip() for item in cleaned.splitlines() if item.strip()]
    if lines:
        tail = lines[-1]
        if re.search(r"[（(:,：，、]$", tail):
            issues.append("truncated_tail")
    return list(dict.fromkeys(issues))


def _generate_section_latex(
    config: AppConfig,
    state: WorkflowState,
    section: str,
    target_paragraphs: int,
) -> str:
    model = state.runner_model or config.runner_model
    skill = _truncate(_skill_text(state.writing_type), 500)
    profile = _ensure_writing_profile(state)
    brief = _section_brief(state, section)
    state.bibliography_profile = _normalize_bibliography_profile(state.bibliography_profile)
    bibliography_hint = _bibliography_command_hint(state.bibliography_profile)
    section_evidence_limit = 6
    selected_evidence = _select_section_evidence(state, section, limit=section_evidence_limit)
    evidence = _format_evidence_cards(selected_evidence)
    has_section_evidence = bool(selected_evidence)
    history = _truncate(_history_memory_text(state), 700)
    evidence_keys = ", ".join(str(card.get("key", "")) for card in selected_evidence)
    evidence_section_block = (
        "本节优先证据卡片：\n" + evidence
        if has_section_evidence
        else "本节优先证据卡片：无（文献库中未检索到匹配论文，严禁编造引用键！）"
    )
    cite_keys_line = (
        "- 优先引用本节 citation keys：" + evidence_keys
        if has_section_evidence
        else "- 文献库无匹配论文，需要引用处请使用 LaTeX 注释占位符 %[cite: 需要引用支持的主题] 替代，严禁编造 \\cite{{XXX}} 形式的虚假引用键"
    )
    workspace_context = _truncate(_section_workspace_context(config, state, section), 4200)
    source_material_context = _truncate(_source_material_context(state, section=section, limit=4), 2600)
    survey_report_context = _truncate(str((state.survey_report or {}).get("markdown") or "暂无 Surveyor 调研报告。"), 2400)
    section_title_instruction = (
        f"Use the title \\section{{{section}}}; if this is the abstract, do not emit a section command and write only the abstract body."
        if state.writing_language == "en"
        else f"本节标题用 \\section{{{section}}}，如果是摘要则不要写 section，只写摘要正文"
    )
    base_prompt = """
## 写作协议：聚焦当前章节，服从模板框架

### 第一步：确认你的任务边界（在脑中完成）
1. 你只负责「{section}」这一节，不要越界写其他章节
2. 查看全局写作计划摘要，确认本节在整个文档中的位置和职责
3. 确认模板使用的引用命令和章节层级——本节必须沿用

### 第二步：生成本节内容
你是基金/论文写作 runner。请只生成『{section}』这一节的完整 LaTeX 片段，不要输出完整文档，不要 markdown。

写作目标：
{goal}

全局写作计划摘要：
{plan_brief}

前文历史记忆：
{history}

写作技能：
{skill}

写作画像：
{profile_label}

本节任务卡：
{task_card}

代码工作区：
{workspace_context}

用户补充材料：
{source_material_context}

Surveyor 调研报告：
{survey_report_context}

{evidence_section_block}

用户附加要求：
{requirements}

参考文献系统：
{bibliography_hint}

要求：
- {language_instruction}
- {section_title_instruction}
- 写 {target_paragraphs} 到 {target_extra} 个自然段；若该节适合列表，可先写段落再列点
- 每段必须围绕一个明确功能：问题、证据、分析、路线、创新、风险或小结；优先服从本节任务卡中的 mission
{cite_keys_line}
- 引用必须遵循上面的参考文献系统，优先使用模板已有的引用命令；禁止输出 [P1]、[P2] 这类伪证据标记
- 如果模板已存在引用命令，新增引用必须沿用同一命令；不要把原模板里的 \\citep / \\citet / \\parencite 等改写成标准 \\cite
- {material_policy}
- {results_policy}
- 若工作区中存在代码或结果图，与本节相关时必须具体写出文件名、模型名、训练设置、指标或图像结论，不能泛泛而谈
- 若工作区中已有可复用图块，只能复制工作区上下文中给出的图块，禁止自造 figure 语法和新 label
- 若工作区图像解读没有明确给出具体数值，禁止自行编造 R²、RMSE、MAE 或百分比提升，只能描述趋势、对比关系和图中现象
- 如果本节任务卡中带有 survey_hints，必须尽量把这些关键词对应的相关工作、数据集、局限或方法谱系写进去，而不是只做空泛综述
- 如果本节属于方法/实验/结果/研究基础/可行性，并且用户补充材料或工作区信息存在，必须优先把它们作为一手事实来源；文献只用于定位、比较和解释
- 如果执行模式是 results_first，严禁把文献中的方法细节硬套成用户自己的方法；必须以用户材料、代码和工作区内容为准
- 如果是中文基金模式，已有结果只能写成研究基础、预实验观察、技术储备或可行性证据，不能写成项目已完成的最终产出
- 不要试图复述全部文献；只保留支撑本节论证的高优先级信息
- 续写时避免重复前文历史记忆中已经展开过的内容
- 不得出现 TODO、占位符、待扩展、This section should be expanded
- 不得编造不存在的文献、数据和实验结果
- 中文国自然申报书要使用正式、具体、可执行的语气
- 如果目标语言是英文，段落、标题、图题和分析语句必须全部为英文，不得夹带中文说明
- 只写当前章节，不要把整篇文章内容一次性展开
- 必须输出完整收尾，不能截断在半句话、半个命令或半个图环境上
- 直接进入正文，不要解释你的写作过程
""".format(
        section=section,
        goal=state.goal,
        plan_brief=_plan_brief(state),
        history=history,
        skill=skill,
        profile_label=profile.get("label", ""),
        task_card=_format_section_brief(brief),
        workspace_context=workspace_context,
        source_material_context=source_material_context,
        survey_report_context=survey_report_context,
        evidence_section_block=evidence_section_block,
        requirements=state.requirements or "无",
        bibliography_hint=bibliography_hint,
        language_instruction=_language_instruction(state.writing_language),
        section_title_instruction=section_title_instruction,
        target_paragraphs=target_paragraphs,
        target_extra=target_paragraphs + 2,
        cite_keys_line=cite_keys_line,
        material_policy=profile.get("material_policy", ""),
        results_policy=profile.get("results_policy", ""),
    ).strip()
    prompt = base_prompt
    fragment = ""
    issues: list[str] = []
    for attempt in range(3):
        raw = _chat_completion(
            state.api_key,
            state.model_provider,
            model,
            [
                {"role": "system", "content": TEMPLATE_GUARDIAN_PROMPT},
                {"role": "user", "content": prompt},
            ],
            timeout=int(os.environ.get("KIMI_SECTION_TIMEOUT", "180")),
            retries=int(os.environ.get("KIMI_SECTION_RETRIES", "2")),
            max_tokens=int(os.environ.get("KIMI_SECTION_MAX_TOKENS", "3400")),
        )
        fragment = _sanitize_generated_fragment(raw)
        fragment = _normalize_evidence_citations(
            fragment,
            _citation_lookup(state, selected_evidence),
            state.bibliography_profile,
        )
        fragment = _normalize_workspace_refs(config, state, section, fragment)
        if "TODO" in fragment or "This section should be expanded" in fragment or "待扩展" in fragment:
            issues = ["placeholder"]
        else:
            issues = _fragment_quality_issues(fragment, section, target_paragraphs)
        if not issues:
            break
        prompt = f"""
请重写『{section}』这一节的完整 LaTeX 片段，不要续写残句，也不要输出 markdown。

当前草稿存在这些问题：
{", ".join(issues)}

你必须基于下面的工作区与文献要求，重新输出一版完整可编译的章节：
{base_prompt}

有问题的草稿如下，请只把其中可用的信息吸收后重写，不要照抄残缺语句：
{fragment or _strip_latex_fence(raw)}
""".strip()
    if issues:
        raise RuntimeError(f"section failed: {section}: {', '.join(issues)}")
    blocks = _workspace_figure_blocks(config, state, section)
    labels_in_fragment = set(re.findall(r"\\label\{([^}]+)\}", fragment))
    if blocks and not re.search(r"\\begin\{figure", fragment):
        block = blocks[-1] if any(token in section for token in ["进度", "条件", "基础", "可行性", "结果", "分析"]) else blocks[0]
        if block.get("label") and block["label"] not in labels_in_fragment:
            fragment = fragment.rstrip() + "\n\n" + block["block"]
    return fragment


def _strip_section_wrapper(fragment: str) -> str:
    cleaned = _strip_latex_fence(fragment)
    cleaned = re.sub(r"\\section\{[^}]+\}", "", cleaned).strip()
    return cleaned


def _citation_lookup(state: WorkflowState, selected_evidence: list[dict[str, Any]] | None = None) -> dict[str, str]:
    items: list[dict[str, Any]] = []
    if selected_evidence:
        items.extend(selected_evidence)
    items.extend(state.evidence)
    lookup: dict[str, str] = {}
    for item in items:
        key = str(item.get("key") or "").strip()
        citation_key = str(item.get("citation_key") or key).strip()
        legacy_key = str(item.get("legacy_key") or "").strip()
        if key and citation_key:
            lookup[key] = citation_key
        if legacy_key and citation_key:
            lookup[legacy_key] = citation_key
    return lookup


def _normalize_evidence_citations(
    fragment: str,
    citation_lookup: dict[str, str] | None = None,
    bibliography_profile: dict[str, Any] | None = None,
) -> str:
    lookup = citation_lookup or {}
    allowed_keys = {value for value in lookup.values() if value} | {key for key in lookup.keys() if key}
    preferred_command = _preferred_cite_command(bibliography_profile)
    cite_pattern = re.compile(
        r"\\("
        + "|".join(_SUPPORTED_CITE_COMMANDS)
        + r")(\*?)((?:\[[^\]]*\]){0,2})\{([^}]+)\}"
    )

    def repl(match: re.Match[str]) -> str:
        command = str(match.group(1) or "").strip() or preferred_command
        star = str(match.group(2) or "")
        options = str(match.group(3) or "")
        raw_keys = [item.strip() for item in match.group(4).split(",") if item.strip()]
        keys: list[str] = []
        for item in raw_keys:
            candidate = lookup.get(item, item)
            if allowed_keys and candidate not in allowed_keys:
                continue
            keys.append(candidate)
        if not keys:
            return ""
        return "\\" + command + star + options + "{" + ",".join(dict.fromkeys(keys)) + "}"

    def bracket_repl(match: re.Match[str]) -> str:
        raw_keys = [item.strip() for item in match.group(1).split(",") if item.strip()]
        if not raw_keys or not all(item in lookup for item in raw_keys):
            return match.group(0)
        keys = [lookup[item] for item in raw_keys if lookup.get(item)]
        if not keys:
            return ""
        return "\\" + preferred_command + "{" + ",".join(dict.fromkeys(keys)) + "}"

    cleaned = cite_pattern.sub(repl, fragment)
    cleaned = re.sub(r"\[\\text\{([^}]+)\}\]", r"[\1]", cleaned)
    cleaned = re.sub(r"\[\\mathrm\{([^}]+)\}\]", r"[\1]", cleaned)
    cleaned = re.sub(
        r"\[\s*([A-Za-z0-9_\-\u4e00-\u9fff]+(?:\s*,\s*[A-Za-z0-9_\-\u4e00-\u9fff]+)*)\s*\]",
        bracket_repl,
        cleaned,
    )
    return cleaned


# LaTeX commands that look like they could be bare citation keys (contain digits)
# but are legitimate commands that should not be wrapped
_SAFE_LATEX_COMMANDS = frozenset({
    # Standard LaTeX
    r"\begin", r"\end", r"\section", r"\subsection", r"\subsubsection",
    r"\chapter", r"\paragraph", r"\subparagraph",
    r"\textbf", r"\textit", r"\texttt", r"\textsf", r"\textmd", r"\textrm",
    r"\emph", r"\cite", r"\citep", r"\citet", r"\citeauthor", r"\citeyear",
    r"\nocite", r"\bibliography", r"\bibliographystyle", r"\printbibliography",
    r"\input", r"\include", r"\usepackage", r"\documentclass", r"\usepackage",
    r"\label", r"\ref", r"\eqref", r"\pageref",
    r"\caption", r"\footnote", r"\item", r"\centering", r"\raggedright",
    r"\hline", r"\cline", r"\newpage", r"\clearpage", r"\pagebreak",
    r"\noindent", r"\vspace", r"\hspace", r"\hfill",
    r"\newcommand", r"\renewcommand", r"\newenvironment", r"\renewenvironment",
    r"\DeclareMathOperator", r"\operatorname",
    # Math
    r"\mathbf", r"\mathcal", r"\mathbb", r"\mathfrak", r"\mathsf", r"\mathtt",
    r"\mathit", r"\mathrm", r"\pm", r"\mp", r"\times", r"\div", r"\cdot",
    r"\leq", r"\geq", r"\equiv", r"\sim", r"\approx", r"\propto",
    r"\alpha", r"\beta", r"\gamma", r"\delta", r"\epsilon", r"\varepsilon",
    r"\zeta", r"\eta", r"\theta", r"\vartheta", r"\iota", r"\kappa",
    r"\lambda", r"\mu", r"\nu", r"\xi", r"\pi", r"\varpi", r"\rho",
    r"\varrho", r"\sigma", r"\varsigma", r"\tau", r"\upsilon", r"\phi",
    r"\varphi", r"\chi", r"\psi", r"\omega",
    r"\Gamma", r"\Delta", r"\Theta", r"\Lambda", r"\Xi", r"\Pi",
    r"\Sigma", r"\Upsilon", r"\Phi", r"\Psi", r"\Omega",
    r"\partial", r"\infty", r"\nabla", r"\forall", r"\exists", r"\emptyset",
    r"\sum", r"\prod", r"\int", r"\oint", r"\bigcup", r"\bigcap",
    r"\sqrt", r"\frac", r"\left", r"\right", r"\langle", r"\rangle",
    r"\ldots", r"\cdots", r"\vdots", r"\ddots",
    r"\rightarrow", r"\Rightarrow", r"\longrightarrow", r"\mapsto",
    r"\leftarrow", r"\Leftarrow", r"\longleftarrow",
    r"\leftrightarrow", r"\Leftrightarrow",
    r"\in", r"\notin", r"\subset", r"\subseteq", r"\supset", r"\supseteq",
    r"\cup", r"\cap", r"\setminus", r"\wedge", r"\vee", r"\neg",
    r"\oplus", r"\ominus", r"\otimes", r"\oslash", r"\odot",
    r"\otimes", r"\circ", r"\bullet", r"\diamond", r"\star",
    r"\mid", r"\colon",
    r"\hat", r"\tilde", r"\bar", r"\vec", r"\dot", r"\ddot",
    r"\overline", r"\underline", r"\widehat", r"\widetilde",
    r"\min", r"\max", r"\sup", r"\inf", r"\lim", r"\log", r"\ln", r"\exp",
    r"\sin", r"\cos", r"\tan", r"\cot", r"\sec", r"\csc",
    r"\arcsin", r"\arccos", r"\arctan",
    r"\sinh", r"\cosh", r"\tanh",
    r"\det", r"\dim", r"\gcd", r"\hom", r"\ker", r"\Pr",
    r"\big", r"\Big", r"\bigg", r"\Bigg",
    r"\displaystyle", r"\textstyle", r"\scriptstyle",
    r"\nonumber", r"\notag", r"\smash", r"\phantom",
})


def _fix_bare_citations(fragment: str) -> str:
    """Wrap bare citation keys (e.g. \\grote2003field) with \\cite{}.

    LLM-generated LaTeX sometimes emits citation keys as bare commands
    (\\namdari2024advancing instead of \\cite{namdari2024advancing}).
    This causes ``Undefined control sequence'' errors in xelatex, which
    makes latexmk refuse to run bibtex.
    """
    # Match: backslash + word containing at least one digit + letter
    # The key insight: citation keys look like \grote2003field or \xu2024soil
    # Use ASCII-only character classes — \w matches Unicode which pulls in CJK

    lines = fragment.split("\n")
    fixed: list[str] = []
    bare_pattern = re.compile(r'\\([a-zA-Z]+\d{2,}[a-zA-Z0-9_]*)')
    for line in lines:
        tokens = list(bare_pattern.finditer(line))
        if not tokens:
            fixed.append(line)
            continue
        # Process replacements from right to left to preserve positions
        replacements: list[tuple[int, int, str]] = []
        for m in tokens:
            full = m.group(0)
            # Skip if inside \cite{...} or other citation wrapper
            prefix = line[max(0, m.start() - 6):m.start()]
            if re.search(r'(cite[A-Za-z]*|bibitem)\{$', prefix):
                continue
            # Skip known safe LaTeX commands (exact match or prefix)
            cmd_tokens = full.split("{")[0]  # Remove possible args
            if cmd_tokens in _SAFE_LATEX_COMMANDS:
                continue
            # Skip if any safe command is a prefix of this token
            safe_prefix = False
            for sc in _SAFE_LATEX_COMMANDS:
                if cmd_tokens == sc or cmd_tokens.startswith(sc + "{"):
                    safe_prefix = True
                    break
            if safe_prefix:
                continue
            # Extract the citation key (without backslash)
            key = m.group(1)
            replacements.append((m.start(), m.end(), "\\cite{" + key + "}"))
        # Apply replacements right-to-left
        result = list(line)
        for start, end, new_text in reversed(replacements):
            result[start:end] = new_text
        fixed.append("".join(result))
    return "\n".join(fixed)


def _evidence_section_latex(state: WorkflowState) -> str:
    rows = []
    for item in state.evidence:
        rows.append(
            "\\item "
            + f"[{_latex_escape(item.get('citation_key', item['key']))}] "
            + _latex_escape(item.get("title", ""))
            + f" ({_latex_escape(item.get('year', ''))}, {_latex_escape(item.get('venue', ''))}). "
            + _latex_escape(item.get("summary") or item.get("abstract") or "")
        )
    section_title = "Local Evidence" if _normalize_writing_language(state.writing_language) == "en" else "本地证据库检索结果"
    if not rows:
        empty_text = (
            "No usable evidence entries were retrieved in the current run."
            if _normalize_writing_language(state.writing_language) == "en"
            else "当前运行未检索到可用于整理的证据条目。"
        )
        return f"\\section{{{section_title}}}\n{empty_text}"
    return f"\\section{{{section_title}}}\n\\begin{{enumerate}}\n" + "\n".join(rows) + "\n\\end{enumerate}"


def _references_section_latex(state: WorkflowState) -> str:
    reference_title = "References" if _normalize_writing_language(state.writing_language) == "en" else "参考文献"
    profile = _normalize_bibliography_profile(state.bibliography_profile)
    tail = str(profile.get("tail") or "").strip()
    if tail:
        return tail
    if not state.evidence:
        empty_text = (
            "No formal bibliography entries were retrieved in the current run."
            if _normalize_writing_language(state.writing_language) == "en"
            else "当前运行未检索到可用于整理的正式文献条目。"
        )
        if str(state.project_mode or profile.get("project_mode") or "").strip() == "manual_upload":
            return ""
        return f"\\section{{{reference_title}}}\n{empty_text}"
    if profile.get("backend") == "biblatex":
        return "\\printbibliography"
    if state.template_id.startswith("hithesis-"):
        if _is_report_like_template(state.template_id):
            return "\\section{主要参考文献}\n\\bibliographystyle{hithesis}\n\\bibliography{reference}"
        return "\\bibliographystyle{hithesis}\n\\bibliography{reference}"
    if profile.get("source_paths") or profile.get("bib_files") or profile.get("cite_commands"):
        bib_files = profile.get("bib_files") or []
        bib_basename = "reference"
        if bib_files:
            bib_basename = bib_files[0]
            if bib_basename.lower().endswith(".bib"):
                bib_basename = bib_basename[:-4]
        if state.template_id.startswith("hithesis-"):
            if _is_report_like_template(state.template_id):
                return f"\\section{{主要参考文献}}\n\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
            return f"\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
        backend = profile.get("backend") or ""
        if backend in {"natbib", "bibtex"}:
            return f"\\bibliographystyle{{plain}}\n\\bibliography{{{bib_basename}}}"
        return f"\\bibliographystyle{{plain}}\n\\bibliography{{{bib_basename}}}"
    if str(state.project_mode or profile.get("project_mode") or "").strip() == "manual_upload":
        return ""
    return "\\bibliographystyle{plain}\n\\bibliography{reference}"


def _strip_latex_fence(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:latex|tex)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def _compiler() -> str:
    for candidate in ["xelatex", "pdflatex", "tectonic"]:
        path = shutil.which(candidate)
        if path:
            return path
    return ""


def compile_node(_config: AppConfig, state: WorkflowState) -> WorkflowState:
    output_dir = DEFAULT_OUTPUT_DIR / state.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    tex_path = output_dir / "manuscript.tex"
    bib_path = output_dir / _default_bib_name()
    plan_path = output_dir / "plan.json"
    evidence_path = output_dir / "evidence.json"
    evidence_memory_path = output_dir / "evidence_memory.json"
    section_memory_path = output_dir / "section_memory.json"
    workspace_analysis_path = output_dir / "workspace_analysis.json"
    survey_report_path = output_dir / "survey_report.md"
    survey_report_json_path = output_dir / "survey_report.json"
    agent_outputs_path = output_dir / "agent_outputs.json"
    review_report_path = output_dir / "review_report.json"
    bibliography = _bibliography_bibtex(state)
    tex_path.write_text(state.latex, encoding="utf-8")
    if bibliography.strip():
        bib_path.write_text(bibliography, encoding="utf-8")
    plan_path.write_text(json.dumps(state.plan, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_path.write_text(json.dumps(state.evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_memory_path.write_text(
        json.dumps(state.evidence_memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    section_memory_path.write_text(
        json.dumps(state.section_memories, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if state.workspace_analysis:
        workspace_analysis_path.write_text(
            json.dumps(state.workspace_analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if state.survey_report:
        survey_report_path.write_text(str(state.survey_report.get("markdown") or ""), encoding="utf-8")
        survey_report_json_path.write_text(
            json.dumps(state.survey_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if state.agent_outputs:
        agent_outputs_path.write_text(
            json.dumps(state.agent_outputs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if state.review_report:
        review_report_path.write_text(
            json.dumps(state.review_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    compiler = _compiler()
    state.artifacts = {
        "output_dir": str(output_dir),
        "tex_path": str(tex_path),
        "bib_path": str(bib_path) if bib_path.exists() else "",
        "plan_path": str(plan_path),
        "evidence_path": str(evidence_path),
        "evidence_memory_path": str(evidence_memory_path),
        "section_memory_path": str(section_memory_path),
        "workspace_analysis_path": str(workspace_analysis_path) if workspace_analysis_path.exists() else "",
        "survey_report_path": str(survey_report_path) if survey_report_path.exists() else "",
        "survey_report_json_path": str(survey_report_json_path) if survey_report_json_path.exists() else "",
        "agent_outputs_path": str(agent_outputs_path) if agent_outputs_path.exists() else "",
        "review_report_path": str(review_report_path) if review_report_path.exists() else "",
    }
    sections_manifest_path = output_dir / "sections_manifest.json"
    if sections_manifest_path.exists():
        state.artifacts["sections_manifest_path"] = str(sections_manifest_path)
    fragments_dir = output_dir / "section_fragments"
    if fragments_dir.exists():
        state.artifacts["section_fragments_dir"] = str(fragments_dir)
    if not compiler:
        state.compile_result = {
            "status": "skipped",
            "reason": "No LaTeX compiler found. Install xelatex, pdflatex, or tectonic.",
        }
        state.messages.append("latex compile skipped")
        return state

    latexmk = shutil.which("latexmk")
    bibtex = shutil.which("bibtex")
    stdout_tail = ""
    stderr_tail = ""
    returncode = 0
    compile_engine = Path(compiler).name
    if latexmk:
        completed = subprocess.run(
            [latexmk, "-pdf", "-interaction=nonstopmode", tex_path.name],
            cwd=output_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=240,
            check=False,
        )
        compile_engine = "latexmk"
        stdout_tail = completed.stdout[-4000:]
        stderr_tail = completed.stderr[-4000:]
        returncode = completed.returncode
    elif Path(compiler).name == "tectonic":
        completed = subprocess.run(
            [compiler, tex_path.name],
            cwd=output_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=240,
            check=False,
        )
        stdout_tail = completed.stdout[-4000:]
        stderr_tail = completed.stderr[-4000:]
        returncode = completed.returncode
    else:
        commands = [[compiler, "-interaction=nonstopmode", tex_path.name]]
        if bibliography.strip() and bibtex:
            commands.append([bibtex, tex_path.stem])
            commands.append([compiler, "-interaction=nonstopmode", tex_path.name])
            commands.append([compiler, "-interaction=nonstopmode", tex_path.name])
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=output_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=240,
                check=False,
            )
            stdout_tail = (stdout_tail + "\n" + completed.stdout)[-4000:]
            stderr_tail = (stderr_tail + "\n" + completed.stderr)[-4000:]
            returncode = completed.returncode
            if completed.returncode != 0:
                break
    pdf_path = output_dir / "manuscript.pdf"
    state.compile_result = {
        "status": "compiled" if returncode == 0 and pdf_path.exists() else "failed",
        "compiler": compile_engine,
        "returncode": returncode,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "pdf_path": str(pdf_path) if pdf_path.exists() else "",
    }
    if pdf_path.exists():
        state.artifacts["pdf_path"] = str(pdf_path)
    state.messages.append("latex compile attempted")
    return state


def _run_langgraph_if_available(config: AppConfig, state: WorkflowState) -> WorkflowState | None:
    try:
        from langgraph.graph import END, StateGraph
    except Exception:
        return None

    def planner(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = planner_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def literature(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = literature_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def rag(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = rag_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def surveyor(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = surveyor_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def architect(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = architect_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def runner(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = runner_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def reviewer(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = reviewer_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    def compile_latex(state_dict: dict[str, Any]) -> dict[str, Any]:
        next_state = compile_node(config, WorkflowState(**state_dict))
        return next_state.__dict__

    graph = StateGraph(dict)
    graph.add_node("planner", planner)
    graph.add_node("literature", literature)
    graph.add_node("rag", rag)
    graph.add_node("surveyor", surveyor)
    graph.add_node("architect", architect)
    graph.add_node("runner", runner)
    graph.add_node("reviewer", reviewer)
    graph.add_node("compile", compile_latex)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "literature")
    graph.add_edge("literature", "rag")
    graph.add_edge("rag", "surveyor")
    graph.add_edge("surveyor", "architect")
    graph.add_edge("architect", "runner")
    graph.add_edge("runner", "reviewer")
    graph.add_edge("reviewer", "compile")
    graph.add_edge("compile", END)
    compiled = graph.compile()
    result = compiled.invoke(state.__dict__)
    return WorkflowState(**result)


def run_research_workflow(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    explicit_language = str(payload.get("writing_language") or "").strip()
    inferred_language = explicit_language or _template_language(str(payload.get("template_id") or "")) or ("zh" if str(payload.get("writing_type") or "academic") == "grant" else "en")
    state = WorkflowState(
        goal=goal,
        writing_type=str(payload.get("writing_type") or "academic"),
        writing_language=_normalize_writing_language(inferred_language),
        template_id=str(payload.get("template_id") or ""),
        force_sectional=bool(payload.get("force_sectional", False)),
        requirements=str(payload.get("requirements") or ""),
        query=str(payload.get("query") or ""),
        use_literature_pipeline=bool(payload.get("use_literature_pipeline", False)),
        max_literature_results=int(payload.get("max_literature_results") or 12),
        summarize_limit=int(payload.get("summarize_limit") or 4),
        rag_limit=int(payload.get("rag_limit") or 0),
        exclude_preprints=bool(payload.get("exclude_preprints", False)),
        api_key=str(payload.get("api_key") or ""),
        model_provider=normalize_model_provider(str(payload.get("model_provider") or "kimi")),
        planner_model=str(payload.get("planner_model") or config.planner_model),
        runner_model=str(payload.get("runner_model") or config.runner_model),
        run_id=str(payload.get("run_id") or _run_id()),
        project_mode=str(payload.get("project_mode") or ""),
        source_materials=[
            item
            for item in (payload.get("source_materials") or [])
            if isinstance(item, dict)
        ],
        bibliography_profile=_normalize_bibliography_profile(payload.get("bibliography_profile") or {}),
        template_profile=payload.get("template_profile") if isinstance(payload.get("template_profile"), dict) else {},
        workspace_index=payload.get("workspace_index") if isinstance(payload.get("workspace_index"), dict) else {},
    )
    result = _run_langgraph_if_available(config, state)
    engine = "langgraph" if result else "sequential"
    if not result:
        for node in [planner_node, literature_node, rag_node, surveyor_node, architect_node, runner_node, reviewer_node, compile_node]:
            state = node(config, state)
        result = state
    workspace_manifest: dict[str, Any] = {}
    workspace_sections: list[dict[str, Any]] = []
    sections_manifest_path = result.artifacts.get("sections_manifest_path", "")
    if sections_manifest_path:
        manifest_path = Path(sections_manifest_path)
        if manifest_path.exists():
            try:
                workspace_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                workspace_manifest = {}
    fragments_dir_raw = result.artifacts.get("section_fragments_dir", "")
    fragments_dir = Path(fragments_dir_raw) if fragments_dir_raw else None
    for item in workspace_manifest.get("sections", []) if isinstance(workspace_manifest, dict) else []:
        title = str(item.get("title") or "")
        slug = str(item.get("slug") or "")
        fragment_path = fragments_dir / f"{slug}.tex" if fragments_dir and slug else None
        if fragment_path and fragment_path.exists():
            workspace_sections.append(
                {
                    **item,
                    "content": fragment_path.read_text(encoding="utf-8"),
                }
            )
    return {
        "status": "ok" if not result.error else "failed",
        "engine": engine,
        "run_id": result.run_id,
        "goal": result.goal,
        "query": result.query,
        "template_id": result.template_id,
        "requirements": result.requirements,
        "writing_language": result.writing_language,
        "plan": result.plan,
        "evidence": result.evidence,
        "evidence_memory": result.evidence_memory,
        "section_memories": result.section_memories,
        "survey_report": result.survey_report,
        "agent_outputs": result.agent_outputs,
        "review_report": result.review_report,
        "workspace_analysis": result.workspace_analysis,
        "bibliography": _bibliography_bibtex(result),
        "bib_name": _default_bib_name(),
        "workspace_manifest": workspace_manifest,
        "workspace_sections": workspace_sections,
        "latex": result.latex,
        "literature_result": {
            key: value
            for key, value in result.literature_result.items()
            if key not in {"ranked", "summaries"}
        },
        "artifacts": result.artifacts,
        "compile": result.compile_result,
        "messages": result.messages,
        "error": result.error,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("goal")
    parser.add_argument("--query", default="")
    parser.add_argument("--writing-type", default="academic", choices=["academic", "grant"])
    parser.add_argument("--writing-language", default="", choices=["", "en", "zh"])
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--rag-limit", type=int, default=0)
    args = parser.parse_args()

    config = load_config()
    result = run_research_workflow(
        config,
        {
            "goal": args.goal,
            "query": args.query,
            "writing_type": args.writing_type,
            "writing_language": args.writing_language,
            "use_literature_pipeline": args.crawl,
            "rag_limit": args.rag_limit,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
