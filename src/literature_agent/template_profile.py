"""Comprehensive LaTeX template comprehension for writing projects.

Replaces the narrow bibliography_profile with a full template fingerprint
that the LLM receives before generating content.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .template_library import get_template

_FILES_DIR_NAME = "project_files"
_MEMORY_DIR_NAME = "memory"


def _project_file_path(project_dir: Path, rel_path: str) -> Path:
    normalized = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    return project_dir / _FILES_DIR_NAME / normalized


def _relative_files(project_dir: Path) -> list[str]:
    root = project_dir / _FILES_DIR_NAME
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and _MEMORY_DIR_NAME not in path.relative_to(root).parts[:1]
    )


# ── Parser helpers ──


def _parse_document_class(content: str) -> dict[str, str]:
    match = re.search(r"\\documentclass(?:\[([^\]]*)\])?\{([^}]+)\}", content)
    if not match:
        return {"name": "article", "options": ""}
    options_raw = str(match.group(1) or "").strip()
    options: dict[str, str] = {}
    for item in options_raw.split(","):
        item = item.strip()
        if "=" in item:
            key, val = item.split("=", 1)
            options[key.strip()] = val.strip()
        elif item:
            options[item] = "true"
    return {"name": str(match.group(2)).strip(), "options_raw": options_raw, "options": options}


def _parse_packages(content: str) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for match in re.finditer(
        r"\\(?:usepackage|RequirePackage)(?:\[([^\]]*)\])?\{([^}]+)\}",
        content,
    ):
        pkg_names = [name.strip() for name in match.group(2).split(",") if name.strip()]
        options = str(match.group(1) or "").strip()
        for name in pkg_names:
            packages.append({"name": name, "options": options})
    return packages


def _parse_custom_commands(content: str) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    patterns = [
        (r"\\newcommand\*?\{([^}]+)\}(?:\[(\d+)\])?(?:\{([^}]*)\})?", "newcommand"),
        (r"\\renewcommand\*?\{([^}]+)\}(?:\[(\d+)\])?(?:\{([^}]*)\})?", "renewcommand"),
        (r"\\def\s*(\\(?:[a-zA-Z]+|@[a-zA-Z]+))", "def"),
        (r"\\newenvironment\{([^}]+)\}(?:\[(\d+)\])?", "newenvironment"),
        (r"\\newtheorem\{([^}]+)\}", "newtheorem"),
    ]
    for pattern, kind in patterns:
        for match in re.finditer(pattern, content):
            commands.append(
                {
                    "kind": kind,
                    "name": str(match.group(1) or "").strip(),
                    "args": str(match.group(2) or "").strip() if match.lastindex and match.lastindex >= 2 else "",
                }
            )
    return commands


def _parse_section_hierarchy(content: str, doc_class: str) -> dict[str, Any]:
    text = str(content or "")
    top_level = "chapter" if doc_class in {"book", "ctexbook", "report", "ctexrep", "hithesisbook"} else "section"
    sections: dict[str, list[str]] = {}
    section_pattern = re.compile(r"\\(chapter|section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}")
    for match in section_pattern.finditer(text):
        level = match.group(1)
        title = str(match.group(2) or "").strip()
        if level not in sections:
            sections[level] = []
        if title not in sections[level]:
            sections[level].append(title)
    uses_part = bool(re.search(r"\\part\*?\{", text))
    uses_appendix = bool(re.search(r"\\(?:appendix|begin\{appendix\*?s?\})", text))

    # Capture abstract as a pseudo-section (article templates use \begin{abstract})
    has_abstract_env = bool(re.search(r"\\begin\{abstract", text))
    frontmatter_titles: list[str] = []
    if has_abstract_env and "Abstract" not in sections.get(top_level, []) and "Abstract" not in sections.get("section", []):
        frontmatter_titles.append("Abstract")

    return {
        "top_level": top_level,
        "levels_found": list(sections.keys()),
        "titles": {level: titles[:20] for level, titles in sections.items()},
        "frontmatter_titles": frontmatter_titles,
        "uses_part": uses_part,
        "uses_appendix": uses_appendix,
    }


def _parse_input_structure(content: str) -> list[str]:
    inputs: list[str] = []
    for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", content):
        name = str(match.group(1) or "").strip()
        inputs.append(name)
    return inputs


def _parse_bibliography_system(content: str, files: list[str], project_dir: Path) -> dict[str, Any]:
    text = str(content or "")

    # Backend detection
    backend = ""
    if re.search(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{biblatex\}", text):
        backend = "biblatex"
    elif re.search(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{natbib\}", text):
        backend = "natbib"
    elif re.search(r"\\bibliographystyle\{", text) or re.search(r"\\bibliography\{", text):
        backend = "bibtex"

    # Citation commands
    cite_pattern = re.compile(
        r"\\(footcitetext|parencite|textcite|autocite|smartcite|footcite|"
        r"citep|citet|citeauthor|citeyearpar|citeyear|cite)\*?"
    )
    cite_commands: list[str] = []
    seen: set[str] = set()
    for match in cite_pattern.finditer(text):
        cmd = match.group(1)
        if cmd not in seen:
            seen.add(cmd)
            cite_commands.append(cmd)

    # Bib files from commands + file list
    bib_files: list[str] = []
    for match in re.finditer(r"\\addbibresource(?:\[[^\]]*\])?\{([^}]+)\}", text):
        for item in match.group(1).split(","):
            bib_files.append(item.strip())
    for match in re.finditer(r"\\bibliography\{([^}]+)\}", text):
        for item in match.group(1).split(","):
            bib_files.append(item.strip())
    if not bib_files:
        bib_files = [f for f in files if f.lower().endswith(".bib")]

    # Bibstyle
    bibstyle = ""
    style_match = re.search(r"\\bibliographystyle\{([^}]+)\}", text)
    if style_match:
        bibstyle = str(style_match.group(1)).strip()

    # Prefer richer commands
    preferred = "cite"
    preferred_order = ["parencite", "autocite", "citep", "textcite", "smartcite", "cite"]
    for cmd in preferred_order:
        if cmd in cite_commands:
            preferred = cmd
            break

    tail = ""
    if backend == "biblatex":
        tail_match = re.search(r"(\\printbibliography\b.*)", text, flags=re.S)
        if tail_match:
            tail = tail_match.group(1).strip()
    else:
        style_m = re.search(r"\\(bibliographystyle\{[^}]+\})", text)
        bib_m = re.search(r"\\(bibliography\{[^}]+\})", text)
        if style_m and bib_m:
            tail = (style_m.group(0) + "\n" + bib_m.group(0)).strip()
        elif bib_m:
            tail = bib_m.group(0).strip()

    return {
        "backend": backend,
        "cite_commands": cite_commands,
        "preferred_cite_command": preferred,
        "bib_files": bib_files,
        "bibstyle": bibstyle,
        "tail": tail,
    }


def _parse_frontmatter(content: str, doc_class: str) -> dict[str, Any]:
    text = str(content or "")
    return {
        "has_title": bool(re.search(r"\\title\{", text)),
        "has_author": bool(re.search(r"\\author\{", text)),
        "has_date": bool(re.search(r"\\date\{", text)),
        "has_abstract": bool(re.search(r"\\begin\{abstract", text)),
        "has_keywords": bool(re.search(r"\\(?:keywords|IEEEkeywords)\{", text)),
        "has_toc": bool(re.search(r"\\tableofcontents", text)),
        "has_lof": bool(re.search(r"\\listoffigures", text)),
        "has_lot": bool(re.search(r"\\listoftables", text)),
        "has_maketitle": bool(re.search(r"\\maketitle", text)),
        "has_frontmatter_switch": bool(re.search(r"\\frontmatter\b", text)),
        "has_dedication": bool(re.search(r"\\dedication\{", text)),
        "has_acknowledgment": bool(
            re.search(r"\\(?:acknowledg?ments?|acks?)\{", text, flags=re.I)
            or "致谢" in text
            or "Acknowledg" in text
        ),
    }


def _parse_mainmatter(content: str) -> dict[str, Any]:
    text = str(content or "")
    return {
        "has_mainmatter_switch": bool(re.search(r"\\mainmatter\b", text)),
        "section_count": len(re.findall(r"\\(?:chapter|section)\*?\{", text)),
        "subsection_count": len(re.findall(r"\\subsection\*?\{", text)),
    }


def _parse_backmatter(content: str, doc_class: str) -> dict[str, Any]:
    text = str(content or "")
    return {
        "has_backmatter_switch": bool(re.search(r"\\backmatter\b", text)),
        "has_appendix": bool(re.search(r"\\(?:appendix|begin\{appendix\*?s?\})", text)),
        "has_bibliography": bool(
            re.search(r"\\(?:bibliography\{|printbibliography\b|bibliographystyle\{)", text)
        ),
        "has_index": bool(re.search(r"\\printindex", text)),
    }


def _parse_float_conventions(content: str) -> dict[str, Any]:
    text = str(content or "")
    figure_placements = re.findall(r"\\begin\{figure\*?\}(?:\[([^\]]*)\])?", text)
    table_placements = re.findall(r"\\begin\{table\*?\}(?:\[([^\]]*)\])?", text)
    placements = figure_placements + table_placements
    default = "htbp" if not placements else "htbp"
    return {
        "uses_figure_star": bool(re.search(r"\\begin\{figure\*\}", text)),
        "uses_table_star": bool(re.search(r"\\begin\{table\*\}", text)),
        "common_placement": max(set(placements), key=placements.count) if placements else default,
        "figure_count": len(re.findall(r"\\begin\{figure\*?\}", text)),
        "table_count": len(re.findall(r"\\begin\{table\*?\}", text)),
        "uses_subfigure": bool(
            re.search(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{sub(?:figure|caption)\}", text)
        ),
    }


def _parse_math_environments(content: str) -> dict[str, Any]:
    text = str(content or "")
    envs_found: list[str] = []
    math_environments = {
        "display": ["equation", "align", "alignat", "gather", "multline", "flalign", "eqnarray"],
        "theorem": ["theorem", "lemma", "corollary", "proposition", "definition", "remark", "example", "proof", "conjecture", "assumption", "claim"],
    }
    for category, env_names in math_environments.items():
        for env in env_names:
            if re.search(rf"\\begin\{{{env}\*?\}}", text):
                envs_found.append(env)
    return {
        "environments_found": envs_found,
        "uses_amsmath": bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{amsmath\}", text)),
        "uses_amssymb": bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{amssymb\}", text)),
        "uses_mathtools": bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{mathtools\}", text)),
        "inline_math_dollar": bool(re.search(r"[^\\]\$[^$]+\$", text)),
        "display_math_bracket": bool(re.search(r"\\\[.*?\\\]", text, flags=re.S)),
    }


def _parse_cross_ref_style(content: str) -> str:
    text = str(content or "")
    if re.search(r"\\usepackage(?:\[[^\]]*\])?\{cleveref\}", text):
        return "cleveref"
    if re.search(r"\\usepackage(?:\[[^\]]*\])?\{hyperref\}", text):
        if re.search(r"\\autoref\{", text):
            return "autoref"
    return "ref"


def _detect_language(content: str, doc_class: str) -> str:
    if doc_class in {"ctexart", "ctexrep", "ctexbook", "hithesisbook", "hithesisart", "hithesisartplus"}:
        return "zh"
    text = str(content or "")
    chinese = len(re.findall(r"[一-鿿]", text))
    english = len(re.findall(r"[A-Za-z]{4,}", text))
    if chinese >= 24 and chinese >= english:
        return "zh"
    return "en"


def _parse_mainmatter_chapter_titles(content: str) -> list[str]:
    """Extract chapter titles from \\mainmatter .. \\backmatter section of the main tex."""
    text = str(content or "")
    mainmatter_start = text.find(r"\mainmatter")
    if mainmatter_start < 0:
        return []
    mainmatter_content = text[mainmatter_start:]
    backmatter_start = mainmatter_content.find(r"\backmatter")
    if backmatter_start >= 0:
        mainmatter_content = mainmatter_content[:backmatter_start]
    titles: list[str] = []
    for match in re.finditer(r"\\chapter\*?\{([^}]*)\}", mainmatter_content):
        title = str(match.group(1) or "").strip()
        if title:
            titles.append(title)
    return titles


def _parse_page_geometry(content: str) -> dict[str, Any]:
    text = str(content or "")
    geo_match = re.search(r"\\usepackage(?:\[([^\]]*)\])?\{geometry\}", text)
    a4 = bool(re.search(r"a4paper", text, flags=re.I))
    twocolumn = bool(re.search(r"twocolumn", text))
    font_size = "11pt"
    size_match = re.search(r"(?:^|[^\w])(1[0-2]pt)\b", text)
    if size_match:
        font_size = size_match.group(1)
    return {
        "a4paper": a4,
        "twocolumn": twocolumn,
        "font_size": font_size,
        "geometry_configured": geo_match is not None,
    }


def _build_bibliography_instruction(profile: dict[str, Any]) -> str:
    bib = profile.get("bibliography", {})
    backend = str(bib.get("backend") or "")
    cite_commands = bib.get("cite_commands") or []
    preferred = str(bib.get("preferred_cite_command") or "cite")
    bib_files = bib.get("bib_files") or []
    cite_text = ", ".join(f"\\{c}" for c in cite_commands[:6]) if cite_commands else "未检测到"
    bib_text = ", ".join(bib_files[:4]) if bib_files else "未检测到"

    if backend == "biblatex":
        return (
            f"参考文献系统：biblatex。请沿用 \\addbibresource / \\printbibliography。"
            f"已检测命令：{cite_text}。新增引用默认使用 \\{preferred}。"
            f"不要把原模板里的引用命令重写成 \\cite。"
            f"Bib 文件：{bib_text}。"
        )
    if backend in {"natbib", "bibtex"}:
        return (
            f"参考文献系统：{backend}。请沿用 \\bibliographystyle / \\bibliography。"
            f"已检测命令：{cite_text}。新增引用默认使用 \\{preferred}。"
            f"不要把原模板里的引用命令重写成 \\cite。"
            f"Bib 文件：{bib_text}。"
        )
    if cite_commands or bib_files:
        return (
            f"已检测引用命令（{cite_text}）和 Bib 文件（{bib_text}）。"
            f"新增引用请使用 \\{preferred}。"
        )
    return "未检测到明确的参考文献系统。"


# ── Public API ──


def build_template_profile(
    project_id: str,
    main_tex: str = "",
    template_id: str = "",
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a comprehensive template fingerprint from project source files.

    Returns a structured dictionary describing every aspect of the LaTeX template
    that the LLM needs to understand before generating or modifying content.
    """
    if project_dir is None:
        from .research_workflow import DEFAULT_OUTPUT_DIR
        project_dir = DEFAULT_OUTPUT_DIR / project_id

    files = _relative_files(project_dir)
    if not main_tex:
        # pick the most likely main file
        tex_files = [f for f in files if f.lower().endswith(".tex")]
        main_tex = next(
            (f for f in tex_files if r"\documentclass" in (_read_file(project_dir, f) or "")),
            DEFAULT_MAIN_TEX if hasattr(DEFAULT_MAIN_TEX, "__iter__") else "main.tex",
        )

    tex_content = _read_file(project_dir, main_tex) or ""

    # Aggregate content from all tex files for global analysis
    all_tex_content = tex_content
    for f in files:
        if f != main_tex and f.lower().endswith(".tex") and not _looks_like_instruction(f):
            body = _read_file(project_dir, f)
            if body:
                all_tex_content += "\n" + body

    doc_class_info = _parse_document_class(tex_content)
    packages = _parse_packages(all_tex_content)
    language = _detect_language(all_tex_content, doc_class_info["name"])
    bibliography = _parse_bibliography_system(all_tex_content, files, project_dir)
    section_hierarchy = _parse_section_hierarchy(all_tex_content, doc_class_info["name"])
    section_hierarchy["mainmatter_chapter_titles"] = _parse_mainmatter_chapter_titles(tex_content)
    input_structure = _parse_input_structure(tex_content)
    custom_commands = _parse_custom_commands(all_tex_content)
    float_conventions = _parse_float_conventions(all_tex_content)
    math_envs = _parse_math_environments(all_tex_content)
    cross_ref_style = _parse_cross_ref_style(all_tex_content)
    page_geometry = _parse_page_geometry(tex_content)
    frontmatter = _parse_frontmatter(tex_content, doc_class_info["name"])
    mainmatter = _parse_mainmatter(tex_content)
    backmatter = _parse_backmatter(tex_content, doc_class_info["name"])

    # Preamble bounds
    preamble = ""
    body_start = tex_content.find(r"\begin{document}")
    if body_start >= 0:
        preamble = tex_content[:body_start].strip()

    # Detect template name
    template_name = "手动上传项目"
    if template_id:
        try:
            template_name = str(get_template(template_id).get("name") or template_id)
        except Exception:
            template_name = template_id

    # Collect source excerpts so the LLM can read the actual template content
    source_excerpts: list[dict[str, str]] = []
    main_excerpt = tex_content[:3000] if len(tex_content) > 3000 else tex_content
    source_excerpts.append({"path": main_tex, "excerpt": main_excerpt})
    for f in files[:6]:
        if f == main_tex or not f.lower().endswith(".tex") or _looks_like_instruction(f):
            continue
        body = _read_file(project_dir, f)
        if body:
            source_excerpts.append({"path": f, "excerpt": body[:2000] if len(body) > 2000 else body})

    profile: dict[str, Any] = {
        "template_id": template_id,
        "template_name": template_name,
        "main_tex": main_tex,
        "files": files,
        "document_class": doc_class_info,
        "language": language,
        "page_geometry": page_geometry,
        "packages": packages,
        "preamble_length": len(preamble),
        "section_hierarchy": section_hierarchy,
        "input_structure": input_structure,
        "bibliography": bibliography,
        "frontmatter": frontmatter,
        "mainmatter": mainmatter,
        "backmatter": backmatter,
        "float_conventions": float_conventions,
        "math_environments": math_envs,
        "cross_ref_style": cross_ref_style,
        "custom_commands": [cmd for cmd in custom_commands if cmd["kind"] != "def"][:60],
        "custom_command_count": len(custom_commands),
        "bibliography_instruction": _build_bibliography_instruction({"bibliography": bibliography}),
        "source_excerpts": source_excerpts,
    }
    return profile


def template_comprehension_prompt(profile: dict[str, Any]) -> str:
    """Generate a structured prompt section that tells the LLM exactly what
    template it is working with and what constraints it must obey."""

    doc = profile.get("document_class", {})
    geo = profile.get("page_geometry", {})
    sec = profile.get("section_hierarchy", {})
    bib = profile.get("bibliography", {})
    fm = profile.get("frontmatter", {})
    bm = profile.get("backmatter", {})
    fl = profile.get("float_conventions", {})
    math_envs = profile.get("math_environments", {})
    custom_cmds = profile.get("custom_commands") or []

    package_names = [p["name"] for p in (profile.get("packages") or [])]
    input_files = profile.get("input_structure") or []

    parts: list[str] = [
        "## 模板理解",
        "",
        f"**模板名称**: {profile.get('template_name', '')}",
        f"**文档类**: \\documentclass[{doc.get('options_raw', '')}]{{{doc.get('name', 'article')}}}",
        f"**目标语言**: {'中文' if profile.get('language') == 'zh' else 'English'}",
        f"**页面**: {'A4' if geo.get('a4paper') else 'letter'}, {geo.get('font_size', '11pt')}{', 双栏' if geo.get('twocolumn') else ''}",
        "",
        "### 包依赖",
        ", ".join(package_names[:30]) if package_names else "未检测到",
        "",
        "### 章节层级",
        f"顶层：{sec.get('top_level', 'section')}，检测到的层级：{', '.join(sec.get('levels_found', []))}",
    ]

    fm_titles = sec.get("frontmatter_titles") or []
    if fm_titles:
        parts.append(f"- 前导区: {' / '.join(fm_titles)}")

    titles = sec.get("titles") or {}
    for level, items in titles.items():
        if items:
            parts.append(f"- {level}: {' / '.join(items[:10])}")

    if not fm_titles and not titles:
        parts.append("- **未检测到预定义章节**（模板可能为空白框架，需要你根据文档类和惯例推断合理章节）")

    parts.append("")
    parts.append("### 导言区（必须保留）")
    parts.append(f"- \\maketitle: {'是' if fm.get('has_maketitle') else '否'}")
    parts.append(f"- 摘要: {'是' if fm.get('has_abstract') else '否'}")
    parts.append(f"- 关键词: {'是' if fm.get('has_keywords') else '否'}")
    if fm.get("has_toc"):
        parts.append("- \\tableofcontents: 是")
    if fm.get("has_frontmatter_switch"):
        parts.append("- \\frontmatter / \\mainmatter 结构: 是")

    parts.append("")
    parts.append("### 正文")
    parts.append(f"- 章节数: {sec.get('section_count', 0)}，子节数: {sec.get('subsection_count', 0)}")

    parts.append("")
    parts.append("### 尾区（必须保留）")
    if bm.get("has_appendix"):
        parts.append("- 附录: 是")
    parts.append(f"- 参考文献: {'是' if bm.get('has_bibliography') else '否'}")

    parts.append("")
    parts.append("### 参考文献系统")
    parts.append(profile.get("bibliography_instruction", ""))

    parts.append("")
    parts.append("### 图表约定")
    parts.append(f"- 常用浮动位置: {fl.get('common_placement', 'htbp')}")
    if fl.get("uses_figure_star"):
        parts.append("- 使用 figure* (通栏图)，适用于双栏模板")
    if fl.get("uses_subfigure"):
        parts.append("- 使用 subfigure 子图包")

    parts.append("")
    parts.append("### 数学环境")
    envs_found = math_envs.get("environments_found") or []
    if envs_found:
        parts.append(f"- 已使用的环境: {', '.join(envs_found[:20])}")
    if math_envs.get("uses_amsmath"):
        parts.append("- amsmath 已加载")

    parts.append("")
    parts.append("### 交叉引用")
    parts.append(f"- 引用风格: \\{profile.get('cross_ref_style', 'ref')}")

    if input_files:
        parts.append("")
        parts.append("### 文件组织")
        parts.append(f"- \\input/\\include 文件: {', '.join(input_files[:15])}")

    if custom_cmds:
        parts.append("")
        parts.append("### 自定义命令（选择）")
        for cmd in custom_cmds[:15]:
            parts.append(f"- \\{cmd['name']} ({cmd['kind']})")

    source_excerpts = profile.get("source_excerpts") or []
    if source_excerpts:
        parts.append("")
        parts.append("### 模板源文摘录（仔细阅读以理解模板结构）")
        for item in source_excerpts[:4]:
            excerpt = str(item.get("excerpt") or "")
            if excerpt:
                parts.append(f"**文件 `{item['path']}`**:")
                parts.append("```tex")
                parts.append(excerpt[:2500])
                parts.append("```")

    parts.append("")
    parts.append("### 硬性约束")
    parts.append("1. **不得修改** documentclass、导言区、\\maketitle 区域和参考文献尾部结构。")
    parts.append("2. **引用命令必须沿用**模板已有命令，不得随意切换（如 \\citep → \\cite）。")
    parts.append("3. **新增内容只允许**放在正文区域（\\begin{document} 之后、参考文献区之前）。")
    parts.append("4. **图表标签**必须使用 \\label 且 key 全局唯一。")
    if profile.get("language") == "zh":
        parts.append("5. 中文正文使用 　　（两个全角空格）缩进段首，保持中文排版习惯。")
    else:
        parts.append("5. 英文正文保持学术语体，段落间留空行。")

    return "\n".join(parts)


# ── Internal helpers ──


def _read_file(project_dir: Path, rel_path: str) -> str | None:
    try:
        path = _project_file_path(project_dir, rel_path)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    return None


def _looks_like_instruction(rel_path: str) -> bool:
    lowered = str(rel_path or "").lower()
    return any(
        token in lowered
        for token in [
            "formatting-instructions",
            "instructions",
            "example",
            "guide",
            "template",
            "copyright",
        ]
    )


DEFAULT_MAIN_TEX = "main.tex"
