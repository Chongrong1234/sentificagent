from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .chat import load_default_model_provider, load_provider_api_key, normalize_model_provider, provider_api_base
from .config import PROJECT_ROOT


GUARDRAILS_SCHEMA_VERSION = 1
GUARDRAILS_DIR = PROJECT_ROOT / "configs" / "guardrails"
MEMORY_GUARDRAILS = "guardrails.yaml"


@dataclass
class Violation:
    code: str
    message: str
    location: str = ""
    section_id: str = ""


@dataclass
class ValidationResult:
    valid: bool
    violations: list[dict[str, str]]


def _normalize_rel_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    return re.sub(r"/{2,}", "/", text)


def _slugify(value: str) -> str:
    cleaned: list[str] = []
    last_sep = False
    for char in str(value or "").strip().lower():
        if char.isalnum() or ("\u4e00" <= char <= "\u9fff"):
            cleaned.append(char)
            last_sep = False
            continue
        if not last_sep:
            cleaned.append("-")
            last_sep = True
    return "".join(cleaned).strip("-") or "section"


def _top_level_heading_command(template_profile: dict[str, Any]) -> str:
    hierarchy = (template_profile or {}).get("section_hierarchy") or {}
    top_level = str(hierarchy.get("top_level") or "section").strip().lower()
    if top_level == "chapter":
        return r"\chapter"
    return r"\section"


def _citation_required(title: str, path: str) -> bool:
    text = f"{title} {path}".lower()
    return any(token in text for token in ["文献", "综述", "现状", "related work", "reference", "literature"])


def _requires_figures(title: str, path: str) -> bool:
    text = f"{title} {path}".lower()
    return any(token in text for token in ["方案", "method", "实验", "experiment", "结果", "技术路线"])


def _negotiation_mode(title: str, path: str) -> str:
    text = f"{title} {path}".lower()
    if "参考文献" in title or "references" in text:
        return "skip"
    if any(token in text for token in ["进度", "schedule", "条件", "经费", "funding", "budget"]):
        return "light"
    return "full"


def _required_elements(title: str) -> list[str]:
    normalized = str(title or "")
    lowered = normalized.lower()
    if "意义" in normalized or "背景" in normalized or "introduction" in lowered:
        return ["问题背景", "现有不足", "研究目标", "研究价值"]
    if "现状" in normalized or "related work" in lowered or "文献" in normalized:
        return ["研究方向划分", "代表性工作", "方法比较", "研究空白"]
    if "研究内容" in normalized:
        return ["任务拆解", "关键方法", "预期产出"]
    if "方案" in normalized or "method" in lowered:
        return ["技术路线", "数据来源", "实验设计", "评价方式"]
    if "进度" in normalized or "schedule" in lowered:
        return ["阶段划分", "里程碑", "交付物"]
    if "风险" in normalized or "困难" in normalized or "discussion" in lowered:
        return ["潜在问题", "影响分析", "缓解措施"]
    return []


def _writing_guide(title: str) -> str:
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
    if "风险" in normalized or "困难" in normalized or "discussion" in lowered:
        return "逐条列出潜在风险，并给出可执行的缓解措施。"
    if "参考文献" in normalized or "references" in lowered:
        return "该部分由系统维护，不需要协商写作。"
    return "按模板标题职责写清楚本节要解决的问题、方法与结论。"


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
    if "背景" in normalized or "意义" in normalized or "introduction" in lowered:
        return [
            {"id": "problem_gap_value", "label": "背景-缺口-价值", "description": "从背景、空白到研究价值递进。"},
            {"id": "application_first", "label": "应用场景型", "description": "先写工程场景，再落到研究问题。"},
        ]
    return [
        {"id": "direct", "label": "直接展开", "description": "按模板标题直接展开内容。"},
        {"id": "structured", "label": "结构化展开", "description": "先列子点，再分别写段落。"},
    ]


def _split_document_segments(text: str) -> tuple[str, str, str]:
    source = str(text or "")
    begin = source.find(r"\begin{document}")
    end = source.rfind(r"\end{document}")
    if begin == -1 or end == -1 or end < begin:
        return "", source, ""
    prefix = source[:begin].rstrip()
    body = source[begin + len(r"\begin{document}") : end].strip()
    suffix = source[end + len(r"\end{document}") :].strip()
    return prefix, body, suffix


def _extract_document_body(text: str) -> str:
    if r"\begin{document}" not in str(text or "") or r"\end{document}" not in str(text or ""):
        return str(text or "").strip()
    _prefix, body, _suffix = _split_document_segments(str(text or ""))
    return body.strip()


def _extract_bibliography_tail(text: str) -> str:
    body = _extract_document_body(str(text or ""))
    markers = [r"\printbibliography", r"\bibliographystyle", r"\bibliography", r"\nocite{*}"]
    positions = [body.find(marker) for marker in markers if body.find(marker) != -1]
    if not positions:
        return ""
    return body[min(positions) :].strip()


def _strip_bibliography_tail(text: str) -> str:
    body = _extract_document_body(str(text or ""))
    tail = _extract_bibliography_tail(body)
    if not tail:
        return body.strip()
    index = body.find(tail)
    if index == -1:
        return body.strip()
    return body[:index].rstrip()


def _heading_pattern(command: str) -> re.Pattern[str]:
    safe = re.escape(command.lstrip("\\"))
    return re.compile(rf"(?m)^[^%\n]*\\{safe}\*?\{{([^}}\n]+)\}}")


def _extract_headings(text: str, command: str) -> list[tuple[str, str]]:
    return [(match.group(0), str(match.group(1) or "").strip()) for match in _heading_pattern(command).finditer(str(text or ""))]


def _replace_first_heading(text: str, command: str, expected_title: str) -> str:
    pattern = _heading_pattern(command)
    replacement = f"{command}{{{expected_title}}}"
    return pattern.sub(lambda _match: replacement, str(text or ""), count=1)


def _remove_unknown_headings(text: str, command: str, allowed_titles: set[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        title = str(match.group(1) or "").strip()
        return match.group(0) if title in allowed_titles else "\n"

    return _heading_pattern(command).sub(repl, str(text or ""))


def _restore_heading_sequence(text: str, command: str, expected_titles: list[str]) -> str:
    matches = list(_heading_pattern(command).finditer(str(text or "")))
    if not matches or not expected_titles:
        return str(text or "")
    parts: list[str] = []
    cursor = 0
    for index, match in enumerate(matches):
        parts.append(str(text or "")[cursor:match.start()])
        if index < len(expected_titles):
            parts.append(f"{command}{{{expected_titles[index]}}}")
        else:
            parts.append("\n")
        cursor = match.end()
    parts.append(str(text or "")[cursor:])
    return "".join(parts)


def _guess_section_file(title: str, path: str, index: int) -> str:
    normalized = _normalize_rel_path(path)
    if normalized:
        return normalized
    return f"sections/{index:02d}_{_slugify(title)}.tex"


def _normalize_citation_command(profile: dict[str, Any]) -> str:
    preferred = str(profile.get("preferred_cite_command") or "").strip().lstrip("\\")
    commands = [str(item).strip().lstrip("\\") for item in (profile.get("cite_commands") or []) if str(item).strip()]
    if preferred:
        return preferred
    if commands:
        return commands[0]
    return "cite"


def _build_sections(project_id: str, project: dict[str, Any]) -> list[dict[str, Any]]:
    from .template_profile import build_template_profile

    template_profile = project.get("template_profile") or build_template_profile(project_id, project_dir=Path(project.get("paths", {}).get("dir") or ""))
    heading = _top_level_heading_command(template_profile)
    sections: list[dict[str, Any]] = []

    try:
        from . import writing_workspace as ww

        manifest = ww._load_sections_manifest(project_id)
        raw_sections = manifest.get("sections", []) if isinstance(manifest, dict) else []
        for index, item in enumerate(raw_sections, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            path = str(item.get("path") or "").strip()
            if not title:
                continue
            mode = _negotiation_mode(title, path)
            sections.append(
                {
                    "id": str(item.get("slug") or item.get("id") or _slugify(title)),
                    "sort_order": index,
                    "title": title,
                    "heading": heading,
                    "file": _guess_section_file(title, path, index),
                    "negotiation": mode,
                    "title_immutable": True,
                    "allow_subsections": mode != "skip",
                    "allow_subsubsections": "方案" in title or "method" in title.lower(),
                    "min_paragraphs": 1 if mode == "light" else 3,
                    "citation_required": _citation_required(title, path),
                    "suggested_order": index,
                    "writing_guide": _writing_guide(title),
                    "required_elements": _required_elements(title),
                    "subsection_strategies": _section_options(title),
                    "requires_figures": _requires_figures(title, path),
                }
            )
        if sections:
            return sections
    except Exception:
        pass

    hierarchy = (template_profile or {}).get("section_hierarchy") or {}
    titles = hierarchy.get("titles") or {}
    top_level = str(hierarchy.get("top_level") or "section")
    raw_titles = titles.get(top_level) or []
    if top_level == "chapter":
        raw_titles = hierarchy.get("mainmatter_chapter_titles") or raw_titles
    for index, title in enumerate(raw_titles, start=1):
        title_text = str(title or "").strip()
        if not title_text:
            continue
        path = _guess_section_file(title_text, "", index)
        mode = _negotiation_mode(title_text, path)
        sections.append(
            {
                "id": _slugify(title_text),
                "sort_order": index,
                "title": title_text,
                "heading": heading,
                "file": path,
                "negotiation": mode,
                "title_immutable": True,
                "allow_subsections": mode != "skip",
                "allow_subsubsections": "方案" in title_text or "method" in title_text.lower(),
                "min_paragraphs": 1 if mode == "light" else 3,
                "citation_required": _citation_required(title_text, path),
                "suggested_order": index,
                "writing_guide": _writing_guide(title_text),
                "required_elements": _required_elements(title_text),
                "subsection_strategies": _section_options(title_text),
                "requires_figures": _requires_figures(title_text, path),
            }
        )
    if sections:
        return sections

    main_tex = str(project.get("main_tex") or "").strip()
    if not main_tex:
        return sections
    try:
        from . import writing_workspace as ww

        content = str(ww.read_project_file(project_id, main_tex).get("content") or "")
    except Exception:
        content = ""
    for index, (_raw, title) in enumerate(_extract_headings(content, heading), start=1):
        path = _guess_section_file(title, "", index)
        mode = _negotiation_mode(title, path)
        sections.append(
            {
                "id": _slugify(title),
                "sort_order": index,
                "title": title,
                "heading": heading,
                "file": path,
                "negotiation": mode,
                "title_immutable": True,
                "allow_subsections": mode != "skip",
                "allow_subsubsections": "方案" in title or "method" in title.lower(),
                "min_paragraphs": 1 if mode == "light" else 3,
                "citation_required": _citation_required(title, path),
                "suggested_order": index,
                "writing_guide": _writing_guide(title),
                "required_elements": _required_elements(title),
                "subsection_strategies": _section_options(title),
                "requires_figures": _requires_figures(title, path),
            }
        )
    return sections


def _synthesized_guardrails(project_id: str) -> dict[str, Any]:
    from . import writing_workspace as ww
    from .template_library import get_template

    project = ww.load_project(project_id)
    template_id = str(project.get("template_id") or "").strip()
    template_name = template_id or "manual-upload"
    source = "user-upload"
    if template_id:
        try:
            template_name = str(get_template(template_id).get("name") or template_id)
            source = "built-in"
        except Exception:
            template_name = template_id
    bibliography_profile = project.get("bibliography_profile") or {}
    citation_style = "biblatex" if str(bibliography_profile.get("backend") or "") == "biblatex" else "bibtex"
    sections = _build_sections(project_id, project)
    heading = sections[0].get("heading", r"\section") if sections else r"\section"
    return {
        "schema_version": GUARDRAILS_SCHEMA_VERSION,
        "template": {
            "id": template_id,
            "name": template_name,
            "source": source,
        },
        "immutable_zones": [
            {"id": "documentclass", "description": r"\documentclass 行及所有选项", "detection": r"regex:\\documentclass"},
            {"id": "preamble", "description": r"\begin{document} 之前的导言区", "detection": "preamble"},
            {"id": "bibliography_tail", "description": "参考文献尾区", "detection": "bibliography_tail"},
            {"id": "end_document", "description": r"\end{document}", "detection": r"regex:\\end{document}"},
        ],
        "citation": {
            "style": citation_style,
            "command": rf"\{_normalize_citation_command(bibliography_profile)}{{}}",
            "bib_files": bibliography_profile.get("bib_files") or [],
            "require_approval": True,
            "min_strength": 2,
        },
        "sections": sections,
        "defaults": {
            "top_level_heading": heading,
        },
    }


def _memory_guardrails_path(project_id: str) -> Path:
    from . import writing_workspace as ww

    return ww._memory_file(project_id, MEMORY_GUARDRAILS)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _builtin_guardrails_path(template_id: str) -> Path:
    return GUARDRAILS_DIR / f"{template_id}.yaml"


def resolve_section_id(guardrails: dict[str, Any], rel_path: str = "", title: str = "") -> str:
    normalized_path = _normalize_rel_path(rel_path)
    title_text = str(title or "").strip()
    for section in guardrails.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        if normalized_path and _normalize_rel_path(str(section.get("file") or "")) == normalized_path:
            return str(section.get("id") or "")
        if title_text and str(section.get("title") or "").strip() == title_text:
            return str(section.get("id") or "")
    return ""


def _guardrails_has_sections(payload: dict[str, Any]) -> bool:
    sections = payload.get("sections") or []
    return bool(sections)


def load_guardrails(project_id: str) -> dict:
    memory_path = _memory_guardrails_path(project_id)
    payload = _load_yaml(memory_path)
    if payload and _guardrails_has_sections(payload):
        return payload

    from . import writing_workspace as ww

    project = ww.load_project(project_id)
    template_id = str(project.get("template_id") or "").strip()
    if template_id:
        builtin = _load_yaml(_builtin_guardrails_path(template_id))
        if builtin and _guardrails_has_sections(builtin):
            _write_yaml(memory_path, builtin)
            return builtin

    generated = _synthesized_guardrails(project_id)
    _write_yaml(memory_path, generated)
    return generated


def read_project_guardrails_yaml(project_id: str) -> str:
    path = _memory_guardrails_path(project_id)
    if not path.exists():
        payload = load_guardrails(project_id)
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    return path.read_text(encoding="utf-8")


def save_project_guardrails_yaml(project_id: str, yaml_text: str) -> dict[str, Any]:
    payload = yaml.safe_load(str(yaml_text or "")) or {}
    if not isinstance(payload, dict):
        raise ValueError("guardrails yaml must decode to an object")
    if not payload.get("schema_version"):
        payload["schema_version"] = GUARDRAILS_SCHEMA_VERSION
    _write_yaml(_memory_guardrails_path(project_id), payload)
    return payload


def _section_definition(guardrails: dict[str, Any], section_id: str | None) -> dict[str, Any]:
    if not section_id:
        return {}
    for item in guardrails.get("sections", []) or []:
        if isinstance(item, dict) and str(item.get("id") or "") == str(section_id or ""):
            return item
    return {}


def _collect_violations(
    new_content: str,
    existing_content: str,
    guardrails: dict[str, Any],
    section_id: str | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    new_text = str(new_content or "")
    existing_text = str(existing_content or "")
    section = _section_definition(guardrails, section_id)
    top_level_heading = str((guardrails.get("defaults") or {}).get("top_level_heading") or section.get("heading") or r"\section")
    allowed_titles = {str(item.get("title") or "").strip() for item in (guardrails.get("sections") or []) if isinstance(item, dict)}

    if r"\begin{document}" in existing_text and r"\end{document}" in existing_text:
        existing_prefix, _existing_body, existing_suffix = _split_document_segments(existing_text)
        new_prefix, _new_body, new_suffix = _split_document_segments(new_text)
        if new_prefix and existing_prefix and new_prefix.strip() != existing_prefix.strip():
            violations.append(Violation(code="immutable_preamble", message="导言区属于 immutable zone，不能修改。"))
        existing_tail = _extract_bibliography_tail(existing_text)
        new_tail = _extract_bibliography_tail(new_text)
        if existing_tail and new_tail and new_tail.strip() != existing_tail.strip():
            violations.append(Violation(code="immutable_bibliography_tail", message="参考文献尾区属于 immutable zone，不能修改。"))
        if existing_suffix and new_suffix and new_suffix.strip() != existing_suffix.strip():
            violations.append(Violation(code="immutable_end_document", message=r"\end{document} 之后的尾区不能修改。"))

    if section:
        heading = str(section.get("heading") or top_level_heading)
        expected_title = str(section.get("title") or "").strip()
        new_headings = _extract_headings(new_text, heading)
        if expected_title and bool(section.get("title_immutable")) and new_headings:
            current_title = new_headings[0][1]
            if current_title and current_title != expected_title:
                violations.append(
                    Violation(
                        code="immutable_title",
                        message=f"章节标题不可修改：{current_title} -> {expected_title}",
                        section_id=str(section.get("id") or ""),
                    )
                )
        if not bool(section.get("allow_subsections", True)) and re.search(r"(?m)^[^%\n]*\\subsection\*?\{", new_text):
            violations.append(Violation(code="subsection_not_allowed", message="当前章节不允许新增 \\subsection。", section_id=str(section.get("id") or "")))
        if not bool(section.get("allow_subsubsections", True)) and re.search(r"(?m)^[^%\n]*\\subsubsection\*?\{", new_text):
            violations.append(Violation(code="subsubsection_not_allowed", message="当前章节不允许新增 \\subsubsection。", section_id=str(section.get("id") or "")))

    for _raw, title in _extract_headings(new_text, top_level_heading):
        if title not in allowed_titles:
            violations.append(Violation(code="unknown_section", message=f"新增了未定义的 {top_level_heading}{{{title}}}。"))

    return violations


def validate_content(
    new_content: str,
    existing_content: str,
    guardrails: dict,
    section_id: str | None = None,
) -> ValidationResult:
    violations = _collect_violations(new_content, existing_content, guardrails, section_id=section_id)
    return ValidationResult(valid=not violations, violations=[asdict(item) for item in violations])


def strip_illegal_content(
    new_content: str,
    existing_content: str,
    guardrails: dict,
    section_id: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    violations = _collect_violations(new_content, existing_content, guardrails, section_id=section_id)
    if not violations:
        return str(new_content or ""), []

    sanitized = str(new_content or "")
    existing_text = str(existing_content or "")
    section = _section_definition(guardrails, section_id)
    top_level_heading = str((guardrails.get("defaults") or {}).get("top_level_heading") or section.get("heading") or r"\section")
    allowed_titles = {str(item.get("title") or "").strip() for item in (guardrails.get("sections") or []) if isinstance(item, dict)}
    expected_titles = [str(item.get("title") or "").strip() for item in (guardrails.get("sections") or []) if isinstance(item, dict) and str(item.get("title") or "").strip()]

    if r"\begin{document}" in existing_text and r"\end{document}" in existing_text:
        existing_prefix, _existing_body, existing_suffix = _split_document_segments(existing_text)
        _new_prefix, new_body, _new_suffix = _split_document_segments(sanitized)
        if not new_body.strip():
            new_body = _extract_document_body(sanitized)
        existing_tail = _extract_bibliography_tail(existing_text)
        body_without_tail = _strip_bibliography_tail(new_body)
        assembled_body = body_without_tail.strip()
        if existing_tail:
            assembled_body = (assembled_body + "\n\n" + existing_tail).strip() if assembled_body else existing_tail
        sanitized = (
            (existing_prefix.rstrip() + "\n" if existing_prefix.strip() else "")
            + r"\begin{document}"
            + "\n"
            + assembled_body.strip()
            + "\n"
            + r"\end{document}"
        )
        if existing_suffix.strip():
            sanitized += "\n" + existing_suffix.strip()

    if section:
        heading = str(section.get("heading") or top_level_heading)
        expected_title = str(section.get("title") or "").strip()
        if expected_title and bool(section.get("title_immutable")) and _extract_headings(sanitized, heading):
            sanitized = _replace_first_heading(sanitized, heading, expected_title)
        if not bool(section.get("allow_subsections", True)):
            sanitized = re.sub(r"(?m)^[^%\n]*\\subsection\*?\{[^}\n]+\}\s*\n?", "", sanitized)
        if not bool(section.get("allow_subsubsections", True)):
            sanitized = re.sub(r"(?m)^[^%\n]*\\subsubsection\*?\{[^}\n]+\}\s*\n?", "", sanitized)
    else:
        sanitized = _restore_heading_sequence(sanitized, top_level_heading, expected_titles)

    sanitized = _remove_unknown_headings(sanitized, top_level_heading, allowed_titles)
    return sanitized, [asdict(item) for item in violations]


def build_guardrails_prompt(
    guardrails: dict,
    section_id: str,
    writing_order: list[str],
    locked_section_summaries: dict[str, str],
) -> str:
    section = _section_definition(guardrails, section_id)
    citation = guardrails.get("citation") or {}
    heading = str(section.get("heading") or (guardrails.get("defaults") or {}).get("top_level_heading") or r"\section")
    citation_command = str(citation.get("command") or r"\cite{}")
    ordered_locked = [f"- {item}: {locked_section_summaries.get(item, '')}" for item in writing_order if item in locked_section_summaries]
    required_elements = "\n".join(f"- {item}" for item in (section.get("required_elements") or []))
    return "\n".join(
        [
            "## 模板护栏",
            f"- 顶层标题命令：{heading}",
            f"- 标题不可修改：{'是' if section.get('title_immutable', False) else '否'}",
            f"- 允许 subsection：{'是' if section.get('allow_subsections', True) else '否'}",
            f"- 允许 subsubsection：{'是' if section.get('allow_subsubsections', True) else '否'}",
            f"- 需要引用审批：{'是' if citation.get('require_approval') else '否'}",
            f"- 引用命令：{citation_command}",
            "",
            "## 当前章节契约",
            yaml.safe_dump(section or {}, allow_unicode=True, sort_keys=False).strip() if section else "无",
            "",
            "## 已锁定章节摘要",
            "\n".join(ordered_locked) if ordered_locked else "无",
            "",
            "## 本章必写元素",
            required_elements or "无",
        ]
    ).strip()


def generate_guardrails_from_template(
    template_content: str,
    template_id: str,
    api_key: str,
) -> dict:
    content = str(template_content or "")
    secret = str(api_key or "").strip()
    if secret:
        try:
            import json
            from urllib import error, request

            system_prompt = (
                "你是 LaTeX 模板结构分析器。"
                "请读取模板正文，输出一份严格 JSON guardrails 草案。"
                "只输出 JSON object，不要解释，不要 markdown。"
            )
            user_prompt = (
                "请基于以下 LaTeX 模板，提取：\n"
                "1. 顶层 section/chapter 标题顺序\n"
                "2. 每章是否适合 full/light/skip 协商\n"
                "3. 是否需要引用、是否需要图表\n"
                "4. immutable_zones 与 citation 配置\n"
                "输出字段必须包含 schema_version, template, immutable_zones, citation, sections, defaults。\n\n"
                f"template_id: {template_id or 'uploaded-template'}\n"
                f"latex:\n{content[:18000]}"
            )
            provider = normalize_model_provider(load_default_model_provider())
            body = {
                "model": "deepseek-chat" if provider == "ds" else "kimi-k2.5",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 2400,
            }
            req = request.Request(
                f"{provider_api_base(provider)}/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {secret}",
                },
                method="POST",
            )
            with request.urlopen(req, timeout=90) as response:
                raw = json.loads(response.read().decode("utf-8"))
            message = (
                raw.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            cleaned = str(message or "").strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S).strip()
            payload = json.loads(cleaned)
            if isinstance(payload, dict) and payload.get("sections"):
                payload.setdefault("schema_version", GUARDRAILS_SCHEMA_VERSION)
                payload.setdefault(
                    "template",
                    {
                        "id": str(template_id or ""),
                        "name": str(template_id or "uploaded-template"),
                        "source": "llm-generated",
                    },
                )
                payload.setdefault("defaults", {"top_level_heading": r"\section"})
                return payload
        except Exception:
            pass
    titles = _extract_headings(content, r"\chapter")
    heading = r"\chapter" if titles else r"\section"
    if not titles:
        titles = _extract_headings(content, r"\section")
    sections: list[dict[str, Any]] = []
    for index, (_raw, title) in enumerate(titles, start=1):
        path = _guess_section_file(title, "", index)
        mode = _negotiation_mode(title, path)
        sections.append(
            {
                "id": _slugify(title),
                "sort_order": index,
                "title": title,
                "heading": heading,
                "file": path,
                "negotiation": mode,
                "title_immutable": True,
                "allow_subsections": mode != "skip",
                "allow_subsubsections": False,
                "min_paragraphs": 1 if mode == "light" else 3,
                "citation_required": _citation_required(title, path),
                "suggested_order": index,
                "writing_guide": _writing_guide(title),
                "required_elements": _required_elements(title),
                "subsection_strategies": _section_options(title),
                "requires_figures": _requires_figures(title, path),
            }
        )
    return {
        "schema_version": GUARDRAILS_SCHEMA_VERSION,
        "template": {
            "id": str(template_id or ""),
            "name": str(template_id or "uploaded-template"),
            "source": "llm-generated" if str(template_id or "").strip() else "user-upload",
        },
        "immutable_zones": [
            {"id": "documentclass", "description": r"\documentclass 行及所有选项", "detection": r"regex:\\documentclass"},
            {"id": "preamble", "description": r"\begin{document} 之前的导言区", "detection": "preamble"},
            {"id": "bibliography_tail", "description": "参考文献尾区", "detection": "bibliography_tail"},
            {"id": "end_document", "description": r"\end{document}", "detection": r"regex:\\end{document}"},
        ],
        "citation": {
            "style": "bibtex",
            "command": r"\cite{}",
            "bib_files": [],
            "require_approval": True,
            "min_strength": 2,
        },
        "sections": sections,
        "defaults": {
            "top_level_heading": heading,
        },
    }


def analyze_project_template_guardrails(
    project_id: str,
    *,
    api_key: str = "",
) -> dict[str, Any]:
    from . import writing_workspace as ww

    project = ww.load_project(project_id)
    main_tex = str(project.get("main_tex") or "").strip()
    if not main_tex:
        raise ValueError("project main_tex is missing")
    content = str(ww.read_project_file(project_id, main_tex).get("content") or "")
    secret = load_provider_api_key(load_default_model_provider(), api_key)
    payload = generate_guardrails_from_template(
        content,
        str(project.get("template_id") or ""),
        secret,
    )
    _write_yaml(_memory_guardrails_path(project_id), payload)
    return payload
