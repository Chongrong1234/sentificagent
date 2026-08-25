# Repository Guidelines

## Project Structure & Module Organization

- `src/literature_agent/` contains the Python capture service, search/attention pipelines, library storage, citation and writing workflows. `knowledge_sync.py` syncs the library into Obsidian vaults and Lark Drive (via `lark-cli`) as Markdown notes with PDF download links only.
- `scripts/` provides runnable entry points, including `run_capture_server.py`, `run_attention_pipeline.py`, `run_research_workflow.py`, and `search_library.py`.
- `apps/browser-extension/` is the Chrome/Edge capture and download extension; `apps/web/` is the local HTML/CSS/JavaScript console.
- `configs/` holds user-owned YAML rules, feeds, and writing guardrails. Keep secrets in `.secrets/` (ignored by Git).
- `tests/` contains Python `unittest` coverage. Runtime artifacts are written under ignored `data/`; design notes live in `docs/`.
- `pyproject.toml`/`setup.py` define install metadata and the `scientific-agent` CLI; `Makefile` provides short local aliases.

## Build, Test, and Development Commands

Install the project with `python3 -m pip install -e .`; this exposes the unified `scientific-agent` command. Run `scientific-agent init` once, then `scientific-agent check` and `scientific-agent serve` to start the local UI at `http://127.0.0.1:8765`. From a checkout, the equivalent `python3 -m src.literature_agent ...` and `make check`/`make test` aliases avoid installation. Use `scientific-agent attention`, `scientific-agent search`, or `scientific-agent library` for non-interactive workflows. The existing `scripts/*.py` entry points remain supported for automation.

Run the complete Python test suite with:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The optional Node summarizer is in `tools/article-summarizer/`; run `npm install` there, then `npm run summarize`.

## Coding Style & Naming Conventions

Use Python 3.11-compatible code, four-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and type hints where practical. Follow the existing small, focused modules and standard-library style; no project formatter or linter is configured. JavaScript uses the existing browser code style and `camelCase` names. Use descriptive lowercase filenames and preserve YAML schema/indentation.

## Testing Guidelines

Add regression tests in `tests/test_<area>.py` using `unittest.TestCase` and `test_<behavior>` methods. Prefer temporary directories and isolated configuration over writing to `data/`. Run targeted tests during development, then the full discovery command before submitting. Network/API-dependent end-to-end tests may require local services or credentials; document skips and assumptions.

## Commit & Pull Request Guidelines

The history currently contains only `first commit`, so no established convention can be inferred. Use short, imperative subjects (for example, `Add citation guardrail tests`) and keep each commit focused. Pull requests should explain user-visible behavior and configuration changes, link related issues when applicable, list validation commands and results, and include screenshots for web or extension UI changes. Never commit API keys, `.secrets/`, generated `data/`, PDFs, or other local artifacts.
