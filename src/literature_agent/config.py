from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "library_rules.example.yaml"


@dataclass(frozen=True)
class AppConfig:
    path: Path
    raw: dict[str, Any]
    root_dir: Path
    inbox_dir: Path
    records_dir: Path
    search_runs_dir: Path
    queue_dir: Path
    browser_download_root: str
    path_template: str
    default_venue_tier: str
    default_team: str
    default_keyword_bucket: str
    planner_model: str
    runner_model: str
    chat_system_prompt: str


def _resolve_path(base_dir: Path, raw_path: str, fallback: Path) -> Path:
    if not raw_path:
        return fallback
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def load_config(config_path: str | os.PathLike[str] | None = None) -> AppConfig:
    resolved_path = Path(
        config_path or os.environ.get("LIT_AGENT_CONFIG") or DEFAULT_CONFIG_PATH
    ).expanduser()
    if not resolved_path.is_absolute():
        resolved_path = (PROJECT_ROOT / resolved_path).resolve()

    with resolved_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    base_dir = resolved_path.parent
    storage = raw.get("storage", {})
    browser = raw.get("browser", {})
    classifier = raw.get("classifier", {})
    models = raw.get("models", {})
    chat = raw.get("chat", {})

    root_dir = _resolve_path(
        base_dir,
        storage.get("root_dir", "../data/library"),
        PROJECT_ROOT / "data" / "library",
    )
    inbox_dir = root_dir / storage.get("inbox_dir", "inbox")
    records_dir = root_dir / storage.get("records_dir", "records")
    search_runs_dir = root_dir / storage.get("search_runs_dir", "search_runs")
    queue_dir = root_dir / storage.get("queue_dir", "queue")

    return AppConfig(
        path=resolved_path,
        raw=raw,
        root_dir=root_dir,
        inbox_dir=inbox_dir,
        records_dir=records_dir,
        search_runs_dir=search_runs_dir,
        queue_dir=queue_dir,
        browser_download_root=browser.get("download_root", "scientific-agent"),
        path_template=classifier.get(
            "path_template",
            "{venue_tier}/{primary_team}/{keyword_bucket}/{year}",
        ),
        default_venue_tier=classifier.get("default_venue_tier", "unranked"),
        default_team=classifier.get("default_team", "unassigned"),
        default_keyword_bucket=classifier.get("default_keyword_bucket", "general"),
        planner_model=models.get("planner", {}).get("model", "kimi-k2.5"),
        runner_model=models.get("runner", {}).get("model", "kimi-k2.5"),
        chat_system_prompt=chat.get("system_prompt", ""),
    )
