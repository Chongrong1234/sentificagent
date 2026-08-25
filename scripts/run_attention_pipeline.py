from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from literature_agent.attention_pipeline import run_attention_pipeline
from literature_agent.config import load_config


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", nargs="?")
    parser.add_argument("--query", default="")
    parser.add_argument("--max-results", type=int, default=0)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--summarize-limit", type=int, default=0)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--strict-ai-only", action="store_true")
    parser.add_argument("--include-references", action="store_true")
    parser.add_argument("--disable-readability-fetch", action="store_true")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    config_path = args.config_path
    config = load_config(config_path)
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
    result = run_attention_pipeline(config, payload)
    preview = {
        "query": result["query"],
        "discovered_count": result["discovered_count"],
        "fresh_count": result["fresh_count"],
        "ranked_count": result["ranked_count"],
        "summarized_count": result["summarized_count"],
        "min_score": result["min_score"],
        "survey_report": result.get("survey_report") or {},
        "artifacts": result["artifacts"],
        "top_titles": [
            item["paper"].get("title", "")
            for item in result["summaries"][:10]
        ],
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
