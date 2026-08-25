from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from literature_agent.config import load_config
from literature_agent.research_workflow import run_research_workflow
from literature_agent.writing_workspace import (
    compile_project,
    create_project,
    import_local_workspace,
    load_project_sources,
    load_workspace_index,
    sync_workflow_project,
)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("goal")
    parser.add_argument("--query", default="")
    parser.add_argument("--writing-type", default="academic", choices=["academic", "grant"])
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--rag-limit", type=int, default=8)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--strict-no-fallback", action="store_true")
    parser.add_argument("--exclude-preprints", action="store_true")
    parser.add_argument("--template-id", default="")
    parser.add_argument("--workspace-path", default="")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--author", default="Scientific Agent")
    parser.add_argument("--requirements", default="")
    parser.add_argument("--sync-project", action="store_true")
    args = parser.parse_args()

    config = load_config()
    payload = {
        "goal": args.goal,
        "query": args.query,
        "writing_type": args.writing_type,
        "template_id": args.template_id,
        "requirements": args.requirements,
        "use_literature_pipeline": args.crawl,
        "rag_limit": args.rag_limit,
        "api_key": args.api_key,
        "strict_no_fallback": args.strict_no_fallback,
        "exclude_preprints": args.exclude_preprints,
    }
    project = {}
    project_id = str(args.project_id or "").strip()
    if args.sync_project:
        project = create_project(
            {
                "project_id": project_id,
                "template_id": args.template_id,
                "title": args.goal[:120],
                "author": args.author,
                "goal": args.goal,
                "requirements": args.requirements,
                "writing_type": args.writing_type,
            }
        )
        project_id = str(project.get("project_id") or "")
        if args.workspace_path:
            import_local_workspace(project_id, args.workspace_path)
        payload["run_id"] = project_id
        payload["template_id"] = args.template_id or str(project.get("template_id") or "")
        payload["workspace_index"] = load_workspace_index(project_id)
        payload["source_materials"] = load_project_sources(project_id, include_text=True)
    result = run_research_workflow(config, payload)
    if args.sync_project and project_id:
        sync_workflow_project(
            project_id,
            result,
            title=args.goal[:120],
            goal=args.goal,
            requirements=args.requirements,
            author=args.author,
            query=args.query,
        )
        result["project_id"] = project_id
        result["project_compile"] = compile_project(project_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
