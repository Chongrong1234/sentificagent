from __future__ import annotations

import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from literature_agent.attention_pipeline import run_attention_pipeline
from literature_agent.config import load_config


def _interval_seconds(config: object) -> int:
    raw = getattr(config, "raw", {}).get("attention", {})
    return int(raw.get("interval_seconds", 3600))


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    while True:
        config = load_config(config_path)
        result = run_attention_pipeline(config, {})
        preview = {
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "discovered_count": result["discovered_count"],
            "fresh_count": result["fresh_count"],
            "ranked_count": result["ranked_count"],
            "summarized_count": result["summarized_count"],
            "survey_report_id": (result.get("survey_report") or {}).get("report_id", ""),
            "artifacts": result["artifacts"],
        }
        print(json.dumps(preview, ensure_ascii=False), flush=True)
        time.sleep(_interval_seconds(config))


if __name__ == "__main__":
    main()
