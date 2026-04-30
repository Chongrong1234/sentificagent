from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import AppConfig


KIMI_API_BASE = "https://api.moonshot.cn/v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KIMI_KEY_FILE = PROJECT_ROOT / ".secrets" / "kimi_api_key.txt"


@dataclass(frozen=True)
class ChatResult:
    content: str
    patch: dict[str, Any]
    plan: dict[str, Any]
    raw: dict[str, Any]


def _load_project_api_key() -> str:
    if not DEFAULT_KIMI_KEY_FILE.exists():
        return ""
    return DEFAULT_KIMI_KEY_FILE.read_text(encoding="utf-8").strip()


def _build_messages(config: AppConfig, user_message: str) -> list[dict[str, str]]:
    config_excerpt = {
        "research_profile": config.raw.get("research_profile", {}),
        "classifier": {
            "venue_tiers": config.raw.get("classifier", {}).get("venue_tiers", []),
            "keyword_buckets": config.raw.get("classifier", {}).get("keyword_buckets", []),
        },
    }

    system_prompt = config.chat_system_prompt.strip() or (
        "你是科研智能体，输出简洁结论，并在需要更新配置时给出 JSON patch。"
    )
    patch_instruction = """
你必须用以下格式回复：
第一部分是自然语言建议。
第二部分如果需要更新配置，追加一行：
CONFIG_PATCH_JSON: <json>
第三部分如果建议立即检索文献，追加一行：
SEARCH_PLAN_JSON: <json>

JSON 约束：
- 只能输出一个 JSON object
- 顶层字段可包含 research_profile, classifier
- 只放需要新增或修改的字段
- 不要输出 markdown 代码块
如果不需要修改配置，则不要输出 CONFIG_PATCH_JSON。
如果不建议检索，则不要输出 SEARCH_PLAN_JSON。

SEARCH_PLAN_JSON 约束：
- 只能输出一个 JSON object
- 可包含 query, max_results, min_score, auto_download, rationale
- query 必须是适合文献检索的关键词串
""".strip()

    return [
        {
            "role": "system",
            "content": f"{system_prompt}\n\n当前配置摘要:\n{json.dumps(config_excerpt, ensure_ascii=False)}\n\n{patch_instruction}",
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]


def _parse_json_line(content: str, marker: str) -> tuple[str, dict[str, Any]]:
    if marker not in content:
        return content, {}

    natural, json_part = content.split(marker, 1)
    json_text = json_part.strip().splitlines()[0].strip()
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        payload = {}
    return natural.strip(), payload


def _parse_response(content: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    remaining, search_plan = _parse_json_line(content, "SEARCH_PLAN_JSON:")
    remaining, patch = _parse_json_line(remaining, "CONFIG_PATCH_JSON:")
    return remaining.strip(), patch, search_plan


def chat_with_kimi(
    config: AppConfig,
    user_message: str,
    api_key: str | None = None,
    model: str | None = None,
) -> ChatResult:
    secret = api_key or os.environ.get("KIMI_API_KEY", "") or _load_project_api_key()
    if not secret:
        raise ValueError("Missing Kimi API key.")

    payload = {
        "model": model or config.planner_model,
        "messages": _build_messages(config, user_message),
    }

    req = request.Request(
        f"{KIMI_API_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Kimi API error: {exc.code} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Kimi network error: {exc}") from exc

    content = (
        body.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    natural, patch, plan = _parse_response(content)
    return ChatResult(content=natural, patch=patch, plan=plan, raw=body)
