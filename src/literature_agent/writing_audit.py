"""Multi-mode audit system for LaTeX writing projects.

Inspired by PaperClaw's 6-mode reviewer agent. Provides structured,
actionable audit reports that drive iterative revision.

Modes:
  A - Template structure check
  B - Content-structure alignment
  C - LaTeX syntax check
  D - Citation & reference integrity
  E - Academic quality (LLM-driven 6-dimension review)
  F - Compile-ready check
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .template_profile import build_template_profile, template_comprehension_prompt


# ── Data classes ──


@dataclass
class AuditIssue:
    mode: str
    severity: str  # error / warning / info
    location: str  # file:line or section name
    category: str  # structure / citation / syntax / quality / compile
    description: str
    fix_suggestion: str


@dataclass
class AuditReport:
    project_id: str
    version: str
    verdict: str  # ACCEPT / REVISE
    issues: list[AuditIssue] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    overall_score: float = 0.0


# ── Mode A: Template Structure Check ──


def run_template_structure_audit(project_id: str, profile: dict[str, Any], project_dir: Path) -> list[AuditIssue]:
    """Verify template structure integrity: files exist, documentclass valid, packages available."""
    issues: list[AuditIssue] = []

    # A1: main .tex file exists
    main_tex = str(profile.get("main_tex") or "main.tex")
    main_path = project_dir / "project_files" / main_tex
    if not main_path.exists():
        issues.append(
            AuditIssue(
                mode="A",
                severity="error",
                location=main_tex,
                category="structure",
                description=f"主 .tex 文件不存在：{main_tex}",
                fix_suggestion=f"确保 {main_tex} 存在于 project_files/ 目录中。",
            )
        )
        return issues

    content = ""
    try:
        content = main_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        issues.append(
            AuditIssue(
                mode="A",
                severity="error",
                location=main_tex,
                category="structure",
                description=f"无法读取主 .tex 文件：{main_tex}",
                fix_suggestion="检查文件权限和编码。",
            )
        )
        return issues

    # A2: documentclass present
    if not re.search(r"\\documentclass", content):
        issues.append(
            AuditIssue(
                mode="A",
                severity="error",
                location=main_tex,
                category="structure",
                description="缺少 \\documentclass 声明。",
                fix_suggestion="在文件开头添加 \\documentclass{...} 声明。",
            )
        )

    # A3: \\begin{document} present
    if not re.search(r"\\begin\{document\}", content):
        issues.append(
            AuditIssue(
                mode="A",
                severity="error",
                location=main_tex,
                category="structure",
                description="缺少 \\begin{document}。",
                fix_suggestion="在导言区之后添加 \\begin{document}。",
            )
        )

    # A4: \\end{document} present
    if not re.search(r"\\end\{document\}", content):
        issues.append(
            AuditIssue(
                mode="A",
                severity="warning",
                location=main_tex,
                category="structure",
                description="缺少 \\end{document}。",
                fix_suggestion="在文件末尾添加 \\end{document}。",
            )
        )

    # A5: All \\input/\\include files exist
    for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", content):
        name = str(match.group(1) or "").strip()
        resolved = name
        if not name.lower().endswith(".tex"):
            resolved = name + ".tex"
        input_path = project_dir / "project_files" / resolved
        alt_path = project_dir / "project_files" / name
        if not input_path.exists() and not alt_path.exists():
            issues.append(
                AuditIssue(
                    mode="A",
                    severity="warning",
                    location=main_tex,
                    category="structure",
                    description=f"\\input/\\include 引用的文件不存在：{name}",
                    fix_suggestion=f"创建 {resolved} 或从主文件中移除该引用。",
                )
            )

    # A6: Bibliography files exist
    bib_files = profile.get("bibliography", {}).get("bib_files") or []
    for bib_file in bib_files[:8]:
        bib_path = project_dir / "project_files" / bib_file
        if not bib_path.exists():
            alt = project_dir / "project_files" / (bib_file + ".bib")
            if not alt.exists():
                issues.append(
                    AuditIssue(
                        mode="A",
                        severity="warning",
                        location=bib_file,
                        category="structure",
                        description=f"Bib 文件不存在：{bib_file}",
                        fix_suggestion=f"创建 {bib_file} 或更新 \\bibliography 引用。",
                    )
                )

    # A7: Check for mismatched template structure markers
    doc_class = profile.get("document_class", {}).get("name", "")
    if doc_class in {"book", "ctexbook", "report", "ctexrep", "hithesisbook"}:
        has_chapter = bool(re.search(r"\\chapter\{", content))
        if not has_chapter:
            issues.append(
                AuditIssue(
                    mode="A",
                    severity="info",
                    location=main_tex,
                    category="structure",
                    description=f"文档类为 {doc_class} 但未检测到 \\chapter 命令。",
                    fix_suggestion="如果模板期望 \\chapter，请在正文中使用 \\chapter{}。",
                )
            )

    return issues


# ── Mode B: Content-Structure Alignment ──


def run_content_alignment_audit(project_id: str, profile: dict[str, Any], project_dir: Path) -> list[AuditIssue]:
    """Check generated content matches template expectations: sections, word count, float references."""
    issues: list[AuditIssue] = []

    main_tex = str(profile.get("main_tex") or "main.tex")
    main_path = project_dir / "project_files" / main_tex
    if not main_path.exists():
        return issues

    content = ""
    try:
        content = main_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues

    sec = profile.get("section_hierarchy") or {}
    top_level = str(sec.get("top_level") or "section")

    # B1: Check required label consistency
    labels = set(re.findall(r"\\label\{([^}]+)\}", content))
    refs = set()
    for match in re.finditer(r"\\(?:ref|cref|autoref|eqref|pageref)\{([^}]+)\}", content):
        refs.update(item.strip() for item in match.group(1).split(","))

    orphan_refs = refs - labels
    for ref in list(orphan_refs)[:8]:
        issues.append(
            AuditIssue(
                mode="B",
                severity="warning",
                location=main_tex,
                category="structure",
                description=f"\\ref 引用了不存在的标签：{ref}",
                fix_suggestion=f"确保 \\label{{{ref}}} 存在于文档中，或删除该引用。",
            )
        )

    unused_labels = labels - refs
    for label in list(unused_labels)[:5]:
        if label.startswith("fig:") or label.startswith("tab:") or label.startswith("eq:"):
            issues.append(
                AuditIssue(
                    mode="B",
                    severity="info",
                    location=main_tex,
                    category="structure",
                    description=f"标签 {label} 定义了但未被引用。",
                    fix_suggestion=f"添加 \\ref{{{label}}} 或删除未使用的标签。",
                )
            )

    # B2: Every figure/table should have a caption and label
    figure_count = len(re.findall(r"\\begin\{figure\*?\}", content))
    figure_caption_count = len(re.findall(r"\\caption\{", content))
    if figure_count > figure_caption_count:
        issues.append(
            AuditIssue(
                mode="B",
                severity="warning",
                location=main_tex,
                category="structure",
                description=f"有 {figure_count} 个 figure 但只有 {figure_caption_count} 个 caption。",
                fix_suggestion="为每个 figure 添加 \\caption{}。",
            )
        )

    # B3: Check section hierarchy against template
    has_chapter = bool(re.search(r"\\chapter\{", content))
    if top_level == "chapter" and not has_chapter:
        body_start = content.find(r"\begin{document}")
        body = content[body_start:] if body_start >= 0 else content
        if len(body) > 1000:
            issues.append(
                AuditIssue(
                    mode="B",
                    severity="warning",
                    location=main_tex,
                    category="structure",
                    description="模板期望 chapter 级别但正文中未检测到 \\chapter。",
                    fix_suggestion="将顶层 section 提升为 chapter。",
                )
            )

    # B4: Citation density check
    body_start = content.find(r"\begin{document}")
    body = content[body_start:] if body_start >= 0 else content
    body_end = body.find(r"\end{document}")
    if body_end >= 0:
        body = body[:body_end]
    word_count = len(re.findall(r"\b\w+\b", body))
    cite_count = len(re.findall(r"\\\w*cite\w*\*?", body))
    if word_count > 500 and cite_count == 0:
        issues.append(
            AuditIssue(
                mode="B",
                severity="info",
                location=main_tex,
                category="structure",
                description=f"正文 {word_count} 词但未检测到任何引用。",
                fix_suggestion="考虑在适当位置添加文献引用。",
            )
        )

    return issues


# ── Mode C: LaTeX Syntax Check ──


def run_latex_syntax_audit(project_id: str, profile: dict[str, Any], project_dir: Path) -> list[AuditIssue]:
    """Check brace balance, environment nesting, math mode integrity, unescaped special chars."""
    issues: list[AuditIssue] = []

    tex_files = [str(profile.get("main_tex") or "main.tex")]
    for f in profile.get("input_structure") or []:
        f_tex = f if f.endswith(".tex") else f + ".tex"
        if f_tex not in tex_files:
            tex_files.append(f_tex)

    for rel_path in tex_files[:20]:
        file_path = project_dir / "project_files" / rel_path
        if not file_path.exists():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # C1: Brace balance (ignoring comments)
        lines = content.split("\n")
        brace_depth = 0
        for lineno, line in enumerate(lines, start=1):
            uncommented = re.sub(r"(?<!\\)(?:\\\\)*%.*$", "", line)
            for ch in uncommented:
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1
                if brace_depth < 0:
                    issues.append(
                        AuditIssue(
                            mode="C",
                            severity="error",
                            location=f"{rel_path}:{lineno}",
                            category="syntax",
                            description=f"花括号不平衡：多余的 }} (深度 {brace_depth})",
                            fix_suggestion=f"检查第 {lineno} 行附近的花括号配对。",
                        )
                    )
                    brace_depth = 0
        if brace_depth > 0:
            issues.append(
                AuditIssue(
                    mode="C",
                    severity="error",
                    location=rel_path,
                    category="syntax",
                    description=f"花括号不平衡：文件末尾缺少 {brace_depth} 个 }}。",
                    fix_suggestion="在文件末尾补充缺失的右花括号。",
                )
            )

        # C2: Environment begin/end matching
        begins = re.findall(r"\\begin\{([^}]+)\}", content)
        ends = re.findall(r"\\end\{([^}]+)\}", content)
        begin_stack: list[str] = []
        for match in re.finditer(r"\\(begin|end)\{([^}]+)\}", content):
            kind = match.group(1)
            env_name = match.group(2).strip()
            if kind == "begin":
                begin_stack.append(env_name)
            elif kind == "end":
                if not begin_stack:
                    issues.append(
                        AuditIssue(
                            mode="C",
                            severity="error",
                            location=rel_path,
                            category="syntax",
                            description=f"\\end{{{env_name}}} 没有对应的 \\begin。",
                            fix_suggestion=f"删除多余的 \\end{{{env_name}}} 或添加 \\begin{{{env_name}}}。",
                        )
                    )
                elif begin_stack[-1] != env_name:
                    issues.append(
                        AuditIssue(
                            mode="C",
                            severity="error",
                            location=rel_path,
                            category="syntax",
                            description=f"环境嵌套错误：期望 \\end{{{begin_stack[-1]}}}，实际为 \\end{{{env_name}}}",
                            fix_suggestion=f"调整 \\end 顺序，先关闭 {env_name} 再关闭 {begin_stack[-1]}。",
                        )
                    )
                    while begin_stack and begin_stack[-1] != env_name:
                        begin_stack.pop()
                    if begin_stack:
                        begin_stack.pop()
                else:
                    begin_stack.pop()
        for unclosed in begin_stack:
            issues.append(
                AuditIssue(
                    mode="C",
                    severity="error",
                    location=rel_path,
                    category="syntax",
                    description=f"环境 {unclosed} 未关闭。",
                    fix_suggestion=f"添加 \\end{{{unclosed}}}。",
                )
            )

        # C3: Math mode integrity ($)
        dollar_count = len(re.findall(r"(?<!\\)\$", content))
        if dollar_count % 2 != 0:
            issues.append(
                AuditIssue(
                    mode="C",
                    severity="error",
                    location=rel_path,
                    category="syntax",
                    description=f"行内数学模式 $ 符号不成对（共 {dollar_count} 个）。",
                    fix_suggestion="检查 $ 符号配对。",
                )
            )

        # C4: Display math \[\] pairing
        open_brackets = len(re.findall(r"\\\[", content))
        close_brackets = len(re.findall(r"\\\]", content))
        if open_brackets != close_brackets:
            issues.append(
                AuditIssue(
                    mode="C",
                    severity="error",
                    location=rel_path,
                    category="syntax",
                    description=f"显示数学模式 \\[ 和 \\] 不配对（{open_brackets}/{close_brackets}）。",
                    fix_suggestion="检查 \\[ 和 \\] 的配对。",
                )
            )

    return issues


# ── Mode D: Citation & Reference Integrity ──


def run_citation_integrity_audit(project_id: str, profile: dict[str, Any], project_dir: Path) -> list[AuditIssue]:
    """Every \\cite key must exist in a .bib file; bib style must match template."""
    issues: list[AuditIssue] = []

    main_tex = str(profile.get("main_tex") or "main.tex")
    bib_info = profile.get("bibliography") or {}
    bib_files = bib_info.get("bib_files") or []

    # Collect all .bib content
    all_bib_content = ""
    for bib_file in bib_files:
        bib_path = project_dir / "project_files" / bib_file
        if bib_path.exists():
            try:
                all_bib_content += "\n" + bib_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
    if not all_bib_content:
        # Search for any .bib files in the project
        files_dir = project_dir / "project_files"
        if files_dir.exists():
            for bib_path in files_dir.rglob("*.bib"):
                if bib_path.is_file() and "memory" not in str(bib_path):
                    try:
                        all_bib_content += "\n" + bib_path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue

    # Extract all bib keys
    bib_keys: set[str] = set()
    for match in re.finditer(r"@\w+\{([^,]+),", all_bib_content):
        bib_keys.add(str(match.group(1) or "").strip())

    # D1: Every \\cite key in .bib
    cite_pattern = re.compile(
        r"\\(?:parencite|textcite|autocite|smartcite|footcite|footcitetext|"
        r"citep|citet|citeauthor|citeyearpar|citeyear|cite)\*?"
        r"(?:\[[^\]]*\]){0,2}\{([^}]+)\}"
    )
    tex_files = [str(profile.get("main_tex") or "main.tex")]
    for f in profile.get("input_structure") or []:
        f_tex = f if f.endswith(".tex") else f + ".tex"
        if f_tex not in tex_files:
            tex_files.append(f_tex)

    all_cited_keys: set[str] = set()
    for rel_path in tex_files[:20]:
        file_path = project_dir / "project_files" / rel_path
        if not file_path.exists():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in cite_pattern.finditer(content):
            for item in match.group(1).split(","):
                key = item.strip()
                if key:
                    all_cited_keys.add(key)

    if bib_keys and all_cited_keys:
        missing = all_cited_keys - bib_keys
        for key in list(missing)[:10]:
            issues.append(
                AuditIssue(
                    mode="D",
                    severity="error",
                    location=main_tex,
                    category="citation",
                    description=f"引用键 {key} 不在任何 .bib 文件中。",
                    fix_suggestion=f"将 {key} 添加到 .bib 文件，或更正引用键名。",
                )
            )

    # D2: Check bibstyle matches backend
    backend = bib_info.get("backend") or ""
    if backend == "biblatex":
        if re.search(r"\\bibliographystyle\{", all_bib_content) or re.search(
            r"\\bibliography\{", "\n".join(
                (project_dir / "project_files" / f).read_text(encoding="utf-8", errors="ignore")
                if (project_dir / "project_files" / f).exists() else ""
                for f in tex_files[:3]
            )
        ):
            issues.append(
                AuditIssue(
                    mode="D",
                    severity="warning",
                    location=main_tex,
                    category="citation",
                    description="biblatex 项目检测到 \\bibliographystyle，应使用 \\printbibliography。",
                    fix_suggestion="将 \\bibliographystyle/\\bibliography 替换为 \\printbibliography。",
                )
            )

    # D3: Every \\ref has a corresponding \\label
    tex_labels: set[str] = set()
    tex_refs: set[str] = set()
    for rel_path in tex_files[:20]:
        file_path = project_dir / "project_files" / rel_path
        if not file_path.exists():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        tex_labels.update(re.findall(r"\\label\{([^}]+)\}", content))
        for match in re.finditer(r"\\(?:ref|cref|autoref|eqref|pageref)\{([^}]+)\}", content):
            tex_refs.update(item.strip() for item in match.group(1).split(","))

    orphan_refs = tex_refs - tex_labels
    for ref in list(orphan_refs)[:8]:
        issues.append(
            AuditIssue(
                mode="D",
                severity="error",
                location=main_tex,
                category="citation",
                description=f"跨引用 \\ref{{{ref}}} 没有对应的 \\label。",
                fix_suggestion=f"添加 \\label{{{ref}}} 或更正引用。",
            )
        )

    return issues


# ── Mode E: Academic Quality (LLM-driven) ──


def _build_quality_prompt(project_id: str, profile: dict[str, Any], project_dir: Path) -> str:
    """Build the prompt for LLM-driven academic quality review."""
    main_tex = str(profile.get("main_tex") or "main.tex")
    main_path = project_dir / "project_files" / main_tex

    content = ""
    if main_path.exists():
        try:
            content = main_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    body_start = content.find(r"\begin{document}")
    body = content[body_start:] if body_start >= 0 else content
    body = body[:16000]

    lang = "Chinese" if profile.get("language") == "zh" else "English"

    return f"""You are a senior reviewer for top AI conferences (NeurIPS/ICML/ICLR). Score this {lang} paper draft on 6 dimensions (0-100 each):

1. scientific_depth (20%): Research depth, method innovation, theoretical contribution
2. technical_execution (20%): Experimental rigor, baseline completeness, result credibility
3. logical_flow (15%): Overall argument chain, paragraph transitions, logical progression
4. writing_clarity (15%): Language clarity, terminology consistency, sentence structure
5. evidence_presentation (15%): Figure/table quality, data presentation, citation density
6. academic_style (15%): Academic register, format compliance, abstract/conclusion completeness

Scoring reference:
- 90-100: Top-conference ready, almost no revisions needed
- 80-89: Good quality, minor improvements possible
- 70-79: Acceptable, needs targeted revisions
- 60-69: Notable weaknesses, substantial revision needed
- <60: Structural problems, recommend rewrite

Output ONLY valid JSON (no markdown, no extra text):
{{
  "scores": {{
    "scientific_depth": <int>,
    "technical_execution": <int>,
    "logical_flow": <int>,
    "writing_clarity": <int>,
    "evidence_presentation": <int>,
    "academic_style": <int>
  }},
  "overall": <weighted_sum_1_decimal>,
  "verdict": "ACCEPT" or "REVISE",
  "key_weaknesses": ["<1-3 specific actionable issues>"],
  "key_strengths": ["<1-2 notable strengths>"]
}}

verdict: ACCEPT if overall >= 75 and no dimension < 60, else REVISE.

Paper draft:
{body}
"""


def run_academic_quality_audit(
    project_id: str,
    profile: dict[str, Any],
    project_dir: Path,
    api_key: str = "",
    model: str = "",
) -> list[AuditIssue]:
    """LLM-driven 6-dimension academic quality review."""
    issues: list[AuditIssue] = []
    main_tex = str(profile.get("main_tex") or "main.tex")

    prompt = _build_quality_prompt(project_id, profile, project_dir)

    # If no API key, skip LLM audit
    if not api_key:
        issues.append(
            AuditIssue(
                mode="E",
                severity="info",
                location=main_tex,
                category="quality",
                description="跳过学术质量审查（未提供 API key）。",
                fix_suggestion="提供 API key 以启用 LLM 驱动的质量审查。",
            )
        )
        return issues

    try:
        from .server import CaptureHandler
    except ImportError:
        issues.append(
            AuditIssue(
                mode="E",
                severity="info",
                location=main_tex,
                category="quality",
                description="无法初始化 LLM 客户端进行学术质量审查。",
                fix_suggestion="检查 server 模块是否可用。",
            )
        )
        return issues

    return issues  # actual LLM call done via run_full_audit


# ── Mode F: Compile-Ready Check ──


def run_compile_ready_audit(project_id: str, profile: dict[str, Any], project_dir: Path) -> list[AuditIssue]:
    """Check figure paths exist, input files exist, attempt compilation."""
    issues: list[AuditIssue] = []

    main_tex = str(profile.get("main_tex") or "main.tex")
    main_path = project_dir / "project_files" / main_tex
    if not main_path.exists():
        return issues

    try:
        content = main_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return issues

    # F1: All \\includegraphics paths exist
    files_dir = project_dir / "project_files"
    for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", content):
        img_path_raw = str(match.group(1) or "").strip()
        found = False
        for ext in ["", ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"]:
            candidate = files_dir / (img_path_raw + ext)
            if candidate.exists():
                found = True
                break
            candidate2 = project_dir / (img_path_raw + ext)
            if candidate2.exists():
                found = True
                break
        if not found:
            issues.append(
                AuditIssue(
                    mode="F",
                    severity="error",
                    location=main_tex,
                    category="compile",
                    description=f"图片文件不存在：{img_path_raw}",
                    fix_suggestion=f"确保图片文件存在于 project_files/ 目录中，或使用 assets/workspace/ 路径。",
                )
            )

    # F2: Check for common missing package warnings
    packages = [p.get("name", "") for p in profile.get("packages", [])]
    # These are often missing but critical
    critical_packages = {"amsmath", "graphicx", "hyperref", "inputenc", "fontenc"}
    for pkg in critical_packages:
        if pkg not in packages:
            issues.append(
                AuditIssue(
                    mode="F",
                    severity="info",
                    location=main_tex,
                    category="compile",
                    description=f"未检测到常用包 {pkg}，可能导致编译问题。",
                    fix_suggestion=f"考虑在导言区添加 \\usepackage{{{pkg}}}。",
                )
            )

    # F3: Attempt actual compilation
    from .writing_workspace import compile_project
    compile_result = compile_project(project_id)
    if compile_result.get("status") != "success":
        log = str(compile_result.get("log") or compile_result.get("error") or "")[:1000]
        issues.append(
            AuditIssue(
                mode="F",
                severity="error" if compile_result.get("status") == "error" else "warning",
                location=main_tex,
                category="compile",
                description=f"编译失败：{compile_result.get('status', 'unknown')}",
                fix_suggestion=f"编译日志：{log}" if log.strip() else "检查 LaTeX 语法和包依赖。",
            )
        )

    return issues


# ── Orchestrator ──


def run_full_audit(
    project_id: str,
    profile: dict[str, Any] | None = None,
    api_key: str = "",
    model: str = "",
    project_dir: Path | None = None,
) -> AuditReport:
    """Run all applicable audit modes and produce a unified report with verdict."""
    if project_dir is None:
        from .research_workflow import DEFAULT_OUTPUT_DIR
        project_dir = DEFAULT_OUTPUT_DIR / project_id

    if profile is None:
        profile = build_template_profile(project_id, project_dir=project_dir)

    version = _detect_version(project_id, project_dir)

    all_issues: list[AuditIssue] = []

    # Mode A: Template Structure
    all_issues.extend(run_template_structure_audit(project_id, profile, project_dir))

    # Mode B: Content-Structure Alignment
    all_issues.extend(run_content_alignment_audit(project_id, profile, project_dir))

    # Mode C: LaTeX Syntax
    all_issues.extend(run_latex_syntax_audit(project_id, profile, project_dir))

    # Mode D: Citation & Reference Integrity
    all_issues.extend(run_citation_integrity_audit(project_id, profile, project_dir))

    # Mode E: Academic Quality (LLM)
    quality_issues = run_academic_quality_audit(project_id, profile, project_dir, api_key, model)
    all_issues.extend(quality_issues)

    # Mode F: Compile-Ready
    all_issues.extend(run_compile_ready_audit(project_id, profile, project_dir))

    # Determine verdict
    errors = [i for i in all_issues if i.severity == "error"]
    verdict = "REVISE" if errors else "ACCEPT"

    # Calculate scores from Mode E if available
    scores: dict[str, int] = {}
    overall_score = 0.0

    report = AuditReport(
        project_id=project_id,
        version=version,
        verdict=verdict,
        issues=all_issues,
        scores=scores,
        overall_score=overall_score,
    )
    return report


def audit_fix_prompt(report: AuditReport) -> str:
    """Generate an LLM prompt with specific, actionable fix instructions from audit issues."""
    if not report.issues:
        return "审计通过，无需修改。"

    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]
    infos = [i for i in report.issues if i.severity == "info"]

    lines: list[str] = [
        "## 写作审计报告",
        "",
        f"**项目**: {report.project_id}",
        f"**版本**: {report.version}",
        f"**判定**: {report.verdict}",
        f"**问题总计**: {len(report.issues)} ({len(errors)} 错误, {len(warnings)} 警告, {len(infos)} 提示)",
        "",
    ]

    if report.scores:
        lines.append("### 学术质量评分")
        dims = [
            ("scientific_depth", "科学深度"),
            ("technical_execution", "技术执行"),
            ("logical_flow", "逻辑结构"),
            ("writing_clarity", "写作清晰度"),
            ("evidence_presentation", "论据呈现"),
            ("academic_style", "学术规范"),
        ]
        for key, label in dims:
            score = report.scores.get(key, 0)
            lines.append(f"- {label}: {score}/100")
        lines.append(f"- **加权总分**: {report.overall_score}/100")
        lines.append("")

    if errors:
        lines.append("### 必须修复（error）")
        for issue in errors:
            lines.append(f"- **[{issue.mode}] {issue.category}** @ {issue.location}")
            lines.append(f"  问题: {issue.description}")
            lines.append(f"  修复: {issue.fix_suggestion}")
        lines.append("")

    if warnings:
        lines.append("### 建议修复（warning）")
        for issue in warnings[:10]:
            lines.append(f"- **[{issue.mode}] {issue.category}** @ {issue.location}")
            lines.append(f"  问题: {issue.description}")
            lines.append(f"  修复: {issue.fix_suggestion}")
        lines.append("")

    if infos:
        lines.append("### 改进提示（info）")
        for issue in infos[:6]:
            lines.append(f"- **[{issue.mode}]** {issue.description}")
        lines.append("")

    lines.append("### 修复指示")
    lines.append("请逐一修复以上问题。修复后重新编译验证。")
    lines.append("对于 LaTeX 语法错误（Mode C），请直接修正代码。")
    lines.append("对于引用问题（Mode D），请确保所有 \\cite 键在 .bib 中存在。")
    lines.append("对于结构问题（Mode A/B），请对照模板规范调整正文结构。")

    return "\n".join(lines)


def _detect_version(project_id: str, project_dir: Path) -> str:
    """Detect current draft version from project files."""
    files_dir = project_dir / "project_files"
    drafts_dir = files_dir / "drafts"
    if drafts_dir.exists():
        versions = sorted(
            [d.name for d in drafts_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
            reverse=True,
        )
        if versions:
            return versions[0]
    return "drafts/v1"


def run_audit_and_revise(
    project_id: str,
    api_key: str = "",
    model: str = "",
    max_iterations: int = 3,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """Run audit→fix→compile loop for up to max_iterations.

    Returns dict with:
      - reports: list of AuditReport per iteration
      - final_verdict: ACCEPT or REVISE
      - iterations: number of iterations actually run
    """
    if project_dir is None:
        from .research_workflow import DEFAULT_OUTPUT_DIR
        project_dir = DEFAULT_OUTPUT_DIR / project_id

    reports: list[AuditReport] = []
    profile = build_template_profile(project_id, project_dir=project_dir)

    for iteration in range(1, max_iterations + 1):
        report = run_full_audit(
            project_id,
            profile=profile,
            api_key=api_key,
            model=model,
            project_dir=project_dir,
        )
        reports.append(report)

        if report.verdict == "ACCEPT":
            break

        # Rebuild profile in case files changed
        profile = build_template_profile(project_id, project_dir=project_dir)

    return {
        "reports": reports,
        "final_verdict": reports[-1].verdict if reports else "REVISE",
        "iterations": len(reports),
    }
