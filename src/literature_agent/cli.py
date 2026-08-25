"""User-facing command line interface for Scientific Agent."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .config import (
    EXAMPLE_CONFIG_PATH,
    PROJECT_ROOT,
    USER_CONFIG_PATH,
    initialize_config,
    load_config,
)


def _json_dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _add_config_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        dest="config_path",
        default=argparse.SUPPRESS,
        help="YAML config path (default: configs/library_rules.yaml when initialized).",
    )


def _config(args: argparse.Namespace):
    return load_config(getattr(args, "config_path", None))


def _command_init(args: argparse.Namespace) -> int:
    try:
        path = initialize_config(args.path, force=args.force)
    except (FileExistsError, ValueError) as exc:
        print(f"初始化失败: {exc}", file=sys.stderr)
        return 2
    print(f"已创建用户配置: {path}")
    print("下一步: scientific-agent check && scientific-agent serve")
    return 0


def _command_check(args: argparse.Namespace) -> int:
    checks: list[tuple[str, str, str]] = []
    try:
        config = _config(args)
    except (OSError, ValueError) as exc:
        checks.append(("ERROR", "配置文件", str(exc)))
        _print_checks(checks, as_json=args.json)
        return 1

    checks.append(("OK", "Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"))
    checks.append(("OK", "配置文件", str(config.path)))
    if config.path.resolve() == EXAMPLE_CONFIG_PATH.resolve():
        checks.append(("WARN", "用户配置", "尚未初始化；运行 `scientific-agent init` 可创建可写配置"))
    else:
        checks.append(("OK", "用户配置", "已初始化"))

    required_sections = ("storage", "search", "classifier")
    missing = [name for name in required_sections if not isinstance(config.raw.get(name), dict)]
    if missing:
        checks.append(("ERROR", "配置结构", f"缺少或无效 section: {', '.join(missing)}"))
    else:
        checks.append(("OK", "配置结构", "storage/search/classifier 可用"))

    storage_parent = config.root_dir.parent
    if storage_parent.exists() and os.access(storage_parent, os.W_OK):
        checks.append(("OK", "数据目录", f"{config.root_dir}（首次运行时自动创建）"))
    else:
        checks.append(("WARN", "数据目录", f"父目录不存在或不可写: {storage_parent}"))

    if shutil.which("xelatex") or shutil.which("pdflatex") or shutil.which("tectonic"):
        checks.append(("OK", "LaTeX", "检测到可用编译器"))
    else:
        checks.append(("WARN", "LaTeX", "未检测到 xelatex/pdflatex/tectonic，写作 PDF 编译将跳过"))

    _print_checks(checks, as_json=args.json)
    return 1 if any(level == "ERROR" for level, _, _ in checks) else 0


def _print_checks(checks: list[tuple[str, str, str]], *, as_json: bool) -> None:
    if as_json:
        _json_dump([{"level": level, "name": name, "message": message} for level, name, message in checks])
        return
    print("Scientific Agent 环境检查")
    for level, name, message in checks:
        print(f"[{level:<5}] {name}: {message}")


def _command_serve(args: argparse.Namespace) -> int:
    config_path = getattr(args, "config_path", None)
    if config_path:
        os.environ["LIT_AGENT_CONFIG"] = str(_config(args).path)
    from .server import main as server_main

    server_main(host=args.host, port=args.port)
    return 0


def _command_search(args: argparse.Namespace) -> int:
    from .search_pipeline import run_search_pipeline

    result = run_search_pipeline(
        _config(args),
        query=args.query,
        max_results=args.max_results,
        auto_download=args.auto_download,
        min_score=args.min_score,
    )
    _json_dump(result)
    return 0


def _command_attention(args: argparse.Namespace) -> int:
    from .attention_pipeline import run_attention_pipeline

    payload = {
        "query": args.query,
        "force_refresh": args.force_refresh,
        "strict_ai_only": args.strict_ai_only,
        "include_references": args.include_references,
        "disable_readability_fetch": args.disable_readability_fetch,
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
    }
    if args.max_results > 0:
        payload["max_results"] = args.max_results
    if args.min_score is not None:
        payload["min_score"] = args.min_score
    if args.summarize_limit > 0:
        payload["summarize_limit"] = args.summarize_limit
    result = run_attention_pipeline(_config(args), payload)
    _json_dump(result)
    return 0


def _command_library(args: argparse.Namespace) -> int:
    from .library_store import library_stats, search_library

    config = _config(args)
    _json_dump({"query": args.query, "stats": library_stats(config), "items": search_library(config, args.query, limit=args.limit)})
    return 0


def _command_report(args: argparse.Namespace) -> int:
    from .survey_reporting import generate_library_survey_report

    result = generate_library_survey_report(
        _config(args),
        {
            "query": args.query,
            "limit": args.limit,
            "language": args.language,
            "days": args.days,
            "title": args.title,
            "report_kind": args.report_kind,
        },
    )
    _json_dump(result)
    return 0


def _command_research(args: argparse.Namespace) -> int:
    from .research_workflow import run_research_workflow

    result = run_research_workflow(
        _config(args),
        {
            "goal": args.goal,
            "query": args.query,
            "writing_type": args.writing_type,
            "writing_language": args.writing_language,
            "template_id": args.template_id,
            "requirements": args.requirements,
            "use_literature_pipeline": args.crawl,
            "rag_limit": args.rag_limit,
            "api_key": args.api_key,
        },
    )
    _json_dump(result)
    return 0 if result.get("status") != "failed" else 1


def _command_kb_sync(args: argparse.Namespace) -> int:
    from .knowledge_sync import knowledge_base_settings, sync_lark, sync_obsidian

    config = _config(args)
    settings = knowledge_base_settings(config)
    selected = {name for name, flag in (("obsidian", args.obsidian), ("lark", args.lark)) if flag}
    targets = selected or {name for name in ("obsidian", "lark") if settings[name]["enabled"]}
    if not targets:
        targets = {"obsidian"}

    result: dict[str, Any] = {}
    if "obsidian" in targets:
        result["obsidian"] = sync_obsidian(config, vault=args.vault or None)
    if "lark" in targets:
        result["lark"] = sync_lark(config, limit=args.limit)
    _json_dump(result)
    failed = any(
        isinstance(section, dict) and section.get("status") in {"failed", "partial"}
        for section in result.values()
    )
    return 1 if failed else 0


def _command_kb_status(args: argparse.Namespace) -> int:
    from .knowledge_sync import knowledge_base_status

    _json_dump(knowledge_base_status(_config(args)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    formatter = argparse.ArgumentDefaultsHelpFormatter
    parser = argparse.ArgumentParser(
        prog="scientific-agent",
        description="本地科研文献检索、注意力管理与写作工作台。",
        formatter_class=formatter,
    )
    parser.add_argument("--version", action="version", version=f"scientific-agent {__version__}")
    _add_config_option(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="从示例创建用户配置", formatter_class=formatter)
    init.add_argument("--path", default=str(USER_CONFIG_PATH), help="目标配置路径")
    init.add_argument("--force", action="store_true", help="覆盖已有用户配置")
    init.set_defaults(handler=_command_init)

    check = subparsers.add_parser("check", help="检查配置、目录和可选工具", formatter_class=formatter)
    _add_config_option(check)
    check.add_argument("--json", action="store_true", help="以 JSON 输出检查结果")
    check.set_defaults(handler=_command_check)

    serve = subparsers.add_parser("serve", help="启动本地 Web 服务", formatter_class=formatter)
    _add_config_option(serve)
    serve.add_argument("--host", default=os.environ.get("LIT_AGENT_HOST", "127.0.0.1"), help="监听地址")
    serve.add_argument("--port", type=int, default=int(os.environ.get("LIT_AGENT_PORT", "8765")), help="监听端口")
    serve.set_defaults(handler=_command_serve)

    search = subparsers.add_parser("search", help="搜索并排序文献", formatter_class=formatter)
    _add_config_option(search)
    search.add_argument("query", help="检索关键词")
    search.add_argument("--max-results", type=int, default=20, help="最多返回结果数")
    search.add_argument("--min-score", type=float, default=None, help="最低相关性分数")
    search.add_argument("--auto-download", action="store_true", help="自动下载可用 PDF")
    search.set_defaults(handler=_command_search)

    attention = subparsers.add_parser("attention", help="运行 RSS/论文注意力流水线", formatter_class=formatter)
    _add_config_option(attention)
    attention.add_argument("--query", default="", help="附加检索词")
    attention.add_argument("--max-results", type=int, default=0)
    attention.add_argument("--min-score", type=float, default=None)
    attention.add_argument("--summarize-limit", type=int, default=0)
    attention.add_argument("--force-refresh", action="store_true")
    attention.add_argument("--strict-ai-only", action="store_true")
    attention.add_argument("--include-references", action="store_true")
    attention.add_argument("--disable-readability-fetch", action="store_true")
    attention.add_argument("--api-key", default="")
    attention.add_argument("--base-url", default="")
    attention.add_argument("--model", default="")
    attention.set_defaults(handler=_command_attention)

    library = subparsers.add_parser("library", help="查询本地文献库", formatter_class=formatter)
    _add_config_option(library)
    library.add_argument("query", nargs="?", default="")
    library.add_argument("--limit", type=int, default=10)
    library.set_defaults(handler=_command_library)

    report = subparsers.add_parser("report", help="生成文献综述报告", formatter_class=formatter)
    _add_config_option(report)
    report.add_argument("query", nargs="?", default="")
    report.add_argument("--limit", type=int, default=16)
    report.add_argument("--language", choices=["zh", "en"], default="zh")
    report.add_argument("--days", type=int, default=7)
    report.add_argument("--title", default="")
    report.add_argument("--report-kind", default="survey")
    report.set_defaults(handler=_command_report)

    research = subparsers.add_parser("research", help="运行规划、检索、RAG 与写作流程", formatter_class=formatter)
    _add_config_option(research)
    research.add_argument("goal", help="写作目标")
    research.add_argument("--query", default="")
    research.add_argument("--writing-type", choices=["academic", "grant"], default="academic")
    research.add_argument("--writing-language", choices=["", "zh", "en"], default="")
    research.add_argument("--template-id", default="")
    research.add_argument("--requirements", default="")
    research.add_argument("--rag-limit", type=int, default=0)
    research.add_argument("--crawl", action="store_true")
    research.add_argument("--api-key", default="")
    research.set_defaults(handler=_command_research)

    kb = subparsers.add_parser("kb", help="同步文献库到 Obsidian / 飞书知识库", formatter_class=formatter)
    kb_sub = kb.add_subparsers(dest="kb_command", required=True)

    kb_sync = kb_sub.add_parser("sync", help="同步文献到知识库（只保留 PDF 下载链接）", formatter_class=formatter)
    _add_config_option(kb_sync)
    kb_sync.add_argument("--obsidian", action="store_true", help="只同步到 Obsidian")
    kb_sync.add_argument("--lark", action="store_true", help="只同步到飞书（需要 lark-cli）")
    kb_sync.add_argument("--vault", default="", help="Obsidian 仓库路径（默认自动检测）")
    kb_sync.add_argument("--limit", type=int, default=0, help="最多同步多少篇（0 为全部）")
    kb_sync.set_defaults(handler=_command_kb_sync)

    kb_status = kb_sub.add_parser("status", help="查看知识库同步配置与状态", formatter_class=formatter)
    _add_config_option(kb_status)
    kb_status.set_defaults(handler=_command_kb_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:  # Keep CLI failures actionable for non-developers.
        if os.environ.get("SCIENTIFIC_AGENT_DEBUG"):
            raise
        print(f"命令失败: {exc}", file=sys.stderr)
        print("提示: 设置 SCIENTIFIC_AGENT_DEBUG=1 可查看完整堆栈。", file=sys.stderr)
        return 1
