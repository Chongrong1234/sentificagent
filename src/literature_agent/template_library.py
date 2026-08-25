from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from .chat import DEFAULT_KIMI_KEY_FILE, KIMI_API_BASE
from .config import PROJECT_ROOT


TEMPLATE_LIBRARY_ROOT = PROJECT_ROOT / "data" / "library" / "template_library"

TEMPLATE_CATEGORIES: dict[str, str] = {
    "thesis": "中文毕业论文",
    "grant": "基金/项目申报书",
    "conference_journal": "会议/期刊论文",
    "hithesis": "哈尔滨工业大学 hithesis",
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _thesis_starter(document_class: str = "ctexbook") -> str:
    return rf"""
% Target template family: {document_class}
\documentclass[UTF8,12pt]{{{document_class}}}
\usepackage[a4paper,margin=2.6cm]{{geometry}}
\usepackage{{hyperref}}
\usepackage{{graphicx}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{amsmath,amssymb}}
\usepackage{{setspace}}
\setstretch{{1.35}}

\title{{__TITLE__}}
\author{{__AUTHOR__}}
\date{{\today}}

\begin{{document}}
\maketitle

\frontmatter
\chapter*{{摘要}}
这里填写中文摘要。

\chapter*{{Abstract}}
Write the English abstract here.

\tableofcontents

\mainmatter
\chapter{{引言}}
这里填写研究背景、问题定义和文章结构。

\chapter{{相关工作}}
这里整理文献脉络、方法对比与研究空白。

\chapter{{方法}}
这里说明研究方案、技术路线与实验设计。

\chapter{{结果与分析}}
这里填写实验结果、图表与分析讨论。

\chapter{{结论}}
这里填写主要结论、局限性与后续工作。

\backmatter
\chapter*{{参考文献}}
建议使用 BibTeX 或手动整理参考文献。

\end{{document}}
""".strip()


def _grant_starter() -> str:
    return r"""
% Target template family: Chinese grant / proposal
\documentclass[UTF8,12pt]{ctexart}
\usepackage[a4paper,margin=2.6cm]{geometry}
\usepackage{hyperref}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{amsmath,amssymb}
\usepackage{enumitem}
\usepackage{setspace}
\setstretch{1.3}

\title{__TITLE__}
\author{__AUTHOR__}
\date{\today}

\begin{document}
\maketitle

\section{项目摘要}
简述研究问题、技术路线、创新点和预期成果。

\section{立项依据与研究意义}
说明科学问题、应用背景和研究价值。

\section{国内外研究现状与发展趋势}
围绕主题梳理代表性文献、方法脉络和空白点。

\section{拟解决的关键科学问题}
明确列出要突破的问题、边界和判断标准。

\section{研究目标}
写清总体目标、分目标和对应验证指标。

\section{研究内容}
分条说明研究任务、关键技术和实验安排。

\section{技术路线与研究方案}
解释方法流程、数据来源、模型/系统设计与验证路线。

\section{创新点}
\begin{enumerate}[leftmargin=*]
  \item 创新点一。
  \item 创新点二。
  \item 创新点三。
\end{enumerate}

\section{年度研究计划}
按年度列出里程碑、阶段任务与交付物。

\section{预期成果}
说明论文、软件、数据、专利或示范应用等产出。

\section{研究基础与可行性分析}
说明已有工作、条件基础、团队与风险缓释能力。

\section{风险分析与对策}
说明数据、模型、实验、工程实施等风险及对应策略。

\section{参考文献}
建议使用 BibTeX 或手动整理参考文献。

\end{document}
""".strip()


def _report_starter() -> str:
    return r"""
% Target template family: HIT report / opening / midterm
\documentclass[UTF8,12pt]{ctexart}
\usepackage[a4paper,margin=2.6cm]{geometry}
\usepackage{hyperref}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{amsmath,amssymb}
\usepackage{enumitem}
\usepackage{setspace}
\setstretch{1.25}

\title{__TITLE__}
\author{__AUTHOR__}
\date{\today}

\begin{document}
\maketitle

\section{课题背景}
说明研究背景、任务来源和当前进展。

\section{研究内容}
说明当前阶段要完成的工作和技术路线。

\section{阶段进展}
说明已经完成的实验、结果与问题。

\section{后续安排}
说明下一阶段计划、风险和预期交付。

\section{总结}
总结本阶段工作与下一步计划。

\end{document}
""".strip()


def _acmart_starter() -> str:
    return r"""
\documentclass[sigconf]{acmart}
\setcopyright{none}
\copyrightyear{2026}
\acmYear{2026}
\acmDOI{}
\acmISBN{}

\title{__TITLE__}
\author{__AUTHOR__}
\affiliation{\institution{Scientific Agent Lab}}
\email{author@example.com}

\begin{document}
\begin{abstract}
Write the abstract here.
\end{abstract}

\keywords{keyword one, keyword two, keyword three}

\maketitle

\section{Introduction}
Write the motivation, problem setup, and contributions here.

\section{Related Work}
Compare the main research streams here.

\section{Method}
Describe the method, system, or technical route here.

\section{Experiments}
Summarize setup, metrics, and core results here.

\section{Discussion}
Discuss limitations, risks, and implications here.

\section{Conclusion}
Summarize the work and next steps here.

\end{document}
""".strip()


def _ieee_starter() -> str:
    return r"""
\documentclass[conference]{IEEEtran}
\usepackage{cite}
\usepackage{amsmath,amssymb,amsfonts}
\usepackage{graphicx}
\usepackage{textcomp}
\usepackage{xcolor}

\title{__TITLE__}
\author{\IEEEauthorblockN{__AUTHOR__}}

\begin{document}
\maketitle

\begin{abstract}
Write the abstract here.
\end{abstract}

\begin{IEEEkeywords}
keyword one, keyword two, keyword three
\end{IEEEkeywords}

\section{Introduction}
Write the motivation, problem setup, and contributions here.

\section{Related Work}
Compare the main research streams here.

\section{Method}
Describe the method, system, or technical route here.

\section{Experiments}
Summarize setup, metrics, and core results here.

\section{Conclusion}
Summarize the work and next steps here.

\end{document}
""".strip()


def _lncs_starter() -> str:
    return r"""
\documentclass[runningheads]{llncs}
\usepackage[T1]{fontenc}
\usepackage{graphicx}

\title{__TITLE__}
\author{__AUTHOR__}
\institute{Scientific Agent Lab}

\begin{document}
\maketitle

\begin{abstract}
Write the abstract here.
\keywords{keyword one \and keyword two \and keyword three}
\end{abstract}

\section{Introduction}
Write the motivation, problem setup, and contributions here.

\section{Related Work}
Compare the main research streams here.

\section{Method}
Describe the method, system, or technical route here.

\section{Experiments}
Summarize setup, metrics, and core results here.

\section{Conclusion}
Summarize the work and next steps here.

\end{document}
""".strip()


def _elsevier_starter() -> str:
    return r"""
\documentclass[preprint,12pt]{elsarticle}
\usepackage{amssymb}
\usepackage{amsmath}
\usepackage{graphicx}
\journal{Your Target Journal}

\begin{document}

\begin{frontmatter}
\title{__TITLE__}
\author{__AUTHOR__}

\begin{abstract}
Write the abstract here.
\end{abstract}

\begin{keyword}
keyword one \sep keyword two \sep keyword three
\end{keyword}
\end{frontmatter}

\section{Introduction}
Write the motivation, problem setup, and contributions here.

\section{Related Work}
Compare the main research streams here.

\section{Method}
Describe the method, system, or technical route here.

\section{Experiments}
Summarize setup, metrics, and core results here.

\section{Conclusion}
Summarize the work and next steps here.

\end{document}
""".strip()


TEMPLATE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "cn-thesis-generic",
        "name": "中文毕业论文通用版",
        "category": "thesis",
        "description": "基于 ctexbook 的可编译中文大论文骨架，适合作为默认毕业论文模板。",
        "language": "zh-CN",
        "source_type": "built-in",
        "source_name": "Scientific Agent",
        "source_url": "",
        "download_url": "",
        "license": "local",
        "prompt_hint": "适合中文本科/硕士/博士论文长文结构，强调摘要、章节和参考文献的完整性。",
        "starter_tex": _thesis_starter(),
    },
    {
        "id": "thuthesis",
        "name": "清华大学 thuthesis",
        "category": "thesis",
        "description": "清华大学论文模板，覆盖本科、硕士、博士和博士后报告。",
        "language": "zh-CN",
        "source_type": "official",
        "source_name": "CTAN / TUNA",
        "source_url": "https://ctan.org/pkg/thuthesis?lang=en",
        "download_url": "https://mirrors.ctan.org/install/macros/latex/contrib/thuthesis.tds.zip",
        "license": "LPPL 1.3c",
        "prompt_hint": "按清华 thuthesis 风格组织毕业论文，封面和声明页由用户后续按学校要求补充。",
        "starter_tex": _thesis_starter(),
    },
    {
        "id": "fduthesis",
        "name": "复旦大学 fduthesis",
        "category": "thesis",
        "description": "复旦大学中英文论文模板，可作中文高校毕业论文参考。",
        "language": "zh-CN",
        "source_type": "official",
        "source_name": "CTAN / FDUThesis",
        "source_url": "https://ctan.org/pkg/fduthesis?lang=en",
        "download_url": "https://mirrors.ctan.org/install/macros/latex/contrib/fduthesis.tds.zip",
        "license": "LPPL 1.3c",
        "prompt_hint": "适合规范化中文毕业论文结构，优先保持章结构、摘要和参考文献完整。",
        "starter_tex": _thesis_starter(),
    },
    {
        "id": "hithesis",
        "name": "哈尔滨工业大学 hithesis",
        "category": "thesis",
        "description": "哈尔滨工业大学论文模板，作为中文毕业论文默认基础模板。",
        "language": "zh-CN",
        "source_type": "official",
        "source_name": "GitHub / hithesis",
        "source_url": "https://github.com/hithesis/hithesis",
        "download_url": "https://codeload.github.com/hithesis/hithesis/zip/refs/heads/master",
        "license": "repository license",
        "prompt_hint": "默认中文毕业论文基础模板，优先保持 hithesis 的项目结构和编译方式。",
        "starter_tex": _thesis_starter(),
    },
    {
        "id": "cn-grant-generic",
        "name": "中文基金/项目申报书通用版",
        "category": "grant",
        "description": "通用中文科研项目申报书骨架，适合先起草内容再迁移到细分模板。",
        "language": "zh-CN",
        "source_type": "built-in",
        "source_name": "Scientific Agent",
        "source_url": "",
        "download_url": "",
        "license": "local",
        "prompt_hint": "适合国自然、重点研发、横向项目等中文申报书写作，强调问题、目标、内容、路线、创新、计划和风险。",
        "starter_tex": _grant_starter(),
    },
    {
        "id": "insfc-2026",
        "name": "iNSFC 2026 国自然模板",
        "category": "grant",
        "description": "活跃维护的国家自然科学基金 LaTeX 模板，非官方。",
        "language": "zh-CN",
        "source_type": "community",
        "source_name": "GitHub / YimianDai",
        "source_url": "https://github.com/YimianDai/iNSFC",
        "download_url": "https://codeload.github.com/YimianDai/iNSFC/zip/refs/heads/main",
        "license": "repository license",
        "prompt_hint": "按国自然正文结构组织内容，但蓝字和版式仍需逐年人工核对。",
        "starter_tex": _grant_starter(),
    },
    {
        "id": "nsfc-application-template",
        "name": "NSFC 面上项目模板",
        "category": "grant",
        "description": "国家自然科学基金申请书正文 LaTeX 模板，非官方。",
        "language": "zh-CN",
        "source_type": "community",
        "source_name": "GitHub / Ruzim",
        "source_url": "https://github.com/Ruzim/NSFC-application-template-latex",
        "download_url": "https://codeload.github.com/Ruzim/NSFC-application-template-latex/zip/refs/heads/main",
        "license": "MIT",
        "prompt_hint": "适合国自然面上项目正文草拟，但必须对照当年官方 Word 模板逐项核实。",
        "starter_tex": _grant_starter(),
    },
    {
        "id": "acmart-sigconf",
        "name": "ACM acmart 会议论文",
        "category": "conference_journal",
        "description": "ACM 官方 acmart 论文模板，适合 SIGCONF/SIGPLAN 等会议。",
        "language": "en",
        "source_type": "official",
        "source_name": "CTAN / ACM",
        "source_url": "https://ctan.org/pkg/acmart?lang=en",
        "download_url": "https://mirrors.ctan.org/install/macros/latex/contrib/acmart.tds.zip",
        "license": "LPPL 1.3",
        "prompt_hint": "适合 ACM 风格的英文会议或期刊论文，强调 abstract、keywords 和 contribution narrative。",
        "starter_tex": _acmart_starter(),
    },
    {
        "id": "ieeetran-conference",
        "name": "IEEEtran 会议/期刊模板",
        "category": "conference_journal",
        "description": "IEEE 官方风格文类，适合 conference 与 transactions 类稿件。",
        "language": "en",
        "source_type": "official",
        "source_name": "CTAN / IEEEtran",
        "source_url": "https://ctan.org/pkg/ieeetran?lang=en",
        "download_url": "https://mirrors.ctan.org/install/macros/latex/contrib/IEEEtran.tds.zip",
        "license": "LPPL 1.3",
        "prompt_hint": "适合 IEEE 会议或期刊文章，要求摘要、关键词、方法和实验写法更紧凑。",
        "starter_tex": _ieee_starter(),
    },
    {
        "id": "springer-lncs",
        "name": "Springer LNCS 模板",
        "category": "conference_journal",
        "description": "Springer Lecture Notes in Computer Science 会议论文风格。",
        "language": "en",
        "source_type": "official",
        "source_name": "Springer / CTAN",
        "source_url": "https://ctan.org/pkg/llncs?lang=en",
        "download_url": "https://mirrors.ctan.org/install/macros/latex/contrib/llncs.zip",
        "license": "CC BY 4.0",
        "prompt_hint": "适合 LNCS/CCIS 一类会议论文，优先保持精炼章节和关键词结构。",
        "starter_tex": _lncs_starter(),
    },
    {
        "id": "elsevier-elsarticle",
        "name": "Elsevier elsarticle 模板",
        "category": "conference_journal",
        "description": "Elsevier 官方投稿文类，适合预印本和多数期刊首稿格式。",
        "language": "en",
        "source_type": "official",
        "source_name": "Elsevier / CTAN",
        "source_url": "https://www.elsevier.com/en-gb/researcher/author/policies-and-guidelines/latex-instructions",
        "download_url": "https://mirrors.ctan.org/install/macros/latex/contrib/elsarticle.tds.zip",
        "license": "LPPL 1.3",
        "prompt_hint": "适合 Elsevier 稿件初稿，优先 frontmatter、abstract、keyword 与标准 section 结构。",
        "starter_tex": _elsevier_starter(),
    },
]


HITHESIS_CAMPUSES: dict[str, str] = {
    "harbin": "哈尔滨",
    "weihai": "威海",
    "shenzhen": "深圳",
}

HITHESIS_DEGREES: dict[str, str] = {
    "bachelor": "本科",
    "master": "硕士",
    "doctor": "博士",
    "postdoc": "博后",
}

HITHESIS_ENGLISH_DEGREES: dict[str, str] = {
    "bachelor": "Bachelor",
    "master": "Master",
    "doctor": "Doctor",
}

HITHESIS_STAGES: dict[str, str] = {
    "opening": "开题报告",
    "midterm": "中期报告",
}


def _hithesis_template_entry(
    template_id: str,
    name: str,
    description: str,
    language: str,
    prompt_hint: str,
    starter_tex: str,
    entry_root: str,
    main_tex: str,
    *,
    group: str,
    sort_key: str,
    document_class: str,
    degree_type: str,
    campus: str,
    stage: str = "",
    hidden: bool = False,
) -> dict[str, Any]:
    payload = {
        "id": template_id,
        "name": name,
        "category": "hithesis",
        "description": description,
        "language": language,
        "source_type": "official",
        "source_name": "GitHub / hithesis",
        "source_url": "https://github.com/hithesis/hithesis",
        "download_url": "https://codeload.github.com/hithesis/hithesis/zip/refs/heads/master",
        "license": "repository license",
        "prompt_hint": prompt_hint,
        "starter_tex": starter_tex,
        "entry_root": entry_root,
        "main_tex": main_tex,
        "group": group,
        "sort_key": sort_key,
        "hidden": hidden,
        "document_class": document_class,
        "degree_type": degree_type,
        "campus": campus,
        "stage": stage,
    }
    if language == "en":
        payload["language_variant"] = "english"
    elif stage:
        payload["language_variant"] = "report"
    else:
        payload["language_variant"] = "chinese"
    return payload


def _hithesis_thesis_template(campus: str, degree_type: str, language: str) -> dict[str, Any]:
    campus_label = HITHESIS_CAMPUSES[campus]
    degree_label = HITHESIS_DEGREES[degree_type]
    if language == "en":
        degree_en = HITHESIS_ENGLISH_DEGREES[degree_type]
        return _hithesis_template_entry(
            f"hithesis-{campus}-{degree_type}-en",
            f"HIT {campus_label}{degree_label}英文论文",
            f"哈尔滨工业大学{campus_label}校区{degree_label}英文毕业论文模板。",
            "en",
            f"适合哈尔滨工业大学{campus_label}校区 {degree_en} thesis，保持 hithesis 英文 thesis 目录结构与编译方式。",
            _thesis_starter(),
            "hithesis-master/examples/hitbook/english",
            "thesis.tex",
            group="英文毕业论文",
            sort_key=f"2-{list(HITHESIS_CAMPUSES).index(campus):02d}-{list(HITHESIS_ENGLISH_DEGREES).index(degree_type):02d}",
            document_class="hithesisbook",
            degree_type=degree_type,
            campus=campus,
        )
    return _hithesis_template_entry(
        f"hithesis-{campus}-{degree_type}-cn",
        f"HIT {campus_label}{degree_label}中文论文",
        f"哈尔滨工业大学{campus_label}校区{degree_label}中文毕业论文模板。",
        "zh-CN",
        f"适合哈尔滨工业大学{campus_label}校区{degree_label}中文论文，保持 hithesis thesis 结构、摘要和参考文献布局。",
        _thesis_starter(),
        "hithesis-master/examples/hitbook/chinese",
        "thesis.tex",
        group="中文毕业论文",
        sort_key=f"1-{list(HITHESIS_CAMPUSES).index(campus):02d}-{list(HITHESIS_DEGREES).index(degree_type):02d}",
        document_class="hithesisbook",
        degree_type=degree_type,
        campus=campus,
    )


def _hithesis_report_template(campus: str, degree_type: str, stage: str) -> dict[str, Any]:
    campus_label = HITHESIS_CAMPUSES[campus]
    degree_label = HITHESIS_DEGREES[degree_type]
    stage_label = HITHESIS_STAGES[stage]
    is_reportplus = campus == "shenzhen" and degree_type == "doctor" and stage == "midterm"
    return _hithesis_template_entry(
        f"hithesis-{campus}-{degree_type}-{stage}",
        f"HIT {campus_label}{degree_label}{stage_label}",
        f"哈尔滨工业大学{campus_label}校区{degree_label}{stage_label}模板。",
        "zh-CN",
        f"适合哈尔滨工业大学{campus_label}校区{degree_label}{stage_label}，保持 hithesis 报告类封面和章节结构。",
        _report_starter(),
        "hithesis-master/examples/hitart/reportplus" if is_reportplus else "hithesis-master/examples/hitart/reports",
        "report.tex",
        group="开题与中期报告",
        sort_key=f"3-{list(HITHESIS_CAMPUSES).index(campus):02d}-{list(HITHESIS_DEGREES).index(degree_type):02d}-{list(HITHESIS_STAGES).index(stage):02d}",
        document_class="hithesisartplus" if is_reportplus else "hithesisart",
        degree_type=degree_type,
        campus=campus,
        stage=stage,
    )


def _legacy_hithesis_alias(
    alias_id: str,
    target_id: str,
    *,
    name: str,
    description: str,
) -> dict[str, Any]:
    target = {
        item["id"]: item
        for item in (
            [_hithesis_thesis_template(campus, degree_type, "zh-CN") for campus in HITHESIS_CAMPUSES for degree_type in HITHESIS_DEGREES]
            + [_hithesis_thesis_template(campus, degree_type, "en") for campus in HITHESIS_CAMPUSES for degree_type in HITHESIS_ENGLISH_DEGREES]
            + [_hithesis_report_template(campus, degree_type, stage) for campus in HITHESIS_CAMPUSES for degree_type in ("bachelor", "master", "doctor") for stage in HITHESIS_STAGES]
        )
    }[target_id]
    payload = dict(target)
    payload["id"] = alias_id
    payload["name"] = name
    payload["description"] = description
    payload["hidden"] = True
    payload["alias_for"] = target_id
    return payload


def _hithesis_templates() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for campus in HITHESIS_CAMPUSES:
        for degree_type in HITHESIS_DEGREES:
            items.append(_hithesis_thesis_template(campus, degree_type, "zh-CN"))
    for campus in HITHESIS_CAMPUSES:
        for degree_type in HITHESIS_ENGLISH_DEGREES:
            items.append(_hithesis_thesis_template(campus, degree_type, "en"))
    for campus in HITHESIS_CAMPUSES:
        for degree_type in ("bachelor", "master", "doctor"):
            for stage in HITHESIS_STAGES:
                items.append(_hithesis_report_template(campus, degree_type, stage))
    items.extend(
        [
            _legacy_hithesis_alias(
                "hithesis-bachelor-cn",
                "hithesis-harbin-bachelor-cn",
                name="HIT 本科中文论文",
                description="兼容旧项目 ID，映射到哈尔滨校区本科中文论文模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-master-cn",
                "hithesis-harbin-master-cn",
                name="HIT 硕士中文论文",
                description="兼容旧项目 ID，映射到哈尔滨校区硕士中文论文模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-doctor-cn",
                "hithesis-harbin-doctor-cn",
                name="HIT 博士中文论文",
                description="兼容旧项目 ID，映射到哈尔滨校区博士中文论文模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-postdoc-report",
                "hithesis-harbin-postdoc-cn",
                name="HIT 博后出站报告",
                description="兼容旧项目 ID，映射到哈尔滨校区博后出站报告模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-bachelor-en",
                "hithesis-harbin-bachelor-en",
                name="HIT 本科英文论文",
                description="兼容旧项目 ID，映射到哈尔滨校区本科英文论文模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-doctor-en",
                "hithesis-harbin-doctor-en",
                name="HIT 博士英文论文",
                description="兼容旧项目 ID，映射到哈尔滨校区博士英文论文模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-bachelor-opening",
                "hithesis-harbin-bachelor-opening",
                name="HIT 本科开题报告",
                description="兼容旧项目 ID，映射到哈尔滨校区本科开题报告模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-bachelor-midterm",
                "hithesis-harbin-bachelor-midterm",
                name="HIT 本科中期报告",
                description="兼容旧项目 ID，映射到哈尔滨校区本科中期报告模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-doctor-opening",
                "hithesis-shenzhen-doctor-opening",
                name="HIT 博士开题报告",
                description="兼容旧项目 ID，映射到深圳校区博士开题报告模板。",
            ),
            _legacy_hithesis_alias(
                "hithesis-doctor-midterm",
                "hithesis-shenzhen-doctor-midterm",
                name="HIT 博士中期报告",
                description="兼容旧项目 ID，映射到深圳校区博士中期报告模板。",
            ),
        ]
    )
    return items


def _load_project_api_key() -> str:
    if not DEFAULT_KIMI_KEY_FILE.exists():
        return ""
    return DEFAULT_KIMI_KEY_FILE.read_text(encoding="utf-8").strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "template"


def _dynamic_index_path() -> Path:
    return TEMPLATE_LIBRARY_ROOT / "dynamic_templates.json"


def _load_dynamic_templates() -> list[dict[str, Any]]:
    path = _dynamic_index_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def _save_dynamic_templates(items: list[dict[str, Any]]) -> None:
    TEMPLATE_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    _dynamic_index_path().write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _all_templates() -> list[dict[str, Any]]:
    return sorted(
        _hithesis_templates(),
        key=lambda item: (
            bool(item.get("hidden")),
            str(item.get("sort_key") or ""),
            str(item.get("name") or ""),
        ),
    )


def _template_by_id(template_id: str) -> dict[str, Any]:
    for item in _all_templates():
        if item["id"] == template_id:
            return item
    raise KeyError(template_id)


def _template_root(template_id: str) -> Path:
    if template_id.startswith("hithesis-"):
        return TEMPLATE_LIBRARY_ROOT / "hithesis"
    return TEMPLATE_LIBRARY_ROOT / template_id


def _archive_name(template: dict[str, Any]) -> str:
    download_url = str(template.get("download_url") or "").rstrip("/")
    if not download_url:
        return ""
    leaf = download_url.split("/")[-1]
    if not leaf:
        return f"{template['id']}.zip"
    if Path(leaf).suffix:
        return leaf
    if "/zip/" in download_url:
        return f"{leaf}.zip"
    return f"{leaf}.zip"


def _status_payload(template: dict[str, Any]) -> dict[str, Any]:
    template_root = _template_root(template["id"])
    archive_path = template_root / _archive_name(template) if _archive_name(template) else None
    manifest_path = template_root / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    extracted_dir = template_root / "extracted"
    return {
        "cached": bool(extracted_dir.exists() or (archive_path and archive_path.exists())),
        "archive_path": str(archive_path) if archive_path and archive_path.exists() else "",
        "extracted_path": str(extracted_dir) if extracted_dir.exists() else "",
        "downloaded_at": manifest.get("downloaded_at", ""),
        "download_error": manifest.get("download_error", ""),
    }


def _public_template(template: dict[str, Any], include_source: bool = False) -> dict[str, Any]:
    payload = {key: value for key, value in template.items() if key != "starter_tex"}
    payload["status"] = _status_payload(template)
    if include_source:
        payload["starter_tex"] = template["starter_tex"]
    return payload


def _starter_for_category(category: str, request_text: str = "") -> str:
    if category == "grant":
        return _grant_starter()
    if category == "conference_journal":
        lower = request_text.lower()
        if "ieee" in lower:
            return _ieee_starter()
        if "acm" in lower:
            return _acmart_starter()
        if "springer" in lower or "lncs" in lower:
            return _lncs_starter()
        if "elsevier" in lower:
            return _elsevier_starter()
        return _acmart_starter()
    return _thesis_starter()


def _guess_category(request_text: str) -> str:
    text = request_text.lower()
    if any(token in request_text for token in ["基金", "申报", "项目申请", "国自然"]) or any(token in text for token in ["grant", "proposal", "nsfc", "insfc"]):
        return "grant"
    if any(token in request_text for token in ["会议", "期刊", "journal", "conference", "acm", "ieee", "springer", "elsevier", "lncs"]):
        return "conference_journal"
    return "thesis"


def _dynamic_template_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    request_text = str(plan.get("request") or "")
    category = str(plan.get("category") or _guess_category(request_text))
    template_name = str(plan.get("name") or request_text or "Dynamic Template").strip()
    source_type = str(plan.get("source_type") or "community")
    source_name = str(plan.get("source_name") or source_type.upper())
    source_url = str(plan.get("source_url") or "")
    download_url = str(plan.get("download_url") or "")
    template_id = str(plan.get("id") or "")
    if not template_id:
        if source_type == "ctan":
            package = str(plan.get("ctan_package") or "").strip()
            template_id = f"ctan-{_slug(package or template_name)}"
        elif source_type == "github":
            owner = _slug(str(plan.get("github_owner") or "github"))
            repo = _slug(str(plan.get("github_repo") or template_name))
            template_id = f"github-{owner}-{repo}"
        else:
            template_id = f"dynamic-{_slug(template_name)}"
    return {
        "id": template_id,
        "name": template_name,
        "category": category,
        "description": str(plan.get("description") or f"动态下载模板：{template_name}"),
        "language": str(plan.get("language") or ("zh-CN" if category != "conference_journal" else "en")),
        "source_type": source_type,
        "source_name": source_name,
        "source_url": source_url,
        "download_url": download_url,
        "license": str(plan.get("license") or "unknown"),
        "prompt_hint": str(plan.get("prompt_hint") or ""),
        "starter_tex": _starter_for_category(category, request_text=request_text),
    }


def _register_dynamic_template(template: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in _load_dynamic_templates() if item.get("id") != template["id"]]
    items.append(template)
    _save_dynamic_templates(items)
    return template


def _chat_completion(
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: int = 90,
) -> str:
    if not api_key:
        raise RuntimeError("Missing Kimi API key.")
    payload = {"model": model, "messages": messages}
    req = request.Request(
        f"{KIMI_API_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Kimi API error: {exc.code} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Kimi network error: {exc}") from exc
    return (
        body.get("choices", [{}])[0]
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
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _ctan_download_url(package: str, install_variant: str = "tds") -> str:
    suffix = ".tds.zip" if install_variant != "zip" else ".zip"
    return f"https://mirrors.ctan.org/install/macros/latex/contrib/{package}{suffix}"


def _github_codeload_url(owner: str, repo: str, ref: str = "main") -> str:
    return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"


def _ctan_plan(package: str, request_text: str = "") -> dict[str, Any]:
    package = package.strip()
    return {
        "id": f"ctan-{_slug(package)}",
        "request": request_text or package,
        "name": f"{package} (CTAN)",
        "category": _guess_category(request_text or package),
        "description": f"从 CTAN 动态下载的模板包 {package}",
        "language": "zh-CN" if _guess_category(request_text or package) != "conference_journal" else "en",
        "source_type": "ctan",
        "source_name": "CTAN",
        "source_url": f"https://ctan.org/pkg/{package}?lang=en",
        "download_url": _ctan_download_url(package),
        "license": "unknown",
        "ctan_package": package,
    }


def _github_plan(owner: str, repo: str, ref: str = "main", request_text: str = "") -> dict[str, Any]:
    return {
        "id": f"github-{_slug(owner)}-{_slug(repo)}",
        "request": request_text or f"{owner}/{repo}",
        "name": f"{owner}/{repo}",
        "category": _guess_category(request_text or repo),
        "description": f"从 GitHub 动态下载的模板仓库 {owner}/{repo}",
        "language": "zh-CN" if _guess_category(request_text or repo) != "conference_journal" else "en",
        "source_type": "github",
        "source_name": "GitHub",
        "source_url": f"https://github.com/{owner}/{repo}",
        "download_url": _github_codeload_url(owner, repo, ref=ref),
        "license": "repository license",
        "github_owner": owner,
        "github_repo": repo,
        "github_ref": ref,
    }


def _plan_from_reference(reference: str) -> dict[str, Any]:
    ref = reference.strip()
    if not ref:
        return {}
    lower = ref.lower()
    if lower.startswith("ctan:"):
        return _ctan_plan(ref.split(":", 1)[1].strip(), request_text=ref)
    if lower.startswith("github:"):
        value = ref.split(":", 1)[1].strip()
        if "/" in value:
            owner, repo = value.split("/", 1)
            return _github_plan(owner.strip(), repo.strip(), request_text=ref)
    if ref.startswith("http://") or ref.startswith("https://"):
        parsed = urlparse(ref)
        if parsed.netloc.endswith("github.com"):
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2:
                return _github_plan(parts[0], parts[1], request_text=ref)
    if "/" in ref and " " not in ref:
        owner, repo = ref.split("/", 1)
        return _github_plan(owner.strip(), repo.strip(), request_text=ref)
    return {}


def list_templates(category: str = "") -> dict[str, Any]:
    items = [
        _public_template(template)
        for template in _all_templates()
        if (not category or template["category"] == category) and not bool(template.get("hidden"))
    ]
    categories = [
        {
            "id": "hithesis",
            "label": TEMPLATE_CATEGORIES["hithesis"],
            "count": len(items),
        }
    ]
    return {"categories": categories, "items": items}


def get_template(template_id: str, include_source: bool = False) -> dict[str, Any]:
    template = _template_by_id(template_id)
    return _public_template(template, include_source=include_source)


def render_template_starter(
    template_id: str,
    title: str = "",
    author: str = "Scientific Agent",
) -> str:
    template = _template_by_id(template_id)
    starter = str(template["starter_tex"])
    return (
        starter
        .replace("__TITLE__", title or template["name"])
        .replace("__AUTHOR__", author)
    )


def get_template_structure(template_id: str) -> dict[str, Any]:
    """Extract document structure from a template's main tex file.

    Returns document class, heading convention, and the list of
    \\include/\\input'd files categorized by frontmatter/mainmatter/backmatter.
    """
    template = _template_by_id(template_id)
    document_class = str(template.get("document_class") or "").strip()
    main_tex = str(template.get("main_tex") or "main.tex").strip()
    entry_root = str(template.get("entry_root") or "").strip()
    starter_tex = str(template.get("starter_tex") or "")

    content = ""
    if entry_root:
        template_root = _template_root(template_id)
        extracted_dir = template_root / "extracted"
        tex_path = extracted_dir / entry_root / main_tex
        if tex_path.exists():
            try:
                content = tex_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    if not content and entry_root:
        template_root = _template_root(template_id)
        extracted_dir = template_root / "extracted"
        tex_path = extracted_dir / main_tex
        if tex_path.exists():
            try:
                content = tex_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    if not content:
        content = starter_tex

    document_class_from_tex = ""
    dc_match = re.search(r"\\documentclass(?:\[.*?\])?\{(.*?)\}", content)
    if dc_match:
        document_class_from_tex = dc_match.group(1).strip()
    if not document_class:
        document_class = document_class_from_tex
    if not document_class and document_class_from_tex:
        document_class = document_class_from_tex

    book_classes = {
        "book", "ctexbook", "hithesisbook", "scrbook", "memoir",
        "report", "ctexrep", "scrreprt",
    }
    is_book_like = (
        document_class in book_classes
        or "ctexbook" in content
        or "hithesisbook" in document_class
    )

    heading_command = r"\chapter" if is_book_like else r"\section"

    current_section = "preamble"
    frontmatter_files: list[str] = []
    chapters: list[str] = []
    backmatter_files: list[str] = []
    all_inputs: list[dict[str, str]] = []

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
        if r"\backmatter" in stripped:
            current_section = "backmatter"
            continue
        if r"\appendix" in stripped:
            current_section = "backmatter"
            continue

        for pattern in (r"\\include\{([^}]+)\}", r"\\input\{([^}]+)\}"):
            for match in re.finditer(pattern, stripped):
                rel_path = match.group(1).strip()
                if not rel_path:
                    continue
                if not rel_path.lower().endswith(".tex"):
                    rel_path += ".tex"
                all_inputs.append({"section": current_section, "path": rel_path})
                if current_section == "frontmatter":
                    frontmatter_files.append(rel_path)
                elif current_section == "mainmatter":
                    chapters.append(rel_path)
                elif current_section == "backmatter":
                    backmatter_files.append(rel_path)

    return {
        "template_id": template_id,
        "document_class": document_class,
        "is_book_like": is_book_like,
        "heading_command": heading_command,
        "main_tex": main_tex,
        "frontmatter_files": frontmatter_files,
        "chapters": chapters,
        "backmatter_files": backmatter_files,
        "all_inputs": all_inputs,
    }


def find_template(query: str) -> dict[str, Any]:
    normalized = query.strip().lower()
    if not normalized:
        return {}
    for template in _all_templates():
        haystacks = [
            str(template.get("id", "")).lower(),
            str(template.get("name", "")).lower(),
            str(template.get("description", "")).lower(),
            str(template.get("source_url", "")).lower(),
        ]
        if any(normalized == haystack or normalized in haystack for haystack in haystacks if haystack):
            return template
    return {}


def resolve_template_request(
    request_text: str,
    api_key: str = "",
    model: str = "kimi-k2.5",
) -> dict[str, Any]:
    request_text = str(request_text or "").strip()
    if not request_text:
        raise ValueError("template request is required")

    raise RuntimeError(
        "Dynamic template resolution is disabled. Upload your LaTeX project or template source manually."
    )


def download_template(template_id: str, timeout: int = 180) -> dict[str, Any]:
    template = _template_by_id(template_id)
    download_url = str(template.get("download_url") or "").strip()
    template_root = _template_root(template_id)
    template_root.mkdir(parents=True, exist_ok=True)
    archive_path = template_root / _archive_name(template)
    extracted_dir = template_root / "extracted"
    manifest_path = template_root / "manifest.json"

    if not download_url:
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)
        extracted_dir.mkdir(parents=True, exist_ok=True)
        (extracted_dir / "main.tex").write_text(
            render_template_starter(
                template_id,
                title=template.get("name", ""),
                author="Scientific Agent",
            ),
            encoding="utf-8",
        )
        manifest = {
            "template_id": template_id,
            "downloaded_at": _utc_stamp(),
            "source_url": template["source_url"],
            "download_url": "",
            "archive_path": "",
            "download_error": "",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return get_template(template_id, include_source=True)

    try:
        with request.urlopen(download_url, timeout=timeout) as response:
            archive_path.write_bytes(response.read())
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)
        extracted_dir.mkdir(parents=True, exist_ok=True)
        if archive_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive_path) as handle:
                handle.extractall(extracted_dir)
        manifest = {
            "template_id": template_id,
            "downloaded_at": _utc_stamp(),
            "source_url": template["source_url"],
            "download_url": download_url,
            "archive_path": str(archive_path),
            "download_error": "",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except (error.URLError, TimeoutError, zipfile.BadZipFile, OSError) as exc:
        manifest = {
            "template_id": template_id,
            "downloaded_at": "",
            "source_url": template["source_url"],
            "download_url": download_url,
            "archive_path": str(archive_path) if archive_path.exists() else "",
            "download_error": str(exc),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"template download failed: {exc}") from exc
    return get_template(template_id, include_source=True)
