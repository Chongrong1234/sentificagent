from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
EXAMPLE_CONFIG_PATH = CONFIG_DIR / "library_rules.example.yaml"
USER_CONFIG_PATH = CONFIG_DIR / "library_rules.yaml"
# Kept as a compatibility alias for callers that imported the old constant.
DEFAULT_CONFIG_PATH = EXAMPLE_CONFIG_PATH


def default_config_path() -> Path:
    """Return the writable user config when initialized, otherwise the example."""
    return USER_CONFIG_PATH if USER_CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH


def initialize_config(
    config_path: str | os.PathLike[str] | None = None,
    *,
    force: bool = False,
) -> Path:
    """Create a user config from the checked-in example template.

    The example file is intentionally never a valid destination. This prevents an
    accidental ``--force`` from overwriting the repository's reference config.
    """
    target = Path(config_path).expanduser() if config_path else USER_CONFIG_PATH
    if not target.is_absolute():
        target = (PROJECT_ROOT / target).resolve()
    if target.resolve() == EXAMPLE_CONFIG_PATH.resolve():
        raise ValueError("The example config is read-only; choose a user config path.")
    if target.exists() and not force:
        raise FileExistsError(f"Config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_CONFIG_PATH, target)
    return target


@dataclass(frozen=True)
class AppConfig:
    path: Path
    raw: dict[str, Any]
    root_dir: Path
    inbox_dir: Path
    records_dir: Path
    search_runs_dir: Path
    queue_dir: Path
    attention_runs_dir: Path
    summaries_dir: Path
    schedules_dir: Path
    reports_dir: Path
    attention_state_dir: Path
    library_db_path: Path
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
    requested_path = config_path or os.environ.get("LIT_AGENT_CONFIG")
    resolved_path = Path(requested_path).expanduser() if requested_path else default_config_path()
    if not resolved_path.is_absolute():
        resolved_path = (PROJECT_ROOT / resolved_path).resolve()

    with resolved_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    base_dir = resolved_path.parent
    attention = raw.get("attention", {}) or {}
    feeds_config = attention.get("feeds_config", "")
    if feeds_config:
        feeds_path = _resolve_path(base_dir, str(feeds_config), base_dir / str(feeds_config))
        if feeds_path.exists():
            with feeds_path.open("r", encoding="utf-8") as handle:
                feed_rules = yaml.safe_load(handle) or {}
            raw["attention"] = {**feed_rules, **attention}
            raw["attention"].pop("feeds_config", None)

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
    attention_runs_dir = root_dir / storage.get("attention_runs_dir", "attention_runs")
    summaries_dir = root_dir / storage.get("summaries_dir", "summaries")
    schedules_dir = root_dir / storage.get("schedules_dir", "schedules")
    reports_dir = root_dir / storage.get("reports_dir", "reports")
    attention_state_dir = root_dir / storage.get("attention_state_dir", "attention_state")
    library_db_path = root_dir / storage.get("library_db", "library.sqlite3")

    return AppConfig(
        path=resolved_path,
        raw=raw,
        root_dir=root_dir,
        inbox_dir=inbox_dir,
        records_dir=records_dir,
        search_runs_dir=search_runs_dir,
        queue_dir=queue_dir,
        attention_runs_dir=attention_runs_dir,
        summaries_dir=summaries_dir,
        schedules_dir=schedules_dir,
        reports_dir=reports_dir,
        attention_state_dir=attention_state_dir,
        library_db_path=library_db_path,
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
