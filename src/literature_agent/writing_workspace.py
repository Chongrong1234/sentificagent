from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .chat import chat_with_kimi, load_default_model_provider, load_provider_api_key
from .config import PROJECT_ROOT, load_config
from .research_workflow import DEFAULT_OUTPUT_DIR
from .template_guardrails import (
    load_guardrails,
    resolve_section_id,
    strip_illegal_content,
)
from .template_library import download_template, get_template, get_template_structure
from .template_profile import build_template_profile
DEFAULT_MAIN_TEX = "main.tex"
PROJECT_META = "project.json"
COMPILE_META = "compile.json"
PROJECT_ARTIFACTS = "project_files"
PROJECT_MEMORY_DIR = "memory"
PROJECT_SOURCE_DIR = "sources"
WORKSPACE_IMPORT_DIR = "workspace_import"
WORKSPACE_INDEX_FILE = "workspace_index.json"
RECENT_CONTEXT_FILE = "recent_context.json"
CONVERSATION_FILE = "conversation.json"
TEXT_FILE_EXTENSIONS = {
    ".tex",
    ".cls",
    ".sty",
    ".bib",
    ".bst",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".py",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".sh",
    ".log",
}

ALGORITHM_STY_FALLBACK = r"""
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{algorithm}[2026/05/05 lightweight fallback]
\RequirePackage{float}
\floatstyle{ruled}
\newfloat{algorithm}{tbp}{loa}
\floatname{algorithm}{Algorithm}
""".strip()

ALGORITHMIC_STY_FALLBACK = r"""
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{algorithmic}[2026/05/05 lightweight fallback]
\newcounter{ALC@line}
\newenvironment{algorithmic}[1][]{
  \setcounter{ALC@line}{0}
  \begin{list}{}{\leftmargin=1.8em \itemsep=0.2em \parsep=0pt}
}{
  \end{list}
}
\newcommand{\STATE}{\item}
\newcommand{\WHILE}[1]{\item \textbf{while} #1 \textbf{do}}
\newcommand{\ENDWHILE}{\item \textbf{end while}}
\newcommand{\IF}[1]{\item \textbf{if} #1 \textbf{then}}
\newcommand{\ELSE}{\item \textbf{else}}
\newcommand{\ENDIF}{\item \textbf{end if}}
""".strip()

GBT7714_STY_FALLBACK = r"""
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{gbt7714}[2026/05/08 lightweight fallback]
\DeclareOption*{\PassOptionsToPackage{\CurrentOption}{natbib}}
\ProcessOptions\relax
\RequirePackage{natbib}
""".strip()

SIUNITX_STY_FALLBACK = r"""
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{siunitx}[2026/05/08 lightweight fallback]
\newcommand{\sisetup}[1]{}
\newcommand{\si}[1]{#1}
\newcommand{\SI}[2]{#1~#2}
\newcommand{\num}[1]{#1}
\newcommand{\qty}[2]{#1~#2}
""".strip()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _new_project_id() -> str:
    return f"project-{_utc_stamp()}"


def _project_dir(project_id: str) -> Path:
    return DEFAULT_OUTPUT_DIR / project_id


def _project_meta_path(project_id: str) -> Path:
    return _project_dir(project_id) / PROJECT_META


def _compile_meta_path(project_id: str) -> Path:
    return _project_dir(project_id) / COMPILE_META


def _files_dir(project_id: str) -> Path:
    return _project_dir(project_id) / PROJECT_ARTIFACTS


def _memory_dir(project_id: str) -> Path:
    return _files_dir(project_id) / PROJECT_MEMORY_DIR


def _sources_dir(project_id: str) -> Path:
    return _memory_dir(project_id) / PROJECT_SOURCE_DIR


def _sources_index_path(project_id: str) -> Path:
    return _sources_dir(project_id) / "index.json"


def _workspace_dir(project_id: str) -> Path:
    return _memory_dir(project_id) / WORKSPACE_IMPORT_DIR


def _workspace_index_path(project_id: str) -> Path:
    return _memory_file(project_id, WORKSPACE_INDEX_FILE)


def _pdf_path(project_id: str) -> Path:
    return _project_dir(project_id) / "manuscript.pdf"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _compiler() -> str:
    for candidate in ["xelatex", "pdflatex", "tectonic"]:
        path = shutil.which(candidate)
        if path:
            return path
    return ""


def _normalize_writing_language(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"en", "english", "en-us", "en-gb"}:
        return "en"
    return "zh"


def _manual_project_starter(
    title: str = "Untitled Project",
    author: str = "Scientific Agent",
    writing_language: str = "en",
) -> str:
    language = _normalize_writing_language(writing_language)
    if language == "zh":
        return rf"""
\documentclass[UTF8,12pt]{{ctexart}}
\usepackage[a4paper,margin=2.5cm]{{geometry}}
\usepackage{{hyperref}}
\usepackage{{graphicx}}
\usepackage{{booktabs}}
\usepackage{{amsmath,amssymb}}

\title{{{title}}}
\author{{{author}}}
\date{{\today}}

\begin{{document}}
\maketitle

\begin{{abstract}}
这里填写摘要。
\end{{abstract}}

\section{{引言}}
这里开始写作。建议先上传你自己的项目源码或现有 LaTeX 文件，再让 LLM 基于当前项目结构生成与改写。

\end{{document}}
""".strip()
    return rf"""
\documentclass[11pt]{{article}}
\usepackage[a4paper,margin=2.5cm]{{geometry}}
\usepackage{{hyperref}}
\usepackage{{graphicx}}
\usepackage{{booktabs}}
\usepackage{{amsmath,amssymb}}

\title{{{title}}}
\author{{{author}}}
\date{{\today}}

\begin{{document}}
\maketitle

\begin{{abstract}}
Write the abstract here.
\end{{abstract}}

\section{{Introduction}}
Start writing here. Upload your existing LaTeX project or source files first, then ask the assistant to draft or revise against the current project structure.

\end{{document}}
""".strip()


def _template_project_starter(
    template_id: str,
    title: str,
    author: str,
    writing_language: str = "en",
) -> str:
    try:
        from .template_library import render_template_starter

        return render_template_starter(template_id, title=title, author=author)
    except Exception:
        return _manual_project_starter(title=title, author=author, writing_language=writing_language)


def _sanitize_tex_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return (
        text.replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("~", r"\textasciitilde{}")
        .replace("^", r"\textasciicircum{}")
        .replace("\\", r"\textbackslash{}")
    )


def _replace_hitsetup_field(content: str, field: str, value: str) -> str:
    safe_value = _sanitize_tex_value(value)
    if not safe_value:
        return content
    pattern = re.compile(rf"({re.escape(field)}\s*=\s*\{{)(.*?)\}}", flags=re.S)
    return pattern.sub(lambda match: f"{match.group(1)}{safe_value}" + "}", content, count=1)


def _replace_documentclass_option(content: str, option_name: str, option_value: str) -> str:
    pattern = re.compile(r"(\\documentclass\[)([^\]]+)(\]\{[^}]+\})", flags=re.S)

    def repl(match: re.Match[str]) -> str:
        raw_options = match.group(2)
        options = [item.strip() for item in raw_options.split(",") if item.strip()]
        replaced = False
        normalized: list[str] = []
        for item in options:
            if item.startswith(f"{option_name}="):
                normalized.append(f"{option_name}={option_value}")
                replaced = True
            else:
                normalized.append(item)
        if not replaced:
            normalized.append(f"{option_name}={option_value}")
        return f"{match.group(1)}{','.join(normalized)}{match.group(3)}"

    return pattern.sub(repl, content, count=1)


def _replace_report_body(content: str, body_path: str) -> str:
    lines = content.splitlines()
    target_line = rf"\input{{{body_path}}}"
    replaced = False
    updated: list[str] = []
    for line in lines:
        if re.search(r"\\input\{body/[^}]+\}", line):
            if not replaced and not line.lstrip().startswith("%"):
                updated.append(target_line)
                replaced = True
            elif line.lstrip().startswith("%"):
                updated.append(line)
            else:
                updated.append("% " + line if not line.lstrip().startswith("%") else line)
            continue
        updated.append(line)
    if not replaced:
        return content
    return "\n".join(updated)


def _ensure_hithesis_runtime_options(main_tex_path: Path) -> None:
    if not main_tex_path.exists():
        return
    try:
        content = main_tex_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    updated = content
    kpsewhich = shutil.which("kpsewhich")
    if kpsewhich:
        has_newtxmath = subprocess.run(
            [kpsewhich, "newtxmath.sty"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
        if not has_newtxmath:
            updated = _replace_documentclass_option(updated, "newtxmath", "false")
    if updated != content:
        main_tex_path.write_text(updated, encoding="utf-8")


def _customize_hithesis_project(template: dict[str, Any], project_root: Path, title: str, author: str) -> None:
    template_id = str(template.get("id") or "")
    document_class = str(template.get("document_class") or "")
    degree_type = str(template.get("degree_type") or "")
    campus = str(template.get("campus") or "")
    stage = str(template.get("stage") or "")
    language_variant = str(template.get("language_variant") or "")

    main_tex_rel = str(template.get("main_tex") or "thesis.tex")
    main_tex_path = project_root / main_tex_rel
    if main_tex_path.exists():
        try:
            main_text = main_tex_path.read_text(encoding="utf-8", errors="ignore")
            if degree_type:
                main_text = _replace_documentclass_option(main_text, "type", degree_type)
            if campus:
                main_text = _replace_documentclass_option(main_text, "campus", campus)
            if stage:
                main_text = _replace_documentclass_option(main_text, "stage", stage)
            if language_variant == "english":
                main_text = _replace_documentclass_option(main_text, "language", "english")
            if document_class:
                main_text = re.sub(
                    r"(\\documentclass\[[^\]]+\]\{)([^}]+)(\})",
                    rf"\1{document_class}\3",
                    main_text,
                    count=1,
                )
            if stage:
                report_body = f"body/report_{campus}_{degree_type}_{stage}"
                main_text = _replace_report_body(main_text, report_body)
            main_tex_path.write_text(main_text, encoding="utf-8")
            _ensure_hithesis_runtime_options(main_tex_path)
        except OSError:
            pass

    cover_candidates = [
        project_root / "front" / "cover.tex",
        project_root / "front" / "coverart.tex",
    ]
    for cover_path in cover_candidates:
        if not cover_path.exists():
            continue
        try:
            cover = cover_path.read_text(encoding="utf-8", errors="ignore")
            cover = _replace_hitsetup_field(cover, "ctitlecover", title)
            cover = _replace_hitsetup_field(cover, "ctitle", title)
            cover = _replace_hitsetup_field(cover, "ctitleone", title[:12] or title)
            remainder = title[12:] if len(title) > 12 else title
            cover = _replace_hitsetup_field(cover, "ctitletwo", remainder or title)
            cover = _replace_hitsetup_field(cover, "etitle", title)
            cover = _replace_hitsetup_field(cover, "cauthor", author)
            cover = _replace_hitsetup_field(cover, "eauthor", author)
            cover_path.write_text(cover, encoding="utf-8")
        except OSError:
            continue

    if template_id.endswith("-en"):
        abstract_path = project_root / "front" / "cover.tex"
        if abstract_path.exists():
            try:
                cover = abstract_path.read_text(encoding="utf-8", errors="ignore")
                cover = _replace_hitsetup_field(cover, "ctitlecover", title)
                cover = _replace_hitsetup_field(cover, "ctitle", title)
                cover = _replace_hitsetup_field(cover, "etitle", title)
                cover_path = abstract_path
                cover_path.write_text(cover, encoding="utf-8")
            except OSError:
                pass


def _copy_template_entry(template_id: str, project_id: str, title: str, author: str) -> str:
    template = get_template(template_id, include_source=True)
    status = template.get("status", {}) if isinstance(template, dict) else {}
    extracted_raw = str(status.get("extracted_path") or "").strip()
    entry_root_raw = str(template.get("entry_root") or "").strip()
    main_tex = str(template.get("main_tex") or "main.tex").strip() or "main.tex"
    if not extracted_raw:
        template = download_template(template_id)
        status = template.get("status", {}) if isinstance(template, dict) else {}
        extracted_raw = str(status.get("extracted_path") or "").strip()
    if not extracted_raw or not entry_root_raw:
        return _template_project_starter(template_id, title, author)
    source_root = Path(extracted_raw) / entry_root_raw
    if not source_root.exists():
        return _template_project_starter(template_id, title, author)
    target_root = _files_dir(project_id)
    if target_root.exists():
        for item in target_root.iterdir():
            if item.name == PROJECT_MEMORY_DIR:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    shutil.copytree(source_root, target_root, dirs_exist_ok=True)
    _customize_hithesis_project(template, target_root, title, author)
    main_path = target_root / main_tex
    if main_path.exists():
        try:
            text = main_path.read_text(encoding="utf-8", errors="ignore")
            text = text.replace("__TITLE__", title).replace("__AUTHOR__", author)
            main_path.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return main_tex


def _texinputs_for_template(template_id: str) -> str:
    try:
        template = get_template(template_id)
    except Exception:
        return ""
    extracted_raw = str(template.get("status", {}).get("extracted_path", "") or "").strip()
    if not extracted_raw:
        return ""
    extracted = Path(extracted_raw)
    if not extracted.exists():
        return ""
    sep = os.pathsep
    current = os.environ.get("TEXINPUTS", "")
    if current:
        return str(extracted) + "//" + sep + current
    return str(extracted) + "//" + sep


def _template_extracted_dir(template_id: str) -> Path | None:
    try:
        template = get_template(template_id)
    except Exception:
        return None
    extracted_raw = str(template.get("status", {}).get("extracted_path", "") or "").strip()
    if not extracted_raw:
        return None
    extracted = Path(extracted_raw)
    return extracted if extracted.exists() else None


def _ensure_hithesis_support_files(template_id: str, tex_dir: Path) -> None:
    if not template_id.startswith("hithesis-"):
        return
    extracted = _template_extracted_dir(template_id)
    if not extracted:
        return
    repo_root = extracted / "hithesis-master"
    if not repo_root.exists():
        return

    cls_files = [
        repo_root / "hithesisbook.cls",
        repo_root / "hithesisart.cls",
        repo_root / "hithesisartplus.cls",
    ]
    if not all(path.exists() for path in cls_files):
        ins_file = repo_root / "hithesis.ins"
        if ins_file.exists():
            subprocess.run(
                ["latex", ins_file.name],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )

    support_patterns = ["hithesis*.cls", "hithesis*.cfg", "hithesis*.ist", "hithesis*.bst", "*.def", "*.clo"]
    for pattern in support_patterns:
        for source in repo_root.glob(pattern):
            if not source.is_file():
                continue
            target = tex_dir / source.name
            if not target.exists():
                shutil.copy2(source, target)

    for filename in ["hithesis.sty", "hitszthesis.bst"]:
        source = repo_root / filename
        if source.exists() and not (tex_dir / filename).exists():
            shutil.copy2(source, tex_dir / filename)

    for source in repo_root.iterdir():
        if not source.is_file():
            continue
        if source.suffix.lower() not in {".eps", ".pdf", ".png", ".jpg", ".jpeg"}:
            continue
        target = tex_dir / source.name
        if not target.exists():
            shutil.copy2(source, target)

    if shutil.which("kpsewhich"):
        termes_x_missing = subprocess.run(
            ["kpsewhich", "TeXGyreTermesX-Regular.otf"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode != 0
        termes_present = subprocess.run(
            ["kpsewhich", "texgyretermes-regular.otf"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
        if termes_x_missing and termes_present:
            for cls_path in tex_dir.glob("hithesis*.cls"):
                try:
                    content = cls_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                updated = content.replace("TeXGyreTermesX", "texgyretermes")
                updated = updated.replace("UprightFont = *-Regular", "UprightFont = *-regular")
                updated = updated.replace("BoldFont = *-Bold", "BoldFont = *-bold")
                updated = updated.replace("ItalicFont = *-Italic", "ItalicFont = *-italic")
                updated = updated.replace("BoldItalicFont = *-BoldItalic", "BoldItalicFont = *-bolditalic")
                if updated != content:
                    cls_path.write_text(updated, encoding="utf-8")


def _relative_files(project_id: str) -> list[str]:
    root = _files_dir(project_id)
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and PROJECT_MEMORY_DIR not in path.relative_to(root).parts[:1]
    )


def _choose_main_tex(project_id: str) -> str:
    files = _relative_files(project_id)
    tex_files = [item for item in files if item.lower().endswith(".tex")]
    if not tex_files:
        return DEFAULT_MAIN_TEX

    def is_manual_starter(rel_path: str) -> bool:
        try:
            content = _project_file_path(project_id, rel_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return (
            r"\documentclass[UTF8,12pt]{ctexart}" in content
            and "这里填写摘要。" in content
            and "这里开始写作。建议先上传你自己的项目源码或现有 LaTeX 文件" in content
        )

    def score(rel_path: str) -> tuple[int, int, str]:
        try:
            content = _project_file_path(project_id, rel_path).read_text(encoding="utf-8", errors="ignore")[:12000]
        except OSError:
            content = ""
        filename = Path(rel_path).name.lower()
        rel_lower = rel_path.lower()
        depth = len(Path(rel_path).parts)
        points = 0
        if r"\documentclass" in content:
            points += 100
        if r"\begin{document}" in content:
            points += 20
        if filename == "main.tex":
            points += 36
        elif filename == "manuscript.tex":
            points += 34
        elif filename in {"anonymous-submission-latex-2026.tex", "paper.tex", "submission.tex", "camera-ready.tex"}:
            points += 32
        elif filename in {"paper.tex", "article.tex", "thesis.tex"}:
            points += 28
        if any(token in rel_lower for token in ["formatting-instructions", "instructions", "example", "guide", "template", "copyright"]):
            points -= 120
        if "anonymoussubmission/" in rel_lower:
            points += 18
        if "cameraready/" in rel_lower:
            points -= 12
        points += max(0, 14 - depth * 3)
        if is_manual_starter(rel_path):
            points -= 200
        return (points, -depth, rel_path)

    ranked = sorted(tex_files, key=score, reverse=True)
    if ranked:
        return ranked[0]
    return DEFAULT_MAIN_TEX


def _guess_project_language_from_tex(content: str) -> str:
    text = str(content or "")
    lowered = text.lower()
    if re.search(r"\\documentclass(?:\[[^\]]*\])?\{(?:ctexart|ctexrep|ctexbook)\}", lowered):
        return "zh"
    chinese_hits = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_hits = len(re.findall(r"[A-Za-z]{4,}", text))
    if chinese_hits >= 24 and chinese_hits >= english_hits:
        return "zh"
    return "en"


def _project_file_language(project_id: str, rel_path: str) -> str:
    try:
        content = _project_file_path(project_id, rel_path).read_text(encoding="utf-8", errors="ignore")[:20000]
    except OSError:
        return "en"
    return _guess_project_language_from_tex(content)


def _infer_project_language(project_id: str, meta: dict[str, Any]) -> str:
    explicit = str(meta.get("writing_language") or "").strip()
    if explicit:
        return _normalize_writing_language(explicit)
    template_id = str(meta.get("template_id") or "").strip()
    if template_id:
        try:
            template = get_template(template_id)
            return _normalize_writing_language(template.get("language") or "en")
        except Exception:
            pass
    main_tex = str(meta.get("main_tex") or "").strip()
    if main_tex:
        return _project_file_language(project_id, main_tex)
    chosen = _choose_main_tex(project_id)
    if chosen:
        return _project_file_language(project_id, chosen)
    writing_type = str(meta.get("writing_type") or "").strip().lower()
    return "zh" if writing_type == "grant" else "en"


def _ensure_main_tex(project_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    current_main = str(meta.get("main_tex") or "").strip()
    chosen_main = _choose_main_tex(project_id)
    changed = False
    if chosen_main and chosen_main != current_main:
        meta = {**meta, "main_tex": chosen_main, "updated_at": datetime.now(timezone.utc).isoformat()}
        changed = True
    inferred_language = _infer_project_language(project_id, meta)
    if inferred_language != str(meta.get("writing_language") or ""):
        meta = {**meta, "writing_language": inferred_language, "updated_at": datetime.now(timezone.utc).isoformat()}
        changed = True
    if changed:
        _write_json(_project_meta_path(project_id), meta)
    return meta


def _memory_file(project_id: str, rel_path: str) -> Path:
    return _memory_dir(project_id) / rel_path


def _load_sections_manifest(project_id: str) -> dict[str, Any]:
    return _load_json(_memory_file(project_id, "sections_manifest.json"))


def _write_sections_manifest(project_id: str, manifest: dict[str, Any]) -> None:
    target = _memory_file(project_id, "sections_manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json(target, manifest)


def _slugify(value: str) -> str:
    cleaned = []
    last_sep = False
    for char in str(value or "").strip().lower():
        if char.isalnum():
            cleaned.append(char)
            last_sep = False
            continue
        if "一" <= char <= "鿿":
            cleaned.append(char)
            last_sep = False
            continue
        if not last_sep:
            cleaned.append("-")
            last_sep = True
    slug = "".join(cleaned).strip("-")
    return slug or "section"


def _read_text(project_id: str, rel_path: str) -> str | None:
    try:
        p = _project_file_path(project_id, rel_path)
        if p.exists():
            return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    return None


def _parse_section_tree(project_id: str) -> dict[str, Any]:
    """Parse the main tex file and its \\input'd children to extract the section/chapter tree."""
    project = load_project(project_id)
    main_tex = project.get("main_tex") or _choose_main_tex(project_id)
    if not main_tex:
        return {"top_level": "section", "titles": [], "files": {}}

    main_body = _read_text(project_id, main_tex) or ""
    doc_class = "article"
    m = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", main_body)
    if m:
        doc_class = m.group(1).strip()
    top_level = "chapter" if doc_class in {"book", "ctexbook", "report", "ctexrep"} else "section"

    visited: set[str] = set()
    all_titles: list[dict[str, Any]] = []
    file_sections: dict[str, list[str]] = {}

    def _scan_file(rel_path: str) -> None:
        norm = str(rel_path).replace("\\", "/").strip().lstrip("/")
        if norm in visited:
            return
        visited.add(norm)
        body = _read_text(project_id, norm) or ""
        titles_in_file: list[str] = []

        # Extract top-level section commands
        for cmd in ("chapter", "section", "part"):
            for match in re.finditer(rf"\\{cmd}\*?\{{([^}}]*)\}}", body):
                title = str(match.group(1)).strip()
                if title and title not in titles_in_file:
                    titles_in_file.append(title)
                    all_titles.append({"title": title, "level": cmd, "file": norm, "order": len(all_titles)})

        if titles_in_file:
            file_sections[norm] = titles_in_file

        # Follow \input / \include
        for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", body):
            name = str(match.group(1)).strip()
            if not name.endswith(".tex"):
                name = name + ".tex"
            if name not in visited:
                _scan_file(name)

    _scan_file(main_tex)
    return {"top_level": top_level, "titles": all_titles, "files": file_sections, "main_tex": main_tex}


def _extract_pdf_chapters(project_id: str) -> list[str]:
    """Try to compile the project and extract chapter/section titles from the PDF via pdftotext."""
    try:
        result = compile_project(project_id)
    except Exception:
        return []
    pdf_path = result.get("pdf_path") or ""
    if not pdf_path:
        return []
    pdf_file = _files_dir(project_id) / pdf_path if not Path(pdf_path).is_absolute() else Path(pdf_path)
    if not pdf_file.exists():
        return []

    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return []

    try:
        completed = subprocess.run(
            [pdftotext, "-layout", str(pdf_file), "-"],
            capture_output=True, text=True, timeout=30,
        )
        text = completed.stdout or ""
    except Exception:
        return []

    # Extract lines that look like chapter/section titles from PDF text
    # Match patterns like "第1章 标题", "Chapter 1 Title", "1. Title", etc.
    titles: list[str] = []
    chapter_patterns = [
        r"^第[一二三四五六七八九十\d]+章\s*.+",
        r"^Chapter\s+\d+[\.\s].+",
        r"^\d+[\.\s]\s*[A-Z一-鿿][^\d]{2,}",
    ]
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 4 or len(line) > 120:
            continue
        for pat in chapter_patterns:
            if re.match(pat, line, re.IGNORECASE):
                if line not in titles:
                    titles.append(line)
                break
    return titles


def analyze_chapters_with_llm(project_id: str) -> dict[str, Any]:
    """Parse the main tex section tree, optionally compile to PDF for chapter extraction,
    then ask the LLM to decide which sections form the editable chapter list.

    Writes the result to sections_manifest.json in the project memory directory.
    """
    tree = _parse_section_tree(project_id)
    parsed_titles = tree.get("titles") or []
    top_level = tree.get("top_level", "section")

    if not parsed_titles:
        return {"sections": [], "source": "llm", "error": "no sections found in tex source"}

    # Try PDF chapter extraction as a supplement
    pdf_titles = _extract_pdf_chapters(project_id)

    # Build a concise structure summary for the LLM — NOT raw file contents
    lines = [
        f"文档类顶层命令：\\{top_level}",
        f"从主tex文件和\\input/\\include子文件中解析到 {len(parsed_titles)} 个章节标题：",
        "",
    ]
    for item in parsed_titles:
        lines.append(f"  [{item['level']}] {item['title']}  (文件: {item['file']})")

    if pdf_titles:
        lines.append("")
        lines.append(f"从编译后的PDF中提取到 {len(pdf_titles)} 个章节标题：")
        for t in pdf_titles:
            lines.append(f"  - {t}")

    structure_summary = "\n".join(lines)

    prompt = f"""你是LaTeX文档结构分析器。下面是一个LaTeX项目的章节结构摘要，请判断哪些条目应该作为"可编辑的正文章节"。

规则：
1. 排除：摘要/Abstract、参考文献/References、致谢/Acknowledgments、附录/Appendix、目录/Table of Contents
2. 排除：仅仅包含"声明""版权""符号表"等非内容性页面
3. 同一标题如果在多个层级出现只保留顶层（如同时有section和subsection同名，只保留section）
4. 按文档出现顺序排列
5. 为每个章节生成英文slug

返回纯JSON（不要markdown包裹）：
{{"sections": [{{"title": "原标题", "slug": "english-slug", "sort_order": 1}}, ...]}}

{structure_summary}"""

    try:
        config = load_config()
        provider = load_default_model_provider()
        result = chat_with_kimi(
            config,
            prompt,
            api_key=load_provider_api_key(provider),
            provider=provider,
        )
        raw = result.content.strip()
        json_start = raw.find("{")
        json_end = raw.rfind("}")
        if json_start >= 0 and json_end > json_start:
            raw = raw[json_start:json_end + 1]
        parsed = json.loads(raw)
        sections = parsed.get("sections", []) if isinstance(parsed, dict) else []
    except Exception as exc:
        # Fallback: use parsed titles directly, filtering obvious non-content
        skip_keywords = ["参考", "文献", "reference", "摘要", "abstract", "致谢", "acknowledg", "附录", "appendix", "目录", "content", "声明"]
        sections = []
        for item in parsed_titles:
            title_lower = item["title"].lower()
            if any(kw in title_lower for kw in skip_keywords):
                continue
            sections.append({
                "title": item["title"],
                "slug": _slugify(item["title"]),
                "sort_order": len(sections) + 1,
            })
        return {"sections": sections, "source": "fallback_regex", "error": str(exc)}

    if not sections:
        return {"sections": [], "source": "llm", "error": "LLM returned empty section list"}

    # Enrich with paths and deduplicate slugs
    enriched: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for item in sections:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        slug = str(item.get("slug") or _slugify(title)).strip()
        base_slug = slug
        counter = 2
        while slug in seen_slugs and counter < 20:
            slug = f"{base_slug}-{counter}"
            counter += 1
        seen_slugs.add(slug)
        enriched.append({
            "title": title,
            "slug": slug,
            "path": f"sections/{slug}.tex",
            "sort_order": int(item.get("sort_order", len(enriched) + 1)),
        })

    manifest = {"sections": enriched, "source": "llm"}
    _write_sections_manifest(project_id, manifest)
    return manifest


def _load_section_memories(project_id: str) -> list[dict[str, Any]]:
    path = _memory_file(project_id, "section_memory.json")
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items") or payload.get("sections") or []
    else:
        items = []
    return items if isinstance(items, list) else []


def _load_evidence_memory(project_id: str) -> dict[str, Any]:
    payload = _load_json(_memory_file(project_id, "evidence_memory.json"))
    return payload if isinstance(payload, dict) else {}


def _load_item_memory(project_id: str, rel_path: str) -> list[dict[str, Any]]:
    path = _memory_file(project_id, rel_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("items", [])
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def _write_item_memory(project_id: str, rel_path: str, items: list[dict[str, Any]]) -> None:
    target = _memory_file(project_id, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json(target, {"items": items})


def _load_recent_context(project_id: str) -> list[dict[str, Any]]:
    return _load_item_memory(project_id, RECENT_CONTEXT_FILE)


def _load_conversation(project_id: str) -> list[dict[str, Any]]:
    return _load_item_memory(project_id, CONVERSATION_FILE)


def _strip_latex_commands(value: str) -> str:
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _excerpt(value: str, limit: int = 280) -> str:
    cleaned = _strip_latex_commands(str(value or ""))
    return cleaned[:limit]


def _section_title(project_id: str, rel_path: str) -> str:
    normalized_path = str(rel_path or "").strip()
    sections_manifest = _load_sections_manifest(project_id)
    sections = sections_manifest.get("sections", []) if isinstance(sections_manifest, dict) else []
    for item in sections if isinstance(sections, list) else []:
        if str(item.get("path") or "") == normalized_path:
            return str(item.get("title") or "").strip() or Path(normalized_path).stem
    return Path(normalized_path).stem or "章节"


def _safe_source_name(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or ""), flags=re.U).strip("_")
    return cleaned[:80] or "source"


def _safe_workspace_segment(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or ""), flags=re.U).strip("._")
    return cleaned[:96] or "workspace"


def _normalize_rel_path(rel_path: str) -> str:
    raw = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("invalid project path")
    return "/".join(parts)


def _is_text_path(rel_path: str, raw: bytes | None = None) -> bool:
    suffix = Path(rel_path).suffix.lower()
    if suffix in TEXT_FILE_EXTENSIONS:
        return True
    if raw is None:
        return False
    if b"\x00" in raw[:2048]:
        return False
    try:
        raw[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _project_file_path(project_id: str, rel_path: str) -> Path:
    normalized = _normalize_rel_path(rel_path)
    path = (_files_dir(project_id) / normalized).resolve()
    root = _files_dir(project_id).resolve()
    if not str(path).startswith(str(root)):
        raise ValueError("invalid file path")
    return path


def _clean_latexmk_db(base_path: Path) -> None:
    """Remove latexmk state files so that dependency tracking starts fresh."""
    for suffix in (".fdb_latexmk", ".fls"):
        p = base_path.with_suffix(suffix)
        if p.exists():
            p.unlink()


def _merge_sections_into_body(project_id: str) -> None:
    """Replace bare \\section{} headings in text-based input files with the
    actual content from each section file (sections/*.tex).  The workflow
    manages individual section files, but templates use monolithic body
    files.  This merges content back before compile."""
    from .writing_workflow import _section_content, _section_map

    project = load_project(project_id)
    files_dir = _files_dir(project_id)
    main_tex = project.get("main_tex") or _choose_main_tex(project_id)
    tex_path = files_dir / main_tex
    if not tex_path.exists():
        return

    tex_text = tex_path.read_text(encoding="utf-8")

    # Walk every \input{…} / \include{…} in the main tex and attempt a merge
    # into each one that is a text file on disk.  The first match was
    # historically front/coverart.tex, so a single re.search was wrong.
    for body_match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", tex_text):
        body_rel = body_match.group(1)
        body_path = files_dir / body_rel
        if not body_path.exists():
            # LaTeX convention: \input{name} resolves to name.tex when no
            # extension is present.
            if not Path(body_rel).suffix:
                body_path = files_dir / (body_rel + ".tex")
        if not body_path.exists():
            continue
        if not body_path.suffix.lower() in {".tex", ".txt", ".md", ".sty", ".cls"}:
            continue

        body_text = body_path.read_text(encoding="utf-8")
        modified = False

        for brief in _section_map(project_id).values():
            content = _section_content(project_id, brief.path)
            if not content.strip():
                continue
            title_re = re.escape(brief.title)
            pattern = re.compile(
                rf"(\\section\s*\{{{title_re}\}}).*?(?=\n\s*\\section|\n\s*\\bibliography|\n\s*\\bibliographystyle|\Z)",
                re.DOTALL,
            )
            body_text, n = re.subn(pattern, lambda _m: content.rstrip(), body_text)
            if n:
                modified = True

        if modified:
            body_path.write_text(body_text, encoding="utf-8")


def _ensure_compile_fallbacks(tex_dir: Path) -> None:
    if not (tex_dir / "algorithm.sty").exists() and not shutil.which("kpsewhich"):
        pass
    kpsewhich = shutil.which("kpsewhich")
    has_algorithm = False
    has_algorithmic = False
    if kpsewhich:
        has_algorithm = subprocess.run(
            [kpsewhich, "algorithm.sty"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
        has_algorithmic = subprocess.run(
            [kpsewhich, "algorithmic.sty"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
    if not has_algorithm and not (tex_dir / "algorithm.sty").exists():
        (tex_dir / "algorithm.sty").write_text(ALGORITHM_STY_FALLBACK, encoding="utf-8")
    if not has_algorithmic and not (tex_dir / "algorithmic.sty").exists():
        (tex_dir / "algorithmic.sty").write_text(ALGORITHMIC_STY_FALLBACK, encoding="utf-8")
    has_gbt7714 = False
    if kpsewhich:
        has_gbt7714 = subprocess.run(
            [kpsewhich, "gbt7714.sty"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
    if not has_gbt7714 and not (tex_dir / "gbt7714.sty").exists():
        (tex_dir / "gbt7714.sty").write_text(GBT7714_STY_FALLBACK, encoding="utf-8")
    has_siunitx = False
    if kpsewhich:
        has_siunitx = subprocess.run(
            [kpsewhich, "siunitx.sty"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
    if not has_siunitx and not (tex_dir / "siunitx.sty").exists():
        (tex_dir / "siunitx.sty").write_text(SIUNITX_STY_FALLBACK, encoding="utf-8")


def _replace_project_tree(project_id: str) -> None:
    root = _files_dir(project_id)
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    else:
        for item in root.iterdir():
            if item.name == PROJECT_MEMORY_DIR:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    pdf_path = _pdf_path(project_id)
    if pdf_path.exists():
        pdf_path.unlink()
    compile_meta_path = _compile_meta_path(project_id)
    if compile_meta_path.exists():
        compile_meta_path.unlink()


def _workspace_section_label(rel_path: str, is_text: bool) -> str:
    suffix = Path(rel_path).suffix.lower()
    rel_lower = rel_path.lower()
    name = Path(rel_path).name.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".pdf", ".svg"} and any(token in rel_lower for token in ["arch", "framework", "pipeline", "system", "overview", "diagram"]):
        return "方法与实现"
    if suffix in {".png", ".jpg", ".jpeg", ".pdf", ".svg"} or any(token in rel_lower for token in ["result", "figure", "plot", "curve", "confusion", "roc", "acc", "loss"]):
        return "结果与分析"
    if suffix in {".py", ".ipynb", ".r", ".jl"}:
        if any(token in rel_lower for token in ["train", "predict", "eval", "test", "experiment", "runner"]):
            return "实验设计"
        if any(token in rel_lower for token in ["model", "network", "module", "layer", "dataset", "loader", "label", "preprocess", "feature"]):
            return "方法与实现"
        return "代码设计"
    if suffix in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf", ".sh"}:
        return "实验设计"
    if suffix in {".csv", ".tsv", ".xlsx"}:
        return "结果与分析"
    if suffix in {".md", ".txt", ".tex"}:
        if any(token in rel_lower for token in ["readme", "method", "design", "implement"]):
            return "方法与实现"
        if any(token in rel_lower for token in ["result", "analysis", "discussion", "conclusion"]):
            return "结果与分析"
        return "项目说明"
    if not is_text:
        return "结果与分析"
    return "代码设计"


def _workspace_role(rel_path: str, section_label: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".pdf", ".svg"}:
        return "figure_asset"
    if suffix in {".py", ".ipynb", ".r", ".jl", ".sh"}:
        return "code_context"
    if suffix in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf"}:
        return "experiment_config"
    if section_label == "结果与分析":
        return "result_evidence"
    if section_label == "方法与实现":
        return "method_context"
    return "workspace_context"


def _workspace_excerpt_from_path(path: Path, rel_path: str, *, is_text: bool) -> str:
    if not is_text:
        return f"{Path(rel_path).suffix.lower() or 'binary'} binary asset"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return _excerpt(text, limit=420)


def _workspace_memory_from_entry(entry: dict[str, Any]) -> str:
    rel_path = str(entry.get("path") or "")
    section = str(entry.get("section") or "")
    role = str(entry.get("role") or "")
    excerpt = str(entry.get("excerpt") or "")
    return f"{Path(rel_path).name} | {role} | {excerpt}".strip(" |")


def _copy_workspace_tree(source_root: Path, target_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    target_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_root.rglob("*")):
        if source.is_dir():
            continue
        rel_path = source.relative_to(source_root).as_posix()
        target = target_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        raw = target.read_bytes()
        is_text = _is_text_path(rel_path, raw=raw)
        entry = {
            "name": source.name,
            "path": rel_path,
            "size": source.stat().st_size,
            "suffix": target.suffix.lower(),
            "is_text": is_text,
        }
        entry["section"] = _workspace_section_label(rel_path, is_text)
        entry["role"] = _workspace_role(rel_path, str(entry["section"]))
        entry["excerpt"] = _workspace_excerpt_from_path(target, rel_path, is_text=is_text)
        entries.append(entry)
    return entries


def _workspace_source_cards(entries: list[dict[str, Any]], workspace_name: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for entry in entries:
        role = str(entry.get("role") or "")
        if role == "figure_asset":
            continue
        section = str(entry.get("section") or "")
        rel_path = str(entry.get("path") or "")
        cards.append(
            {
                "name": f"{workspace_name}/{rel_path}",
                "content_type": "workspace",
                "kind": "workspace",
                "text": f"[{section}] {Path(rel_path).name}\n{str(entry.get('excerpt') or '')}",
            }
        )
    return cards


def _workspace_figure_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for entry in entries:
        if str(entry.get("role") or "") != "figure_asset":
            continue
        rel_path = str(entry.get("path") or "")
        figures.append(
            {
                "name": str(entry.get("name") or Path(rel_path).name),
                "path": rel_path,
                "section": str(entry.get("section") or "结果与分析"),
                "size": int(entry.get("size") or 0),
                "latex_path": f"assets/workspace/{rel_path}",
            }
        )
    return figures


def _write_workspace_section_memories(project_id: str, entries: list[dict[str, Any]]) -> None:
    existing = _load_section_memories(project_id)
    retained = [item for item in existing if not str(item.get("path") or "").startswith("workspace::")]
    if len(retained) != len(existing):
        _write_json(_memory_file(project_id, "section_memory.json"), {"items": retained[-36:]})
    for entry in entries:
        role = str(entry.get("role") or "")
        if role == "figure_asset":
            continue
        rel_path = str(entry.get("path") or "")
        if not rel_path:
            continue
        update_section_memory(
            project_id,
            f"workspace::{rel_path}",
            _workspace_memory_from_entry(entry),
            prompt=f"workspace import -> {str(entry.get('section') or '')}",
            evidence_keys=[],
        )


def _ensure_workspace_assets(project_id: str, workspace_root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    asset_entries = [entry for entry in entries if str(entry.get("role") or "") == "figure_asset"]
    if not asset_entries:
        return []
    for entry in asset_entries:
        rel_path = str(entry.get("path") or "")
        source = workspace_root / rel_path
        target = _project_file_path(project_id, f"assets/workspace/{rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copy2(source, target)
    return _workspace_figure_entries(asset_entries)


def import_local_workspace(
    project_id: str,
    workspace_path: str,
    *,
    copy_assets_into_project: bool = True,
) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    workspace_path = str(workspace_path or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    if not workspace_path:
        raise ValueError("workspace_path is required")
    load_project(project_id)
    source_root = Path(workspace_path).expanduser().resolve()
    if not source_root.exists():
        raise FileNotFoundError("workspace path not found")
    if not source_root.is_dir():
        raise ValueError("workspace path must be a directory")
    project_root = PROJECT_ROOT.resolve()
    if not str(source_root).startswith(str(project_root.parent)) and not str(source_root).startswith("/tmp/"):
        raise ValueError("workspace path is outside the allowed import area")

    workspace_name = _safe_workspace_segment(source_root.name)
    target_root = _workspace_dir(project_id) / workspace_name
    if target_root.exists():
        shutil.rmtree(target_root)
    entries = _copy_workspace_tree(source_root, target_root)
    figures = _ensure_workspace_assets(project_id, target_root, entries) if copy_assets_into_project else []
    existing_sources = load_project_sources(project_id, include_text=True)
    save_project_sources(project_id, existing_sources + _workspace_source_cards(entries, workspace_name))
    _write_workspace_section_memories(project_id, entries)

    payload = {
        "workspace_name": workspace_name,
        "workspace_path": str(source_root),
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(entries),
        "figure_count": len(figures),
        "entries": entries[:300],
        "figures": figures[:120],
    }
    _write_json(_workspace_index_path(project_id), payload)
    meta = _load_json(_project_meta_path(project_id))
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta["workspace_path"] = str(source_root)
    _write_json(_project_meta_path(project_id), meta)
    record_project_turn(
        project_id,
        "assistant",
        f"已导入本地代码工作区：{source_root}",
        kind="workspace:import",
        metadata={
            "workspace_path": str(source_root),
            "workspace_name": workspace_name,
            "file_count": len(entries),
            "figure_count": len(figures),
        },
    )
    return {
        "workspace_name": workspace_name,
        "workspace_path": str(source_root),
        "entries": entries,
        "figures": figures,
        "file_count": len(entries),
        "figure_count": len(figures),
    }


def load_workspace_index(project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    if not _project_meta_path(project_id).exists():
        raise FileNotFoundError("writing project not found")
    payload = _load_json(_workspace_index_path(project_id))
    return payload if isinstance(payload, dict) else {}


def insert_workspace_figure(
    project_id: str,
    target_path: str,
    figure_rel_path: str,
    *,
    caption: str = "",
    label: str = "",
    width: str = "0.92\\linewidth",
) -> dict[str, Any]:
    if not project_id:
        raise ValueError("project_id is required")
    if not target_path:
        raise ValueError("target_path is required")
    if not figure_rel_path:
        raise ValueError("figure_rel_path is required")
    file_record = read_project_file(project_id, target_path)
    if not bool(file_record.get("is_text")):
        raise ValueError("target file is not editable text")
    workspace_index = load_workspace_index(project_id)
    workspace_name = str(workspace_index.get("workspace_name") or "").strip()
    if not workspace_name:
        raise FileNotFoundError("workspace figure not found")
    source = _workspace_dir(project_id) / workspace_name / _normalize_rel_path(figure_rel_path)
    if not source.exists():
        raise FileNotFoundError("workspace figure not found")
    figure_target = _project_file_path(project_id, f"assets/workspace/{_normalize_rel_path(figure_rel_path)}")
    figure_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, figure_target)
    figure_name = Path(figure_rel_path).stem
    figure_label = label.strip() or f"fig:{re.sub(r'[^a-zA-Z0-9]+', '-', figure_name).strip('-')}"
    figure_caption = caption.strip() or figure_name.replace("_", " ")
    snippet = "\n".join(
        [
            r"\begin{figure}[htbp]",
            r"\centering",
            rf"\includegraphics[width={width}]{{assets/workspace/{_normalize_rel_path(figure_rel_path)}}}",
            rf"\caption{{{_sanitize_tex_value(figure_caption)}}}",
            rf"\label{{{_sanitize_tex_value(figure_label)}}}",
            r"\end{figure}",
            "",
        ]
    )
    content = str(file_record.get("content") or "")
    content = content.rstrip() + "\n\n" + snippet
    saved = save_project_file(
        {
            "project_id": project_id,
            "path": target_path,
            "content": content,
            "set_main_tex": target_path == str(load_project(project_id).get("main_tex") or ""),
        }
    )
    update_section_memory(project_id, target_path, content, prompt=f"insert workspace figure {figure_rel_path}")
    record_project_turn(
        project_id,
        "assistant",
        f"已插入工作区图片 {figure_rel_path}",
        kind="workspace:figure",
        file_path=target_path,
        metadata={"figure_path": figure_rel_path, "latex_path": f"assets/workspace/{_normalize_rel_path(figure_rel_path)}"},
    )
    return {
        "file": saved,
        "figure_rel_path": figure_rel_path,
        "latex_path": f"assets/workspace/{_normalize_rel_path(figure_rel_path)}",
        "snippet": snippet,
    }


def project_structure_digest(project_id: str, limit: int = 24) -> str:
    files = _relative_files(project_id)
    if not files:
        return "当前项目还没有源码文件。"
    rows = []
    for rel_path in files[:limit]:
        path = _project_file_path(project_id, rel_path)
        suffix = path.suffix.lower()
        if suffix in {".tex", ".cls", ".sty", ".bib"}:
            try:
                snippet = _excerpt(path.read_text(encoding="utf-8", errors="ignore"), limit=220)
            except OSError:
                snippet = ""
            rows.append(f"{rel_path}\n摘要: {snippet}")
        else:
            rows.append(f"{rel_path}\n资源类型: {suffix or 'file'}")
    return "\n\n".join(rows)


def load_project_sources(project_id: str, include_text: bool = False) -> list[dict[str, Any]]:
    payload = _load_json(_sources_index_path(project_id))
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return []
    results: list[dict[str, Any]] = []
    root = _memory_dir(project_id)
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        rel_path = str(entry.get("path") or "").strip()
        if include_text and rel_path:
            path = (root / rel_path).resolve()
            if path.exists():
                entry["text"] = path.read_text(encoding="utf-8")
        results.append(entry)
    return results


def save_project_sources(project_id: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    load_project(project_id)
    root = _sources_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for index, item in enumerate(sources, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"source-{index}")
        text = str(item.get("text") or "")
        if not text.strip():
            continue
        filename = f"{index:02d}_{_safe_source_name(Path(name).stem)}.txt"
        rel_path = f"{PROJECT_SOURCE_DIR}/{filename}"
        target = root / filename
        target.write_text(text, encoding="utf-8")
        items.append(
            {
                "name": name,
                "content_type": str(item.get("content_type") or ""),
                "kind": str(item.get("kind") or Path(name).suffix.lower().lstrip(".")),
                "chars": len(text),
                "path": rel_path,
                "excerpt": text[:400],
            }
        )
    _write_json(_sources_index_path(project_id), {"items": items})
    meta = _load_json(_project_meta_path(project_id))
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(_project_meta_path(project_id), meta)
    return load_project_sources(project_id, include_text=False)


def _requirements_text(project_id: str, meta: dict[str, Any]) -> str:
    requirements = str(meta.get("requirements") or "")
    path = _memory_file(project_id, "project_requirements.md")
    if requirements.strip():
        return requirements
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _project_summary(project_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    meta = _ensure_main_tex(project_id, meta)
    template_id = str(meta.get("template_id") or "")
    bibliography_profile = project_bibliography_profile(project_id)
    template_profile = build_template_profile(project_id, project_dir=_project_dir(project_id))
    template_name = "手动导入项目"
    if template_id:
        try:
            template_name = str(get_template(template_id).get("name") or template_id)
        except Exception:
            template_name = template_id
    compile_result = _load_json(_compile_meta_path(project_id))
    files = _relative_files(project_id)
    sections_manifest = _load_sections_manifest(project_id)
    sections = sections_manifest.get("sections", []) if isinstance(sections_manifest, dict) else []
    section_memories = _load_section_memories(project_id)
    evidence_memory = _load_evidence_memory(project_id)
    source_files = load_project_sources(project_id, include_text=False)
    recent_context = _load_recent_context(project_id)
    workspace_index = load_workspace_index(project_id)
    return {
        "project_id": project_id,
        "title": str(meta.get("title") or ""),
        "author": str(meta.get("author") or "Scientific Agent"),
        "goal": str(meta.get("goal") or ""),
        "query": str(meta.get("query") or ""),
        "requirements": _requirements_text(project_id, meta),
        "writing_type": str(meta.get("writing_type") or "academic"),
        "writing_language": str(meta.get("writing_language") or _infer_project_language(project_id, meta)),
        "template_id": template_id,
        "template_name": template_name,
        "project_mode": str(meta.get("project_mode") or ("manual_upload" if not template_id else "template_based")),
        "bibliography_profile": bibliography_profile,
        "template_profile": template_profile,
        "main_tex": str(meta.get("main_tex") or _choose_main_tex(project_id)),
        "created_at": str(meta.get("created_at") or ""),
        "updated_at": str(meta.get("updated_at") or ""),
        "paths": {
            "dir": str(_project_dir(project_id)),
            "files_dir": str(_files_dir(project_id)),
            "pdf": str(_pdf_path(project_id)) if _pdf_path(project_id).exists() else "",
        },
        "files": files,
        "workspace": {
            "sections": sections if isinstance(sections, list) else [],
            "section_count": len(sections) if isinstance(sections, list) else 0,
            "section_memory_count": len(section_memories),
            "evidence_card_count": len(evidence_memory.get("cards", [])) if isinstance(evidence_memory, dict) else 0,
            "source_count": len(source_files),
            "recent_context_count": len(recent_context),
            "workspace_path": str(workspace_index.get("workspace_path") or meta.get("workspace_path") or ""),
            "workspace_name": str(workspace_index.get("workspace_name") or ""),
            "workspace_file_count": int(workspace_index.get("file_count") or 0),
            "workspace_figure_count": int(workspace_index.get("figure_count") or 0),
            "workspace_figures": workspace_index.get("figures") or [],
        },
        "compile": compile_result,
    }


def _write_uploaded_files(project_id: str, incoming_files: list[dict[str, Any]], replace_project: bool = False) -> str:
    if replace_project:
        _replace_project_tree(project_id)
    main_tex = ""
    for item in incoming_files:
        rel_path = _normalize_rel_path(str(item.get("path") or "").strip())
        raw = item.get("content_bytes")
        text = item.get("content")
        if raw is None and text is None:
            continue
        target = _project_file_path(project_id, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            target.write_bytes(raw if isinstance(raw, bytes) else bytes(raw))
        else:
            target.write_text(str(text or ""), encoding="utf-8")
        if not main_tex and rel_path.lower().endswith(".tex"):
            main_tex = rel_path
    return main_tex or _choose_main_tex(project_id)


def create_project(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or payload.get("run_id") or "").strip() or _new_project_id()
    project_dir = _project_dir(project_id)
    files_dir = _files_dir(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    existing = _load_json(_project_meta_path(project_id))
    template_id = str(payload.get("template_id") if "template_id" in payload else existing.get("template_id") or "")
    incoming_files = payload.get("files")
    incoming_tex = str(payload.get("tex") or "")
    main_tex = str(payload.get("main_tex") or existing.get("main_tex") or DEFAULT_MAIN_TEX)
    memory_files = payload.get("memory_files")
    replace_project = bool(payload.get("replace_project", False))
    writing_type = str(payload.get("writing_type") or existing.get("writing_type") or "academic")
    writing_language = str(payload.get("writing_language") or existing.get("writing_language") or "").strip()
    if not writing_language:
        if template_id:
            try:
                writing_language = _normalize_writing_language(get_template(template_id).get("language") or "en")
            except Exception:
                writing_language = "zh" if writing_type == "grant" else "en"
        else:
            writing_language = "zh" if writing_type == "grant" else "en"

    if incoming_files and isinstance(incoming_files, list):
        main_tex = _write_uploaded_files(project_id, incoming_files, replace_project=replace_project) or main_tex
    elif incoming_tex.strip():
        target = files_dir / main_tex
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(incoming_tex, encoding="utf-8")
    elif not any(files_dir.iterdir()):
        title = str(payload.get("title") or existing.get("title") or "Untitled Project")
        author = str(payload.get("author") or existing.get("author") or "Scientific Agent")
        if template_id:
            main_tex = _copy_template_entry(template_id, project_id, title, author)
        else:
            starter = _template_project_starter(template_id, title, author, writing_language=writing_language)
            (files_dir / DEFAULT_MAIN_TEX).write_text(starter, encoding="utf-8")
            main_tex = DEFAULT_MAIN_TEX

    if memory_files and isinstance(memory_files, list):
        for item in memory_files:
            rel_path = str(item.get("path") or "").strip()
            content = str(item.get("content") or "")
            if not rel_path:
                continue
            target = (_memory_dir(project_id) / rel_path).resolve()
            root = _memory_dir(project_id).resolve()
            if not str(target).startswith(str(root)):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    created_at = existing.get("created_at") or datetime.now(timezone.utc).isoformat()
    meta = {
        **existing,
        "project_id": project_id,
        "title": str(payload.get("title") or existing.get("title") or "Untitled Project"),
        "author": str(payload.get("author") or existing.get("author") or "Scientific Agent"),
        "goal": str(payload.get("goal") or existing.get("goal") or ""),
        "query": str(payload.get("query") or existing.get("query") or ""),
        "requirements": str(payload.get("requirements") or existing.get("requirements") or ""),
        "writing_type": writing_type,
        "writing_language": _normalize_writing_language(writing_language),
        "template_id": template_id,
        "main_tex": main_tex,
        "project_mode": "manual_upload" if not template_id else "template_based",
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(_project_meta_path(project_id), meta)
    try:
        load_guardrails(project_id)
    except Exception:
        pass
    # Auto-analyze chapter structure with LLM when files were uploaded
    if incoming_files and isinstance(incoming_files, list):
        try:
            analyze_chapters_with_llm(project_id)
        except Exception:
            pass
    return load_project(project_id)


def list_projects() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not DEFAULT_OUTPUT_DIR.exists():
        return {"items": items}
    for path in sorted(DEFAULT_OUTPUT_DIR.iterdir(), key=lambda item: item.name, reverse=True):
        if not path.is_dir():
            continue
        meta = _load_json(path / PROJECT_META)
        if not meta:
            continue
        items.append(_project_summary(path.name, meta))
    return {"items": items}


def load_project(project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    meta = _load_json(_project_meta_path(project_id))
    if not meta:
        raise FileNotFoundError("writing project not found")
    return _project_summary(project_id, meta)


def delete_project(project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    root = _project_dir(project_id).resolve()
    base = DEFAULT_OUTPUT_DIR.resolve()
    if not str(root).startswith(str(base)):
        raise ValueError("invalid project path")
    if not root.exists():
        raise FileNotFoundError("writing project not found")
    shutil.rmtree(root)
    return {"project_id": project_id, "deleted": True}


def load_project_context(
    project_id: str,
    rel_path: str = "",
    *,
    include_source_text: bool = False,
    source_text_limit: int = 2400,
    recent_context_limit: int = 8,
    section_memory_limit: int = 6,
    evidence_card_limit: int = 6,
    conversation_limit: int = 12,
) -> dict[str, Any]:
    project = load_project(project_id)
    section_memories = _load_section_memories(project_id)[-section_memory_limit:]
    evidence_memory = _load_evidence_memory(project_id)
    if isinstance(evidence_memory, dict):
        cards = evidence_memory.get("cards", [])
        if isinstance(cards, list):
            evidence_memory = {**evidence_memory, "cards": cards[-evidence_card_limit:]}
    source_files = load_project_sources(project_id, include_text=include_source_text)
    workspace_index = load_workspace_index(project_id)
    recent_context = _load_recent_context(project_id)[-recent_context_limit:]
    conversation = _load_conversation(project_id)[-conversation_limit:]
    sections_manifest = _load_sections_manifest(project_id)
    sections = sections_manifest.get("sections", []) if isinstance(sections_manifest, dict) else []
    current_section = {}
    normalized_path = str(rel_path or "").strip()
    for item in sections if isinstance(sections, list) else []:
        if str(item.get("path") or "") == normalized_path:
            current_section = item
            break
    if not current_section and normalized_path:
        for item in section_memories:
            if str(item.get("section") or "") in normalized_path:
                current_section = item
                break
    source_cards: list[dict[str, Any]] = []
    for item in source_files:
        entry = dict(item)
        if include_source_text and source_text_limit > 0:
            text = str(entry.get("text") or "")
            if text:
                entry["text"] = text[:source_text_limit]
        else:
            entry.pop("text", None)
        entry["excerpt"] = str(entry.get("excerpt") or entry.get("text") or "")[:500]
        source_cards.append(entry)
    return {
        "project_id": project_id,
        "project": project,
        "requirements": project.get("requirements", ""),
        "sections": sections if isinstance(sections, list) else [],
        "section": current_section,
        "section_memories": section_memories,
        "evidence_memory": evidence_memory,
        "source_files": source_cards,
        "workspace_index": workspace_index,
        "recent_context": recent_context,
        "conversation": conversation,
    }


def read_project_file(project_id: str, rel_path: str) -> dict[str, Any]:
    project = load_project(project_id)
    path = _project_file_path(project_id, rel_path)
    if not path.exists():
        raise FileNotFoundError("project file not found")
    raw = path.read_bytes()
    is_text = _is_text_path(rel_path, raw=raw)
    return {
        "project_id": project_id,
        "path": _normalize_rel_path(rel_path),
        "content": raw.decode("utf-8", errors="ignore") if is_text else "",
        "is_text": is_text,
        "size": len(raw),
        "project": project,
    }


def save_project_file(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    rel_path = str(payload.get("path") or "").strip()
    content = str(payload.get("content") or "")
    if not project_id:
        raise ValueError("project_id is required")
    if not rel_path:
        raise ValueError("path is required")
    project = load_project(project_id)
    try:
        existing_content = str(read_project_file(project_id, rel_path).get("content") or "")
    except FileNotFoundError:
        existing_content = ""
    preserve_structure = bool(payload.get("preserve_structure", False))
    bibliography_text = str(payload.get("bibliography") or "")
    content = _prepare_project_file_content(
        project_id,
        rel_path,
        content,
        preserve_structure=preserve_structure,
        bibliography_profile=project.get("bibliography_profile") if isinstance(project, dict) else None,
        bibliography_text=bibliography_text,
        main_tex=str(project.get("main_tex") or ""),
        template_id=str(project.get("template_id") or ""),
    )
    guardrail_report = {
        "valid": True,
        "violations": [],
        "section_id": "",
    }
    try:
        guardrails = load_guardrails(project_id)
        section_title = _section_title(project_id, rel_path)
        section_id = resolve_section_id(guardrails, rel_path=rel_path, title=section_title)
        content, violations = strip_illegal_content(
            content,
            existing_content,
            guardrails,
            section_id=section_id or None,
        )
        guardrail_report = {
            "valid": not violations,
            "violations": violations,
            "section_id": section_id,
        }
    except Exception:
        guardrail_report = {
            "valid": True,
            "violations": [],
            "section_id": "",
        }
    path = _project_file_path(project_id, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    meta = _load_json(_project_meta_path(project_id))
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    if bool(payload.get("set_main_tex", False)) or not meta.get("main_tex"):
        meta["main_tex"] = rel_path
    _write_json(_project_meta_path(project_id), meta)
    saved = read_project_file(project_id, rel_path)
    saved["guardrails"] = guardrail_report
    return saved


def create_project_file(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = str(payload.get("project_id") or "").strip()
    rel_path = str(payload.get("path") or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    if not rel_path:
        raise ValueError("path is required")
    return save_project_file(
        {
            "project_id": project_id,
            "path": _normalize_rel_path(rel_path),
            "content": str(payload.get("content") or ""),
            "set_main_tex": bool(payload.get("set_main_tex", False)),
        }
    )


def compile_project(project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    project = load_project(project_id)
    files_dir = _files_dir(project_id)
    main_tex = project.get("main_tex") or _choose_main_tex(project_id)
    tex_path = files_dir / main_tex
    if not tex_path.exists():
        raise FileNotFoundError(f"{main_tex} not found")
    tex_dir = tex_path.parent
    template_id = str(project.get("template_id") or "")
    _ensure_hithesis_support_files(template_id, tex_dir)
    if template_id.startswith("hithesis-"):
        _ensure_hithesis_runtime_options(tex_path)
    _ensure_compile_fallbacks(tex_dir)

    # Merge section file content into the monolithic template body file so
    # that edits made through the writing workflow are reflected in the PDF.
    _merge_sections_into_body(project_id)

    # Remove latexmk dependency database so that changes to included files
    # (sections, bibliography) always trigger recompilation.  Without this,
    # latexmk may report "All targets are up-to-date" even after a section
    # file was edited through the writing interface.
    _clean_latexmk_db(tex_path)
    _clean_latexmk_db(tex_dir / "report")

    compiler = _compiler()
    if not compiler:
        result = {
            "status": "skipped",
            "reason": "No LaTeX compiler found. Install xelatex, pdflatex, or tectonic.",
            "pdf_path": "",
        }
        _write_json(_compile_meta_path(project_id), result)
        return result

    env = os.environ.copy()
    texinputs = _texinputs_for_template(project.get("template_id", ""))
    if texinputs and not template_id.startswith("hithesis-"):
        env["TEXINPUTS"] = texinputs

    # Compile the project file by absolute path so TEXINPUTS cannot accidentally
    # cause TeX to resolve a same-named file from the template extraction tree.
    latexmk = shutil.which("latexmk")
    latexmkrc = tex_dir / "latexmkrc"
    bibtex = shutil.which("bibtex")
    run_stdout = ""
    run_stderr = ""
    final_returncode = 0
    bibliography_profile = project_bibliography_profile(project_id)
    bibliography_backend = str(bibliography_profile.get("backend") or "").strip().lower()
    needs_bibtex = bibliography_backend in {"bibtex", "natbib"} or bool(bibliography_profile.get("bib_files"))

    def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            cwd=tex_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=180,
            check=False,
            env=env,
        )
        return completed

    if latexmk and latexmkrc.exists():
        command = [latexmk, "-pdf", "-interaction=nonstopmode", tex_path.name]
        completed = run_command(command)
        run_stdout = completed.stdout
        run_stderr = completed.stderr
        final_returncode = completed.returncode
    elif template_id.startswith("hithesis-") and Path(compiler).name == "xelatex":
        commands: list[list[str]] = [
            [compiler, "-interaction=nonstopmode", tex_path.name],
        ]
        if bibtex:
            commands.append([bibtex, tex_path.stem])
        commands.extend(
            [
                [compiler, "-interaction=nonstopmode", tex_path.name],
                [compiler, "-interaction=nonstopmode", tex_path.name],
            ]
        )
        for command in commands:
            completed = run_command(command)
            run_stdout += completed.stdout
            run_stderr += completed.stderr
            final_returncode = completed.returncode
    else:
        if Path(compiler).name == "tectonic":
            command = [compiler, tex_path.name]
            completed = run_command(command)
            run_stdout = completed.stdout
            run_stderr = completed.stderr
            final_returncode = completed.returncode
        else:
            commands = [[compiler, "-interaction=nonstopmode", tex_path.name]]
            if needs_bibtex and bibtex:
                commands.append([bibtex, tex_path.stem])
                commands.append([compiler, "-interaction=nonstopmode", tex_path.name])
                commands.append([compiler, "-interaction=nonstopmode", tex_path.name])
            for command in commands:
                completed = run_command(command)
                run_stdout += completed.stdout
                run_stderr += completed.stderr
                final_returncode = completed.returncode
                if completed.returncode != 0:
                    break
    candidate_pdf = tex_path.with_suffix(".pdf")
    output_pdf = _pdf_path(project_id)
    fatal_error = "\n!" in run_stdout or "\n!" in run_stderr
    warning_patterns = {
        "undefined_citations": r"LaTeX Warning: Citation `[^`]+` .* undefined",
        "undefined_references": r"LaTeX Warning: There were undefined references\.",
        "missing_bibdata": r"I found no \\bibdata command",
        "missing_bibstyle": r"I found no \\bibstyle command",
        "empty_bibliography": r"Empty bibliography",
        "rerun_recommended": r"Rerun to get (?:cross-references|outlines) right",
    }
    warning_hits = [
        name
        for name, pattern in warning_patterns.items()
        if re.search(pattern, run_stdout) or re.search(pattern, run_stderr)
    ]
    blocking_warnings = {
        "undefined_citations",
        "undefined_references",
        "missing_bibdata",
        "missing_bibstyle",
        "empty_bibliography",
    }
    has_blocking_warning = any(item in blocking_warnings for item in warning_hits)
    if candidate_pdf.exists() and (final_returncode == 0 or not fatal_error):
        shutil.copy2(candidate_pdf, output_pdf)
    elif output_pdf.exists():
        output_pdf.unlink()
    pdf_ready = output_pdf.exists()
    compiled_ok = pdf_ready and final_returncode == 0 and not fatal_error and not has_blocking_warning
    compiled_with_warnings = pdf_ready and not compiled_ok and not fatal_error and final_returncode == 0
    result = {
        "status": "compiled" if compiled_ok else "compiled_with_warnings" if compiled_with_warnings else "failed",
        "compiler": compiler,
        "returncode": final_returncode,
        "main_tex": main_tex,
        "stdout_tail": run_stdout[-4000:],
        "stderr_tail": run_stderr[-4000:],
        "warnings": warning_hits,
        "pdf_path": str(output_pdf) if pdf_ready else "",
    }
    _write_json(_compile_meta_path(project_id), result)
    return result


def read_pdf_bytes(project_id: str) -> bytes:
    path = _pdf_path(project_id)
    if not path.exists():
        raise FileNotFoundError("pdf not found")
    return path.read_bytes()


def record_project_turn(
    project_id: str,
    role: str,
    text: str,
    *,
    kind: str = "chat",
    file_path: str = "",
    metadata: dict[str, Any] | None = None,
    conversation_limit: int = 48,
    recent_limit: int = 16,
) -> dict[str, Any]:
    load_project(project_id)
    item = {
        "role": str(role or "assistant"),
        "kind": str(kind or "chat"),
        "file_path": str(file_path or ""),
        "text": str(text or "")[:12000],
        "summary": _excerpt(str(text or ""), limit=320),
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    conversation = _load_conversation(project_id)
    conversation.append(item)
    _write_item_memory(project_id, CONVERSATION_FILE, conversation[-conversation_limit:])

    recent_context = _load_recent_context(project_id)
    recent_context.append(
        {
            "kind": item["kind"],
            "role": item["role"],
            "file_path": item["file_path"],
            "summary": item["summary"],
            "metadata": item["metadata"],
            "created_at": item["created_at"],
        }
    )
    _write_item_memory(project_id, RECENT_CONTEXT_FILE, recent_context[-recent_limit:])
    return item


def update_section_memory(
    project_id: str,
    rel_path: str,
    content: str,
    *,
    prompt: str = "",
    evidence_keys: list[str] | None = None,
) -> dict[str, Any]:
    load_project(project_id)
    normalized_path = str(rel_path or "").strip()
    section = _section_title(project_id, normalized_path)
    item = {
        "section": section,
        "path": normalized_path,
        "memory": _excerpt(content, limit=420),
        "prompt": _excerpt(prompt, limit=240),
        "evidence_keys": [str(key) for key in (evidence_keys or []) if str(key).strip()],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    existing = _load_section_memories(project_id)
    filtered = [
        entry
        for entry in existing
        if str(entry.get("path") or "") != normalized_path
        and not (
            not str(entry.get("path") or "").strip()
            and str(entry.get("section") or "").strip() == section
        )
    ]
    filtered.append(item)
    _write_json(_memory_file(project_id, "section_memory.json"), {"items": filtered[-36:]})
    return item


def _workflow_target_body_tex(project_id: str, project: dict[str, Any]) -> str:
    main_tex = str(project.get("main_tex") or "").strip()
    if not main_tex:
        return ""
    main_path = _project_file_path(project_id, main_tex)
    if not main_path.exists():
        return ""
    try:
        content = main_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    candidates: list[str] = []
    for match in re.finditer(r"^[^%\n]*\\(?:input|include)\{([^}]+)\}", content, flags=re.M):
        rel_path = match.group(1).strip()
        if not rel_path:
            continue
        if not rel_path.lower().endswith(".tex"):
            rel_path += ".tex"
        target = _project_file_path(project_id, rel_path)
        if target.exists():
            candidates.append(rel_path)
    preferred = [
        rel_path
        for rel_path in candidates
        if "report_" in rel_path or rel_path.startswith("body/")
    ]
    if len(preferred) == 1:
        return preferred[0]
    if len(candidates) == 1:
        return candidates[0]
    for rel_path in candidates:
        lowered = rel_path.lower()
        if any(token in lowered for token in ["body/", "sections/", "content/", "chapters/"]):
            return rel_path
    return ""


def _workflow_target_body_tex_list(project_id: str, project: dict[str, Any]) -> list[str]:
    """Return ALL body chapter files from the template's \\mainmatter section."""
    main_tex = str(project.get("main_tex") or "").strip()
    if not main_tex:
        return []
    main_path = _project_file_path(project_id, main_tex)
    if not main_path.exists():
        return []
    try:
        content = main_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    current_section = "preamble"
    chapters: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("%"):
            continue
        if r"\frontmatter" in stripped:
            current_section = "frontmatter"
            continue
        if r"\mainmatter" in stripped:
            current_section = "mainmatter"
            continue
        if r"\backmatter" in stripped or r"\appendix" in stripped:
            break
        if current_section != "mainmatter":
            continue
        for match in re.finditer(r"\\(?:input|include)\{([^}]+)\}", stripped):
            rel_path = match.group(1).strip()
            if not rel_path:
                continue
            if not rel_path.lower().endswith(".tex"):
                rel_path += ".tex"
            target = _project_file_path(project_id, rel_path)
            if target.exists():
                chapters.append(rel_path)
    return chapters


def _strip_existing_bibliography_from_body(content: str) -> str:
    text = str(content or "")
    if not text.strip():
        return ""
    bounds = _bibliography_tail_bounds(text)
    if not bounds:
        return text.strip()
    start, _end = bounds
    return text[:start].rstrip()


def _normalize_bib_resource_path(value: str) -> str:
    text = str(value or "").strip().strip("{}")
    if not text:
        return ""
    text = text.replace("\\", "/")
    name = Path(text).name
    if name and not name.lower().endswith(".bib") and "." not in name:
        return f"{text}.bib"
    return text


def _resolve_bib_resource_path(source_rel_path: str, value: str) -> str:
    normalized = _normalize_bib_resource_path(value)
    if not normalized:
        return ""
    source_parent = Path(str(source_rel_path or "")).parent
    if str(source_parent) in {"", "."}:
        return normalized
    return str((source_parent / normalized).as_posix())


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _project_bibliography_keys(
    project_id: str,
    bibliography_profile: dict[str, Any] | None = None,
    bibliography_text: str = "",
) -> set[str]:
    profile = bibliography_profile or project_bibliography_profile(project_id)
    candidates = list(profile.get("bib_files") or [])
    if not candidates:
        candidates = [rel_path for rel_path in _relative_files(project_id) if rel_path.lower().endswith(".bib")]
    keys = set(_bib_keys_from_text(bibliography_text))
    for rel_path in candidates:
        try:
            content = str(read_project_file(project_id, rel_path).get("content") or "")
        except FileNotFoundError:
            continue
        keys.update(_bib_keys_from_text(content))
    return keys


_SUPPORTED_CITE_COMMANDS = (
    "parencite",
    "textcite",
    "autocite",
    "smartcite",
    "footcitetext",
    "footcite",
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


def _is_instruction_tex(rel_path: str) -> bool:
    lowered = str(rel_path or "").lower()
    return any(
        token in lowered
        for token in [
            "formatting-instructions", "instructions", "example", "guide",
            "template", "copyright", "readme",
        ]
    )


def _looks_like_instruction_tex(rel_path: str) -> bool:
    lowered = str(rel_path or "").lower()
    return any(
        token in lowered
        for token in ["formatting-instructions", "instructions", "example", "guide", "template", "copyright"]
    )


def _strip_document_terminator(content: str) -> str:
    text = str(content or "").strip()
    if r"\end{document}" in text:
        text = text.rsplit(r"\end{document}", 1)[0]
    return text.strip()


def _sanitize_bibliography_tail(content: str) -> str:
    text = _strip_document_terminator(content)
    if not text:
        return ""
    text = re.sub(r"(?:\s*(?:\\\\|\\par|\\newline)\s*)+$", "", text)
    text = re.sub(r"(\\(?:bibliographystyle\{[^}]+\}|bibliography\{[^}]+\}|printbibliography\b))\s*\\\\\s*$", r"\1", text)
    return text.strip()


def _split_document_segments(content: str) -> tuple[str, str, str]:
    text = str(content or "")
    if r"\begin{document}" not in text or r"\end{document}" not in text:
        return "", text, ""
    prefix, remainder = text.split(r"\begin{document}", 1)
    body, suffix = remainder.rsplit(r"\end{document}", 1)
    return prefix, body, suffix


_PRESERVED_BODY_PREFIX_PATTERNS = [
    re.compile(r"\s*(?:%[^\n]*\n)+", flags=re.S),
    re.compile(r"\s*\\(?:frontmatter|mainmatter|backmatter|maketitle|tableofcontents|listoffigures|listoftables)\b\s*", flags=re.S),
    re.compile(r"\s*\\(?:clearpage|newpage|thispagestyle\{[^}]+\}|pagenumbering\{[^}]+\})\s*", flags=re.S),
    re.compile(r"\s*\\begin\{titlepage\}.*?\\end\{titlepage\}\s*", flags=re.S),
    re.compile(r"\s*\\begin\{abstract\*?\}.*?\\end\{abstract\*?\}\s*", flags=re.S),
    re.compile(r"\s*\\begin\{keywords?\}.*?\\end\{keywords?\}\s*", flags=re.S),
    re.compile(r"\s*\\keywords\{.*?\}\s*", flags=re.S),
    re.compile(r"\s*\\(?:makecover|makebackcover)\b\s*", flags=re.S),
    re.compile(r"\s*\\(?:input|include)\{[^}]*front/[^}]*\.tex\}\s*", flags=re.S),
]


def _split_preserved_body_prefix(content: str) -> tuple[str, str]:
    remaining = str(content or "")
    preserved: list[str] = []
    while True:
        matched = False
        for pattern in _PRESERVED_BODY_PREFIX_PATTERNS:
            match = pattern.match(remaining)
            if not match or not match.group(0).strip():
                continue
            preserved.append(match.group(0).strip())
            remaining = remaining[match.end():]
            matched = True
            break
        if not matched:
            break
    return "\n\n".join(part for part in preserved if part).strip(), remaining.lstrip()


def _bibliography_tail_bounds(content: str) -> tuple[int, int] | None:
    text = _strip_document_terminator(content)
    if not text:
        return None
    all_markers = list(
        re.finditer(
            r"\\printbibliography\b|\\bibliographystyle\{[^}]+\}|\\bibliography\{[^}]+\}",
            text,
        )
    )
    # Filter out markers that are inside LaTeX comments (% to end of line)
    markers: list = []
    for m in all_markers:
        line_start = text.rfind("\n", 0, m.start())
        if line_start < 0:
            line_start = 0
        line_prefix = text[line_start:m.start()]
        # Comment if % preceded by even number of backslashes (0, 2, 4...)
        # Odd count (e.g. \%) = escaped percent, not a comment
        if re.search(r"(?<!\\)(?:\\\\)*%", line_prefix):
            continue
        markers.append(m)
    if not markers:
        return None
    selected = markers[-1]
    start = selected.start()
    if selected.group(0).startswith(r"\bibliography"):
        style_markers = [
            m for m in markers
            if m.group(0).startswith(r"\bibliographystyle") and m.start() < start
        ]
        if style_markers:
            style = style_markers[-1]
            if not text[style.end():start].strip():
                start = style.start()
    heading_match = None
    for match in re.finditer(
        r"\\(?:section|chapter)\*?\{([^}]*(?:参考文献|主要参考文献|References?))\}",
        text,
        flags=re.I,
    ):
        if match.start() < start:
            heading_match = match
        else:
            break
    if heading_match and not text[heading_match.end():start].strip():
        start = heading_match.start()
    return start, len(text)


def _extract_bibliography_tail(content: str) -> str:
    text = _strip_document_terminator(content)
    bounds = _bibliography_tail_bounds(content)
    if not bounds:
        return ""
    start, end = bounds
    return _sanitize_bibliography_tail(text[start:end])


def _bibliography_profile_from_text(content: str, source_rel_path: str = "") -> dict[str, Any]:
    text = str(content or "")
    cite_commands: list[str] = []
    cite_pattern = re.compile(
        r"\\(parencite|textcite|autocite|smartcite|footcite|footcitetext|citep|citet|citeauthor|citeyearpar|citeyear|cite)\*?"
    )
    for match in cite_pattern.finditer(text):
        cite_commands.append(match.group(1))
    bib_files: list[str] = []
    for match in re.finditer(r"\\addbibresource(?:\[[^\]]*\])?\{([^}]+)\}", text):
        bib_files.extend(_resolve_bib_resource_path(source_rel_path, item) for item in match.group(1).split(","))
    for match in re.finditer(r"\\bibliography\{([^}]+)\}", text):
        bib_files.extend(_resolve_bib_resource_path(source_rel_path, item) for item in match.group(1).split(","))
    backend = ""
    if re.search(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{biblatex\}", text) or "\\addbibresource" in text or "\\printbibliography" in text:
        backend = "biblatex"
    elif re.search(r"\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{natbib\}", text) or re.search(r"\\cite[p,t]\*?\{", text):
        backend = "natbib"
    elif "\\bibliographystyle{" in text or "\\bibliography{" in text:
        backend = "bibtex"
    tail = _extract_bibliography_tail(text)
    return {
        "backend": backend,
        "cite_commands": _ordered_unique(cite_commands),
        "bib_files": _ordered_unique([item for item in bib_files if item]),
        "tail": tail,
    }


def project_bibliography_profile(project_id: str) -> dict[str, Any]:
    meta = _load_json(_project_meta_path(project_id))
    candidate_paths: list[str] = []
    main_tex = str(meta.get("main_tex") or "").strip()
    if main_tex:
        candidate_paths.append(main_tex)
    for rel_path in _relative_files(project_id):
        if rel_path.lower().endswith(".tex") and rel_path not in candidate_paths and not _looks_like_instruction_tex(rel_path):
            candidate_paths.append(rel_path)

    profile: dict[str, Any] = {
        "backend": "",
        "cite_commands": [],
        "bib_files": [],
        "tail": "",
        "source_paths": [],
        "project_mode": str(meta.get("project_mode") or ("manual_upload" if not str(meta.get("template_id") or "").strip() else "template_based")),
    }
    for rel_path in candidate_paths[:16]:
        try:
            content = _project_file_path(project_id, rel_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not str(content or "").strip():
            continue
        detected = _bibliography_profile_from_text(str(content), rel_path)
        if detected.get("backend") and not profile["backend"]:
            profile["backend"] = str(detected.get("backend") or "")
        profile["cite_commands"] = _ordered_unique(
            list(profile.get("cite_commands") or []) + list(detected.get("cite_commands") or [])
        )
        profile["bib_files"] = _ordered_unique(
            list(profile.get("bib_files") or []) + list(detected.get("bib_files") or [])
        )
        if not profile["tail"] and str(detected.get("tail") or "").strip():
            profile["tail"] = str(detected.get("tail") or "").strip()
        if any(detected.get(key) for key in ["backend", "cite_commands", "bib_files", "tail"]):
            profile["source_paths"].append(rel_path)

    if not profile["bib_files"]:
        profile["bib_files"] = _ordered_unique(
            [rel_path for rel_path in _relative_files(project_id) if rel_path.lower().endswith(".bib")]
        )
    profile["preferred_cite_command"] = _preferred_cite_command(profile)
    return profile


def _default_workflow_bibliography_tail(template_id: str) -> str:
    template_id = str(template_id or "")
    if template_id.startswith("hithesis-"):
        if template_id.endswith("-opening") or template_id.endswith("-midterm"):
            return "\\section{主要参考文献}\n\\bibliographystyle{hithesis}\n\\bibliography{reference}"
        return "\\bibliographystyle{hithesis}\n\\bibliography{reference}"
    return "\\bibliographystyle{plain}\n\\bibliography{reference}"


def _workflow_bibliography_tail(
    existing_content: str,
    template_id: str,
    *,
    project_mode: str = "",
    bibliography_profile: dict[str, Any] | None = None,
) -> str:
    content = str(existing_content or "")
    profile = bibliography_profile or {}
    extracted = _extract_bibliography_tail(content)
    if extracted:
        return extracted
    tail = str(profile.get("tail") or "").strip()
    if tail:
        return _sanitize_bibliography_tail(tail)
    if str(project_mode or "").strip() == "manual_upload":
        if profile.get("source_paths") or profile.get("bib_files") or profile.get("cite_commands"):
            bib_files = profile.get("bib_files") or []
            bib_basename = "reference"
            if bib_files:
                bib_basename = bib_files[0]
                if bib_basename.lower().endswith(".bib"):
                    bib_basename = bib_basename[:-4]
            if template_id.startswith("hithesis-"):
                if template_id.endswith("-opening") or template_id.endswith("-midterm"):
                    return f"\\section{{主要参考文献}}\n\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
                return f"\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
            return f"\\bibliographystyle{{plain}}\n\\bibliography{{{bib_basename}}}"
        return ""
    if profile.get("source_paths") or profile.get("bib_files") or profile.get("cite_commands"):
        bib_files = profile.get("bib_files") or []
        bib_basename = "reference"
        if bib_files:
            bib_basename = bib_files[0]
            if bib_basename.lower().endswith(".bib"):
                bib_basename = bib_basename[:-4]
        backend = str(profile.get("backend") or "").strip()
        if backend == "biblatex":
            return "\\printbibliography"
        if template_id.startswith("hithesis-"):
            if template_id.endswith("-opening") or template_id.endswith("-midterm"):
                return f"\\section{{主要参考文献}}\n\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
            return f"\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
        if backend in {"natbib", "bibtex"}:
            return f"\\bibliographystyle{{plain}}\n\\bibliography{{{bib_basename}}}"
        return f"\\bibliographystyle{{plain}}\n\\bibliography{{{bib_basename}}}"
    backend = str(profile.get("backend") or "").strip()
    if backend == "biblatex":
        return "\\printbibliography"
    if backend in {"natbib", "bibtex"}:
        return "\\bibliographystyle{plain}\n\\bibliography{reference}"
    return _default_workflow_bibliography_tail(template_id)


def _extract_document_body(latex: str) -> str:
    text = str(latex or "")
    if r"\begin{document}" not in text or r"\end{document}" not in text:
        return text.strip()
    _prefix, body, _suffix = _split_document_segments(text)
    return body.strip()


def _normalize_manual_body(content: str) -> str:
    text = _extract_document_body(str(content or ""))
    text = re.sub(r"\\maketitle\b", "", text)
    text = re.sub(r"\\begin\{document\}", "", text)
    text = re.sub(r"\\end\{document\}", "", text)
    return text.strip()


def _replace_document_body(existing_content: str, new_body: str) -> str:
    content = str(existing_content or "")
    body = _normalize_manual_body(new_body)
    # If the body has its own preamble (contains \begin{document}), extract just the body
    if r"\begin{document}" in body:
        body = _extract_document_body(body)
    if not body:
        return content.strip()
    if r"\begin{document}" not in content or r"\end{document}" not in content:
        return body
    prefix, existing_body, suffix = _split_document_segments(content)
    existing_head, existing_remaining = _split_preserved_body_prefix(existing_body)
    existing_tail = _extract_bibliography_tail(existing_body)
    if existing_tail:
        existing_remaining = _strip_existing_bibliography_from_body(existing_remaining)
    _discarded_head, candidate_body = _split_preserved_body_prefix(body)
    candidate_tail = _extract_bibliography_tail(candidate_body)
    candidate_body = _strip_existing_bibliography_from_body(candidate_body)
    if not candidate_body.strip():
        candidate_body = existing_remaining.strip()
    tail = existing_tail or candidate_tail
    assembled_parts = [part for part in [existing_head, candidate_body.strip(), tail] if part and part.strip()]
    assembled_body = "\n\n".join(assembled_parts).strip()
    document = prefix.rstrip() + "\n\\begin{document}\n" + assembled_body + "\n\\end{document}"
    if suffix.strip():
        document += "\n" + suffix.strip()
    return document


def _normalize_body_headings_to_chapter(body: str) -> str:
    """Convert top-level \\section{} to \\chapter{} for book-like templates."""
    text = str(body or "")
    if not text.strip():
        return text
    lines = text.splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(r"\section{") or stripped.startswith(r"\section*{"):
            line = line.replace(r"\section{", r"\chapter{", 1).replace(r"\section*{", r"\chapter*{", 1)
        result.append(line)
    return "\n".join(result)


def _sanitize_workflow_fragment(content: str) -> str:
    text = str(content or "")
    text = re.sub(r"\\begin\{figure\}\s*\\cite\{[^}]*\}", r"\\begin{figure}[htbp]", text)
    text = re.sub(r"\\begin\{figure\}\s*\[([^\]]*)\]", r"\\begin{figure}[\1]", text)
    text = re.sub(
        r"\\text(?!tt\b|bf\b|it\b|rm\b|sf\b|sc\b|width\b|height\b|backslash\b|asciitilde\b|asciicircum\b|\{)",
        "",
        text,
    )
    text = re.sub(r"\\section\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\texttt\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\cite\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\ref\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\caption\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"\\includegraphics(?:\[[^\]\n]*\])?\{[^}\n]*$", "", text, flags=re.M)
    text = re.sub(r"[（(]\s*$", "", text, flags=re.M)
    result: list[str] = []
    cursor = 0
    while True:
        begin = text.find(r"\begin{figure", cursor)
        if begin == -1:
            result.append(text[cursor:])
            break
        result.append(text[cursor:begin])
        end = text.find(r"\end{figure}", begin)
        next_section = text.find(r"\section{", begin + 1)
        if end == -1 or (next_section != -1 and next_section < end):
            cursor = next_section if next_section != -1 else len(text)
            continue
        chunk = text[begin:end + len(r"\end{figure}")]
        if not re.search(r"\\caption\{[^}]+\}", chunk):
            cursor = end + len(r"\end{figure}")
            continue
        result.append(chunk)
        cursor = end + len(r"\end{figure}")
    cleaned = "".join(result)
    lines = cleaned.splitlines()
    while lines:
        tail = lines[-1].rstrip()
        if not tail:
            lines.pop()
            continue
        if re.search(r"(\\end\{[^}]+\}|[。！？；：\}\]])$", tail):
            break
        if re.search(r"(\\section\{|\\(?:texttt|cite|ref|caption|includegraphics|label)\{|[（(])", tail):
            lines.pop()
            continue
        if len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", tail)) < 80:
            lines.pop()
            continue
        break
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _fragment_slug(title: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(title or ""), flags=re.U).strip("_")
    return cleaned[:80] or "section"


def _namespace_fragment_labels(content: str, title: str) -> str:
    text = str(content or "")
    slug = _fragment_slug(title)
    for base in ["fig:workspace_results", "fig:workspace_method"]:
        namespaced = f"{base}_{slug}"
        text = text.replace(rf"\label{{{base}}}", rf"\label{{{namespaced}}}")
        text = text.replace(rf"\ref{{{base}}}", rf"\ref{{{namespaced}}}")
        text = text.replace(rf"\pageref{{{base}}}", rf"\pageref{{{namespaced}}}")
    return text


def _remove_undefined_refs(content: str) -> str:
    text = str(content or "")
    labels = set(re.findall(r"\\label\{([^}]+)\}", text))
    if not labels:
        text = re.sub(r"\\(?:ref|pageref)\{[^}]+\}", "", text)
    else:
        text = re.sub(
            r"\\(?:ref|pageref)\{([^}]+)\}",
            lambda match: match.group(0) if match.group(1) in labels else "",
            text,
        )
    text = re.sub(r"如图\s+所示", "如图所示", text)
    text = re.sub(r"见图\s+所示", "见图所示", text)
    text = re.sub(r"图\s+\s*", "图 ", text)
    return text


def _bib_keys_from_text(content: str) -> set[str]:
    return {
        str(match.group(1)).strip()
        for match in re.finditer(r"@\w+\{([^,]+),", str(content or ""))
        if str(match.group(1)).strip()
    }


def _remove_unknown_citations(
    content: str,
    allowed_keys: set[str],
    bibliography_profile: dict[str, Any] | None = None,
) -> str:
    text = str(content or "")
    if not allowed_keys:
        return text
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
        keys = [item.strip() for item in match.group(4).split(",") if item.strip()]
        kept = [item for item in keys if item in allowed_keys]
        if not kept:
            return r"%[cite: " + ",".join(keys) + "]"
        return "\\" + command + star + options + "{" + ",".join(dict.fromkeys(kept)) + "}"

    text = cite_pattern.sub(repl, text)
    text = re.sub(r"~\s*%\[cite:", r"%[cite:", text)
    text = re.sub(r"\(\s*(%\[cite:[^]]*\])\)", r"\1", text)
    return text


def _split_bibtex_entries(content: str) -> list[str]:
    text = str(content or "")
    entries: list[str] = []
    cursor = 0
    while True:
        start = text.find("@", cursor)
        if start == -1:
            break
        brace_start = text.find("{", start)
        if brace_start == -1:
            break
        depth = 0
        end = brace_start
        while end < len(text):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    entries.append(text[start:end + 1].strip())
                    end += 1
                    break
            end += 1
        cursor = end
        if end >= len(text):
            break
    return [entry for entry in entries if entry]


def _merge_bibliography_content(existing_content: str, generated_content: str) -> str:
    existing = str(existing_content or "").strip()
    generated = str(generated_content or "").strip()
    if not existing:
        return generated + ("\n" if generated else "")
    if not generated:
        return existing + ("\n" if existing else "")
    merged_entries = [existing]
    existing_keys = _bib_keys_from_text(existing)
    for entry in _split_bibtex_entries(generated):
        keys = _bib_keys_from_text(entry)
        if keys and keys.issubset(existing_keys):
            continue
        existing_keys.update(keys)
        merged_entries.append(entry)
    merged = "\n\n".join(part.strip() for part in merged_entries if part.strip())
    return merged + ("\n" if merged else "")


def _prepare_project_file_content(
    project_id: str,
    rel_path: str,
    content: str,
    *,
    preserve_structure: bool = False,
    bibliography_profile: dict[str, Any] | None = None,
    bibliography_text: str = "",
    main_tex: str = "",
    template_id: str = "",
) -> str:
    normalized_path = _normalize_rel_path(rel_path)
    text = str(content or "")
    if not normalized_path.lower().endswith(".tex"):
        return text
    existing = ""
    try:
        existing = str(read_project_file(project_id, normalized_path).get("content") or "")
    except FileNotFoundError:
        existing = ""

    prepared = text.strip()
    if preserve_structure and existing.strip():
        if r"\begin{document}" in existing and r"\end{document}" in existing:
            prepared = _replace_document_body(existing, prepared)
        else:
            existing_tail = _extract_bibliography_tail(existing)
            if existing_tail:
                body = _strip_existing_bibliography_from_body(prepared).strip()
                prepared = (body + "\n\n" + existing_tail).strip() if body else existing_tail
    fallback_tail_needed = (
        preserve_structure
        and bool(main_tex)
        and normalized_path == _normalize_rel_path(main_tex)
        and r"\begin{document}" in existing
        and not _extract_bibliography_tail(prepared)
    )
    if fallback_tail_needed:
        profile = bibliography_profile or {}
        bib_files = profile.get("bib_files") or []
        bib_basename = "reference"
        if bib_files:
            bib_basename = bib_files[0]
            if bib_basename.lower().endswith(".bib"):
                bib_basename = bib_basename[:-4]
        if str(bibliography_text or "").strip() or bib_files:
            backend = str(profile.get("backend") or "").strip()
            if backend == "biblatex":
                fallback_tail = "\\printbibliography"
            elif template_id.startswith("hithesis-"):
                if template_id.endswith("-opening") or template_id.endswith("-midterm"):
                    fallback_tail = f"\\section{{主要参考文献}}\n\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
                else:
                    fallback_tail = f"\\bibliographystyle{{hithesis}}\n\\bibliography{{{bib_basename}}}"
            else:
                fallback_tail = f"\\bibliographystyle{{plain}}\n\\bibliography{{{bib_basename}}}"
        else:
            fallback_tail = _default_workflow_bibliography_tail(template_id)
        if fallback_tail.strip():
            body = _strip_existing_bibliography_from_body(_extract_document_body(prepared)).strip()
            prepared = _replace_document_body(existing, (body + "\n\n" + fallback_tail).strip())

    allowed_keys = _project_bibliography_keys(
        project_id,
        bibliography_profile=bibliography_profile,
        bibliography_text=bibliography_text,
    )
    if allowed_keys:
        prepared = _remove_unknown_citations(
            prepared,
            allowed_keys,
            bibliography_profile=bibliography_profile,
        )
    return _remove_undefined_refs(prepared).strip()


def _distribute_workspace_sections_to_chapters(
    workflow_result: dict[str, Any],
    chapter_paths: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Map each workspace section to the corresponding chapter file, one-to-one."""
    sections = workflow_result.get("workspace_sections") or []
    main_sections = [
        item
        for item in sections
        if isinstance(item, dict)
        and str(item.get("title") or "") not in {"摘要", "Abstract", "参考文献"}
        and "参考" not in str(item.get("title") or "")
    ]
    result: dict[str, list[dict[str, Any]]] = {path: [] for path in chapter_paths}
    for i, section in enumerate(main_sections):
        idx = i if i < len(chapter_paths) else len(chapter_paths) - 1
        result[chapter_paths[idx]].append(section)
    return result


def _workflow_body_content_multi(
    project_id: str,
    project: dict[str, Any],
    chapter_sections: list[dict[str, Any]],
    target_path: str,
    *,
    bibliography_profile: dict[str, Any] | None = None,
    bibliography_text: str = "",
) -> str:
    """Generate body content for a single chapter file from its assigned sections."""
    fragments: list[str] = []
    for item in chapter_sections:
        title = str(item.get("title") or "")
        content = str(item.get("content") or "").strip()
        if content:
            fragment = _sanitize_workflow_fragment(content)
            fragment = _namespace_fragment_labels(fragment, title)
            fragments.append(fragment)
    existing = ""
    try:
        existing = str(read_project_file(project_id, target_path).get("content") or "")
    except FileNotFoundError:
        existing = ""
    existing_body = _strip_existing_bibliography_from_body(existing)
    body = "\n\n".join(fragment for fragment in fragments if fragment.strip()).strip()
    if not body and existing_body.strip():
        body = existing_body.strip()
    template_id = str(project.get("template_id") or "")
    if template_id:
        try:
            structure = get_template_structure(template_id)
            if structure.get("is_book_like"):
                body = _normalize_body_headings_to_chapter(body)
        except (KeyError, OSError):
            pass
    main_tex = str(project.get("main_tex") or "").strip()
    if _normalize_rel_path(target_path) != _normalize_rel_path(main_tex):
        body = _strip_existing_bibliography_from_body(body)
    allowed_keys = _project_bibliography_keys(
        project_id,
        bibliography_profile=bibliography_profile,
        bibliography_text=bibliography_text,
    )
    body = _remove_unknown_citations(
        body,
        allowed_keys,
        bibliography_profile=bibliography_profile,
    )
    return _remove_undefined_refs(body)


def _workflow_body_content(
    project_id: str,
    project: dict[str, Any],
    workflow_result: dict[str, Any],
    target_path: str,
    *,
    bibliography_profile: dict[str, Any] | None = None,
) -> str:
    fragments: list[str] = []
    for item in workflow_result.get("workspace_sections") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        lowered = title.lower()
        if title in {"摘要", "Abstract"} or "参考" in title or lowered == "references" or "证据" in title or "evidence" in lowered:
            continue
        content = str(item.get("content") or "").strip()
        if content:
            fragment = _sanitize_workflow_fragment(content)
            fragment = _namespace_fragment_labels(fragment, title)
            fragments.append(fragment)
    existing = ""
    try:
        existing = str(read_project_file(project_id, target_path).get("content") or "")
    except FileNotFoundError:
        existing = ""
    existing_body = _strip_existing_bibliography_from_body(existing)
    tail = _workflow_bibliography_tail(
        existing,
        str(project.get("template_id") or ""),
        project_mode=str(project.get("project_mode") or ""),
        bibliography_profile=bibliography_profile,
    )
    body = "\n\n".join(fragment for fragment in fragments if fragment.strip()).strip()
    if not body and existing_body.strip():
        body = existing_body.strip()
    template_id = str(project.get("template_id") or "")
    if template_id:
        try:
            structure = get_template_structure(template_id)
            if structure.get("is_book_like"):
                body = _normalize_body_headings_to_chapter(body)
        except (KeyError, OSError):
            pass
    main_tex = str(project.get("main_tex") or "").strip()
    if tail and _normalize_rel_path(target_path) == _normalize_rel_path(main_tex):
        body = (body + "\n\n" + tail).strip() if body else tail
    bibliography = str(workflow_result.get("bibliography") or "")
    allowed_keys = _project_bibliography_keys(
        project_id,
        bibliography_profile=bibliography_profile,
        bibliography_text=bibliography,
    )
    body = _remove_unknown_citations(
        body,
        allowed_keys,
        bibliography_profile=bibliography_profile,
    )
    return _remove_undefined_refs(body)


def _workflow_bib_target(
    project_id: str,
    suggested_name: str,
    bibliography_profile: dict[str, Any] | None = None,
) -> str:
    suggested = str(suggested_name or "reference.bib").strip() or "reference.bib"
    profile = bibliography_profile or {}
    preferred_candidate = ""
    for bib_name in profile.get("bib_files") or []:
        candidate = str(bib_name or "").strip()
        if not candidate:
            continue
        if not preferred_candidate:
            preferred_candidate = candidate
        try:
            if _project_file_path(project_id, candidate).exists():
                return candidate
        except Exception:
            continue
    if preferred_candidate:
        return preferred_candidate
    for rel_path in _relative_files(project_id):
        if rel_path.lower().endswith(".bib"):
            return rel_path
    return suggested


def merge_project_bibliography(
    project_id: str,
    bibliography_text: str,
    *,
    suggested_name: str = "reference.bib",
    bibliography_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bibliography = str(bibliography_text or "").strip()
    if not bibliography:
        return {}
    profile = bibliography_profile or project_bibliography_profile(project_id)
    bib_target = _workflow_bib_target(project_id, suggested_name, profile)
    try:
        existing_bibliography = str(read_project_file(project_id, bib_target).get("content") or "")
    except FileNotFoundError:
        existing_bibliography = ""
    merged = _merge_bibliography_content(existing_bibliography, bibliography)
    return save_project_file(
        {
            "project_id": project_id,
            "path": bib_target,
            "content": merged,
        }
    )


def sync_workflow_project(
    project_id: str,
    workflow_result: dict[str, Any],
    *,
    title: str = "",
    goal: str = "",
    requirements: str = "",
    author: str = "",
    query: str = "",
) -> dict[str, Any]:
    project = load_project(project_id)
    bibliography_profile = project_bibliography_profile(project_id)
    output_dir_raw = str((workflow_result.get("artifacts") or {}).get("output_dir") or "").strip()
    output_dir = Path(output_dir_raw) if output_dir_raw else None
    workspace_sections = workflow_result.get("workspace_sections") or []
    files: list[dict[str, str]] = []
    memory_files: list[dict[str, str]] = []

    latex = str(workflow_result.get("latex") or "")
    template_id = str(project.get("template_id") or "")
    body_targets = _workflow_target_body_tex_list(project_id, project)
    if body_targets:
        chapters_map = _distribute_workspace_sections_to_chapters(workflow_result, body_targets)
        for chapter_path, chapter_sections in chapters_map.items():
            chapter_content = _workflow_body_content_multi(
                project_id,
                project,
                chapter_sections,
                chapter_path,
                bibliography_profile=bibliography_profile,
                bibliography_text=str(workflow_result.get("bibliography") or ""),
            )
            if chapter_content.strip():
                files.append({"path": chapter_path, "content": chapter_content})
    elif latex.strip():
        main_tex = str(project.get("main_tex") or "main.tex")
        if str(project.get("project_mode") or "") == "manual_upload":
            try:
                existing_main = str(read_project_file(project_id, main_tex).get("content") or "")
            except FileNotFoundError:
                existing_main = ""
            body_content = _workflow_body_content(
                project_id,
                project,
                workflow_result,
                main_tex,
                bibliography_profile=bibliography_profile,
            )
            if not body_content.strip():
                body_content = _extract_document_body(latex)
            if existing_main.strip() and body_content.strip():
                files.append({"path": main_tex, "content": _replace_document_body(existing_main, body_content)})
            else:
                files.append({"path": main_tex, "content": latex})
        else:
            # template-based project: merge body into existing main_tex to
            # preserve cover page, front matter, and other template boilerplate
            try:
                existing_main = str(read_project_file(project_id, main_tex).get("content") or "")
            except FileNotFoundError:
                existing_main = ""
            body_content = _extract_document_body(latex)
            if not body_content.strip():
                body_content = _workflow_body_content(
                    project_id,
                    project,
                    workflow_result,
                    main_tex,
                    bibliography_profile=bibliography_profile,
                )
            if existing_main.strip() and body_content.strip():
                files.append({"path": main_tex, "content": _replace_document_body(existing_main, body_content)})
            else:
                files.append({"path": main_tex, "content": latex})

    bibliography = str(workflow_result.get("bibliography") or "")
    if bibliography.strip():
        bib_target = _workflow_bib_target(
            project_id,
            str(workflow_result.get("bib_name") or "reference.bib"),
            bibliography_profile,
        )
        try:
            existing_bibliography = str(read_project_file(project_id, bib_target).get("content") or "")
        except FileNotFoundError:
            existing_bibliography = ""
        files.append(
            {
                "path": bib_target,
                "content": _merge_bibliography_content(existing_bibliography, bibliography),
            }
        )

    if output_dir and output_dir.exists():
        manifest_path = output_dir / "sections_manifest.json"
        if manifest_path.exists():
            memory_files.append(
                {
                    "path": "sections_manifest.json",
                    "content": manifest_path.read_text(encoding="utf-8"),
                }
            )
        evidence_memory_path = output_dir / "evidence_memory.json"
        if evidence_memory_path.exists():
            memory_files.append(
                {
                    "path": "evidence_memory.json",
                    "content": evidence_memory_path.read_text(encoding="utf-8"),
                }
            )
        section_memory_path = output_dir / "section_memory.json"
        if section_memory_path.exists():
            memory_files.append(
                {
                    "path": "section_memory.json",
                    "content": section_memory_path.read_text(encoding="utf-8"),
                }
            )
        workspace_analysis_path = output_dir / "workspace_analysis.json"
        if workspace_analysis_path.exists():
            memory_files.append(
                {
                    "path": "workspace_analysis.json",
                    "content": workspace_analysis_path.read_text(encoding="utf-8"),
                }
            )
        survey_report_path = output_dir / "survey_report.md"
        if survey_report_path.exists():
            memory_files.append(
                {
                    "path": "survey_report.md",
                    "content": survey_report_path.read_text(encoding="utf-8"),
                }
            )
        survey_report_json_path = output_dir / "survey_report.json"
        if survey_report_json_path.exists():
            memory_files.append(
                {
                    "path": "survey_report.json",
                    "content": survey_report_json_path.read_text(encoding="utf-8"),
                }
            )
        agent_outputs_path = output_dir / "agent_outputs.json"
        if agent_outputs_path.exists():
            memory_files.append(
                {
                    "path": "agent_outputs.json",
                    "content": agent_outputs_path.read_text(encoding="utf-8"),
                }
            )
        review_report_path = output_dir / "review_report.json"
        if review_report_path.exists():
            memory_files.append(
                {
                    "path": "review_report.json",
                    "content": review_report_path.read_text(encoding="utf-8"),
                }
            )
        context_summary_path = output_dir / "context_summary.md"
        if context_summary_path.exists():
            memory_files.append(
                {
                    "path": "context_summary.md",
                    "content": context_summary_path.read_text(encoding="utf-8"),
                }
            )

    if not template_id:
        for item in workspace_sections:
            if not isinstance(item, dict):
                continue
            rel_path = str(item.get("path") or "").strip()
            content = str(item.get("content") or "")
            if not rel_path or not content.strip():
                continue
            files.append({"path": rel_path, "content": content})

    generated_bibliography = str(workflow_result.get("bibliography") or "")
    prepared_files: list[dict[str, str]] = []
    for item in files:
        rel_path = str(item.get("path") or "").strip()
        if not rel_path:
            continue
        prepared_files.append(
            {
                "path": rel_path,
                "content": _prepare_project_file_content(
                    project_id,
                    rel_path,
                    str(item.get("content") or ""),
                    preserve_structure=True,
                    bibliography_profile=bibliography_profile,
                    bibliography_text=generated_bibliography,
                    main_tex=str(project.get("main_tex") or ""),
                    template_id=template_id,
                ),
            }
        )

    updated = create_project(
        {
            "project_id": project_id,
            "template_id": template_id,
            "title": title or project.get("title") or "Untitled Project",
            "author": author or project.get("author") or "Scientific Agent",
            "goal": goal or project.get("goal") or "",
            "query": query or project.get("query") or "",
            "requirements": requirements or project.get("requirements") or "",
            "writing_type": str(workflow_result.get("plan", {}).get("writing_type") or project.get("writing_type") or "academic"),
            "writing_language": str(
                workflow_result.get("plan", {}).get("writing_language")
                or project.get("writing_language")
                or "en"
            ),
            "main_tex": str(project.get("main_tex") or "main.tex"),
            "files": prepared_files,
            "memory_files": memory_files,
        }
    )

    compile_result = workflow_result.get("compile") or {}
    if isinstance(compile_result, dict) and compile_result:
        compile_meta = dict(compile_result)
        pdf_path = str(compile_result.get("pdf_path") or "").strip()
        if pdf_path:
            source_pdf = Path(pdf_path)
            target_pdf = _pdf_path(project_id)
            if source_pdf.exists():
                if source_pdf.resolve() != target_pdf.resolve():
                    shutil.copy2(source_pdf, target_pdf)
                compile_meta["pdf_path"] = str(target_pdf)
            else:
                compile_meta["pdf_path"] = ""
        _write_json(_compile_meta_path(project_id), compile_meta)

    record_project_turn(
        project_id,
        "assistant",
        f"已按章节模式生成项目：{workflow_result.get('goal') or goal or updated.get('title', '')}",
        kind="workflow",
        metadata={
            "run_id": workflow_result.get("run_id", ""),
            "section_count": len(workspace_sections),
            "compile_status": compile_result.get("status", ""),
        },
    )
    return load_project(project_id)


def import_project_archive(
    project_id: str,
    archive_name: str,
    archive_bytes: bytes,
    *,
    replace_project: bool = True,
) -> dict[str, Any]:
    load_project(project_id)
    if replace_project:
        _replace_project_tree(project_id)
    files_dir = _files_dir(project_id)
    files_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            filename = member.filename.replace("\\", "/").strip("/")
            if not filename or filename.startswith("__MACOSX/"):
                continue
            parts = [part for part in filename.split("/") if part not in {"", "."}]
            if not parts or any(part == ".." for part in parts):
                continue
            rel_path = "/".join(parts)
            target = _project_file_path(project_id, rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    meta = _load_json(_project_meta_path(project_id))
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta["main_tex"] = _choose_main_tex(project_id)
    meta["writing_language"] = _infer_project_language(project_id, meta)
    _write_json(_project_meta_path(project_id), meta)
    record_project_turn(
        project_id,
        "assistant",
        f"已导入项目压缩包：{archive_name}",
        kind="import",
        metadata={"archive_name": archive_name},
    )
    return load_project(project_id)
