"""Sync the local literature library into external knowledge bases.

Obsidian: renders one Markdown note per paper plus topic index notes into a
vault folder. Lark/Feishu: pushes the same Markdown notes to Lark Drive via
the official ``lark-cli`` binary (``npm install -g @larksuite/cli``).

Notes only keep the PDF download link — PDF files are never copied or
uploaded, the local library stays the single source of truth.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import AppConfig
from .library_store import (
    _connect,
    _library_item,
    ensure_graph_store,
)

DEFAULT_SUBDIR = "Literature"
PAPERS_DIRNAME = "papers"
TOPICS_DIRNAME = "topics"
INDEX_NOTE_NAME = "文献库首页"
LARK_STATE_DIRNAME = "kb_sync"
LARK_STATE_FILENAME = "lark_files.json"

LARK_INSTALL_HINT = (
    "安装并登录 lark-cli: npm install -g @larksuite/cli && "
    "lark-cli config init && lark-cli auth login --recommend"
)

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|#\^\[\]\r\n]+')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def knowledge_base_settings(config: AppConfig) -> dict[str, dict[str, Any]]:
    raw = config.raw.get("knowledge_base", {}) or {}
    obsidian = raw.get("obsidian", {}) or {}
    lark = raw.get("lark", {}) or {}
    return {
        "obsidian": {
            "enabled": bool(obsidian.get("enabled", True)),
            "vault": str(obsidian.get("vault", "") or ""),
            "subdir": str(obsidian.get("subdir", DEFAULT_SUBDIR) or DEFAULT_SUBDIR),
        },
        "lark": {
            "enabled": bool(lark.get("enabled", False)),
            "cli": str(lark.get("cli", "lark-cli") or "lark-cli"),
            "folder_token": str(lark.get("folder_token", "") or ""),
            "as": str(lark.get("as", "") or ""),
        },
    }


def detect_obsidian_vault(home: Path | None = None) -> Path | None:
    """Return the most relevant vault from Obsidian's own registry."""
    home = home or Path.home()
    registry = home / ".config" / "obsidian" / "obsidian.json"
    try:
        data = json.loads(registry.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vaults = [
        entry
        for entry in (data.get("vaults") or {}).values()
        if isinstance(entry, dict) and entry.get("path")
    ]
    if not vaults:
        return None

    def _rank(entry: dict[str, Any]) -> tuple[bool, float]:
        return (bool(entry.get("open")), float(entry.get("ts") or 0))

    best = max(vaults, key=_rank)
    path = Path(str(best["path"])).expanduser()
    return path if path.is_dir() else None


def _sanitize_note_name(title: str, paper_id: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    if not cleaned:
        cleaned = f"untitled-{paper_id[:8]}"
    return cleaned[:80].rstrip()


def _topic_rows(conn, paper_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.slug, t.label, pt.weight
        FROM paper_topics pt
        JOIN topic_nodes t ON t.topic_id=pt.topic_id
        WHERE pt.paper_id=?
        ORDER BY pt.weight DESC, t.label ASC
        """,
        (paper_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _collect_library(config: AppConfig, limit: int = 0) -> list[dict[str, Any]]:
    ensure_graph_store(config)
    with _connect(config) as conn:
        rows = conn.execute(
            "SELECT paper_id FROM papers ORDER BY last_seen_at DESC"
        ).fetchall()
        paper_ids = [str(row[0]) for row in rows]
        if limit > 0:
            paper_ids = paper_ids[:limit]
        items: list[dict[str, Any]] = []
        for paper_id in paper_ids:
            item = _library_item(conn, paper_id, None)
            if not item:
                continue
            item["topics"] = _topic_rows(conn, paper_id)
            items.append(item)
        return items


def _assign_note_names(items: list[dict[str, Any]]) -> None:
    used: dict[str, str] = {}
    for item in items:
        name = _sanitize_note_name(item.get("title", ""), item["paper_id"])
        owner = used.get(name)
        if owner is not None and owner != item["paper_id"]:
            name = f"{name}-{item['paper_id'][:8]}"
        used[name] = item["paper_id"]
        item["note_name"] = name


def render_paper_note(
    item: dict[str, Any],
    *,
    wiki_links: bool = True,
) -> str:
    topics = item.get("topics") or []
    frontmatter: dict[str, Any] = {
        "title": item.get("title") or "Untitled",
        "authors": item.get("authors") or [],
        "year": item.get("year") or "",
        "venue": item.get("venue") or "",
        "doi": item.get("doi") or "",
        "score": item.get("score"),
        "priority": item.get("priority") or "",
        "tags": ["literature"] + [f"topic/{topic['slug']}" for topic in topics],
        "page_url": item.get("page_url") or "",
        "pdf_url": item.get("pdf_url") or "",
        "paper_id": item.get("paper_id") or "",
    }
    frontmatter = {
        key: value
        for key, value in frontmatter.items()
        if value not in ("", None, [])
    }

    title = re.sub(r"[\r\n]+", " ", item.get("title") or "Untitled").strip()
    lines: list[str] = [
        "---",
        yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# {title}",
        "",
        "## 链接",
    ]
    if item.get("page_url"):
        lines.append(f"- [论文页面]({item['page_url']})")
    if item.get("pdf_url"):
        lines.append(f"- [PDF 下载]({item['pdf_url']})")
    if item.get("doi"):
        lines.append(f"- DOI: `{item['doi']}`")
    if not any([item.get("page_url"), item.get("pdf_url"), item.get("doi")]):
        lines.append("- （暂无链接）")

    lines += ["", "## 摘要", ""]
    abstract = (item.get("abstract") or "").strip()
    lines.append(abstract if abstract else "（暂无摘要）")

    summary = item.get("summary") or {}
    summary_text = (summary.get("summary") or "").strip()
    if summary_text:
        lines += ["", "## AI 总结", "", summary_text]
        why_it_matters = (summary.get("why_it_matters") or "").strip()
        if why_it_matters:
            lines += ["", f"**为什么重要**：{why_it_matters}"]
        summary_json = summary.get("summary_json") or {}
        next_actions = summary_json.get("next_actions") if isinstance(summary_json, dict) else []
        if isinstance(next_actions, str):
            next_actions = [next_actions]
        if isinstance(next_actions, list) and next_actions:
            lines += ["", "**下一步**:"]
            for action in next_actions[:5]:
                action_text = re.sub(r"[\r\n]+", " ", str(action)).strip()
                if action_text:
                    lines.append(f"- {action_text}")

    if topics:
        lines += ["", "## 主题", ""]
        for topic in topics:
            label = topic.get("label") or topic.get("slug") or ""
            if wiki_links:
                lines.append(f"- [[{label}]]")
            else:
                lines.append(f"- {label}")

    lines += [
        "",
        "---",
        "*由 scientific-agent 同步；知识库不保存 PDF，仅保留下载链接。*",
        "",
    ]
    return "\n".join(lines)


def render_topic_note(
    topic: dict[str, Any],
    papers: list[dict[str, Any]],
) -> str:
    label = topic.get("label") or topic.get("slug") or "topic"
    lines = [
        "---",
        yaml.safe_dump(
            {"tags": ["literature-topic"], "papers": len(papers)},
            allow_unicode=True,
            sort_keys=False,
        ).strip(),
        "---",
        "",
        f"# 主题：{label}",
        "",
    ]
    for item in papers:
        score = item.get("score")
        suffix = f"（相关度 {score:.2f}）" if isinstance(score, (int, float)) else ""
        lines.append(f"- [[{item['note_name']}|{item.get('title') or 'Untitled'}]]{suffix}")
    lines.append("")
    return "\n".join(lines)


def render_index_note(
    items: list[dict[str, Any]],
    topics: list[dict[str, Any]],
) -> str:
    lines = [
        "# 文献知识库",
        "",
        f"- 论文总数：{len(items)}",
        f"- 主题总数：{len(topics)}",
        "- 说明：本目录由 `scientific-agent kb sync` 生成，只保留 PDF 下载链接，不保存 PDF 文件。",
        "",
        "## 主题索引",
        "",
    ]
    for topic in topics:
        label = topic.get("label") or topic.get("slug") or "topic"
        lines.append(f"- [[{label}]]（{len(topic.get('papers') or [])} 篇）")
    lines += ["", "## 最近收录", ""]
    for item in items[:20]:
        lines.append(f"- [[{item['note_name']}|{item.get('title') or 'Untitled'}]]")
    lines.append("")
    return "\n".join(lines)


def _write_if_changed(path: Path, content: str) -> str:
    if path.exists():
        try:
            if path.read_text("utf-8") == content:
                return "unchanged"
        except OSError:
            pass
        path.write_text(content, "utf-8")
        return "updated"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")
    return "created"


def sync_obsidian(
    config: AppConfig,
    *,
    vault: str | None = None,
    subdir: str | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    settings = knowledge_base_settings(config)["obsidian"]
    if vault:
        vault_path = Path(vault).expanduser()
    elif settings["vault"]:
        vault_path = Path(settings["vault"]).expanduser()
    else:
        vault_path = detect_obsidian_vault()
    if vault_path is None or not vault_path.is_dir():
        raise ValueError(
            "未找到 Obsidian 仓库；请在配置的 knowledge_base.obsidian.vault 中填写路径，"
            "或先用 Obsidian 打开一次目标仓库。"
        )

    base_dir = vault_path / (subdir or settings["subdir"])
    items = _collect_library(config, limit=limit)
    _assign_note_names(items)

    topics: dict[str, dict[str, Any]] = {}
    for item in items:
        for topic in item.get("topics") or []:
            slug = topic.get("slug") or ""
            if not slug:
                continue
            entry = topics.setdefault(
                slug,
                {"slug": slug, "label": topic.get("label") or slug, "papers": []},
            )
            entry["papers"].append(item)
    ordered_topics = sorted(
        topics.values(),
        key=lambda entry: (-len(entry["papers"]), str(entry["label"]).lower()),
    )

    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for item in items:
        content = render_paper_note(item, wiki_links=True)
        path = base_dir / PAPERS_DIRNAME / f"{item['note_name']}.md"
        counts[_write_if_changed(path, content)] += 1
    for topic in ordered_topics:
        content = render_topic_note(topic, topic["papers"])
        path = base_dir / TOPICS_DIRNAME / f"{_sanitize_note_name(str(topic['label']), topic['slug'])}.md"
        counts[_write_if_changed(path, content)] += 1
    index_path = base_dir / f"{INDEX_NOTE_NAME}.md"
    counts[_write_if_changed(index_path, render_index_note(items, ordered_topics))] += 1

    return {
        "status": "ok",
        "vault": str(vault_path),
        "base_dir": str(base_dir),
        "papers": len(items),
        "topics": len(ordered_topics),
        "notes": counts,
        "pdf_files_written": 0,
    }


def _lark_state_path(config: AppConfig) -> Path:
    return config.root_dir / LARK_STATE_DIRNAME / LARK_STATE_FILENAME


def _load_lark_state(config: AppConfig) -> dict[str, Any]:
    try:
        data = json.loads(_lark_state_path(config).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_lark_state(config: AppConfig, state: dict[str, Any]) -> None:
    path = _lark_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


def _extract_file_token(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("file_token", "token"):
            value = payload.get(key)
            if isinstance(value, str) and len(value) >= 6:
                return value
        for value in payload.values():
            found = _extract_file_token(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _extract_file_token(value)
            if found:
                return found
    return ""


def sync_lark(
    config: AppConfig,
    *,
    limit: int = 0,
) -> dict[str, Any]:
    settings = knowledge_base_settings(config)["lark"]
    cli = shutil.which(settings["cli"])
    if cli is None and Path(settings["cli"]).is_file():
        cli = settings["cli"]
    if cli is None:
        return {
            "status": "skipped",
            "reason": f"未找到 lark-cli 可执行文件: {settings['cli']}",
            "hint": LARK_INSTALL_HINT,
        }

    items = _collect_library(config, limit=limit)
    _assign_note_names(items)
    state = _load_lark_state(config)
    created = updated = failed = 0
    errors: list[dict[str, str]] = []

    for item in items:
        paper_id = item["paper_id"]
        note_name = f"{item['note_name']}.md"
        markdown = render_paper_note(item, wiki_links=False)
        existing = state.get(paper_id) or {}
        file_token = str(existing.get("file_token") or "")

        if file_token:
            argv = [cli, "markdown", "+overwrite", "--file-token", file_token, "--content", "-"]
        else:
            argv = [cli, "markdown", "+create", "--name", note_name, "--content", "-"]
            if settings["folder_token"]:
                argv += ["--folder-token", settings["folder_token"]]
        if settings["as"]:
            argv += ["--as", settings["as"]]

        try:
            proc = subprocess.run(
                argv,
                input=markdown,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed += 1
            errors.append({"paper_id": paper_id, "error": str(exc)})
            continue

        if proc.returncode != 0:
            failed += 1
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            errors.append({"paper_id": paper_id, "error": detail or f"exit {proc.returncode}"})
            lowered = detail.lower()
            if "not_configured" in lowered or "unauthorized" in lowered or "auth" in lowered:
                return {
                    "status": "failed",
                    "reason": "lark-cli 未配置或未登录，已中止同步。",
                    "hint": LARK_INSTALL_HINT,
                    "created": created,
                    "updated": updated,
                    "failed": failed,
                    "errors": errors,
                }
            continue

        if file_token:
            updated += 1
            existing["synced_at"] = _utc_now()
            state[paper_id] = existing
        else:
            try:
                new_token = _extract_file_token(json.loads(proc.stdout))
            except json.JSONDecodeError:
                new_token = ""
            created += 1
            state[paper_id] = {
                "file_token": new_token,
                "name": note_name,
                "synced_at": _utc_now(),
            }

    _save_lark_state(config, state)
    return {
        "status": "ok" if failed == 0 else "partial",
        "papers": len(items),
        "created": created,
        "updated": updated,
        "failed": failed,
        "errors": errors[:10],
        "pdf_files_uploaded": 0,
    }


def knowledge_base_status(config: AppConfig) -> dict[str, Any]:
    settings = knowledge_base_settings(config)
    obsidian = settings["obsidian"]
    configured_vault = obsidian["vault"]
    detected_vault = detect_obsidian_vault()
    resolved_vault = (
        Path(configured_vault).expanduser()
        if configured_vault
        else detected_vault
    )
    lark_cli = shutil.which(settings["lark"]["cli"])
    return {
        "obsidian": {
            "enabled": obsidian["enabled"],
            "configured_vault": configured_vault,
            "detected_vault": str(detected_vault) if detected_vault else "",
            "resolved_vault": str(resolved_vault) if resolved_vault else "",
            "subdir": obsidian["subdir"],
        },
        "lark": {
            "enabled": settings["lark"]["enabled"],
            "cli": settings["lark"]["cli"],
            "cli_found": lark_cli or "",
            "synced_files": len(_load_lark_state(config)),
        },
    }
