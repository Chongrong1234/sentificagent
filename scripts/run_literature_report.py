from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from literature_agent.config import load_config
from literature_agent.survey_reporting import generate_library_survey_report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--config-path", default="")
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--language", default="zh", choices=["zh", "en"])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--title", default="")
    parser.add_argument("--report-kind", default="survey")
    args = parser.parse_args()

    config = load_config(args.config_path or None)
    result = generate_library_survey_report(
        config,
        {
            "query": args.query,
            "limit": args.limit,
            "language": args.language,
            "days": args.days,
            "title": args.title,
            "report_kind": args.report_kind,
        },
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
