# Scientific Agent

This repository starts with a browser-extension-first MVP for literature capture and a
local Kimi-powered chat console for evolving research preferences.

Current scope:

- capture paper metadata from the current browser tab
- classify papers with a user-owned YAML config
- suggest a config-driven download path for PDFs
- persist capture records locally for later LangGraph workflows
- chat locally with Kimi and update research configuration through suggested patches
- batch search literature from open sources, score matches, tag results, and auto-download PDFs when available

## Intended workflow

The primary workflow is now:

1. chat with the agent about research interests, venue priorities, and download strategy
2. let the agent suggest a config patch and a search plan
3. apply the patch and run the search plan in one action
4. review ranked results and queued downloads
5. use the browser extension to execute queued downloads with the current Chrome session
6. accumulate metadata and PDFs into the local knowledge base

The first prototype deliberately separates:

- browser extension: extract metadata and trigger browser-side PDF download
- local capture service: classify, persist, and prepare downstream workflows
- future LangGraph runtime: planner/runner orchestration, graph building, and library QA

## Layout

```text
apps/browser-extension/    Chrome/Edge extension
apps/web/                  local chat UI
configs/                   user-owned classification rules
docs/                      architecture notes
scripts/                   local entrypoints
src/literature_agent/      capture service and core logic
```

## Quick start (recommended)

Python 3.10+ is supported. Install the project in editable mode so the
`scientific-agent` command is available from any directory:

```bash
python3 -m pip install -e .
scientific-agent init       # creates configs/library_rules.yaml
scientific-agent check      # validates configuration and local tools
scientific-agent serve      # starts http://127.0.0.1:8765
```

The checked-in `configs/library_rules.example.yaml` is a read-only template.
The generated `configs/library_rules.yaml` is ignored by Git and is where the
web UI writes user preferences. Every command accepts `--config PATH` when a
separate profile is needed. Use `scientific-agent --help` or
`scientific-agent <command> --help` for all options.

Common non-interactive commands are:

```bash
scientific-agent search "multimodal remote sensing" --max-results 20
scientific-agent attention --query "smart agriculture"
scientific-agent library "smart agriculture" --limit 10
scientific-agent report "crop monitoring" --language zh
scientific-agent research "撰写移动巡检路面病害检测开题报告" --crawl
```

The existing `scripts/*.py` entry points remain supported for automation and
backward compatibility.

## Run the local app

```bash
python3 scripts/run_capture_server.py
```

The service listens on `http://127.0.0.1:8765`.
Open `http://127.0.0.1:8765` for the local chat UI.

The web UI is designed as an AGI lab console:

- layer 1: browser literature capture and authenticated PDF downloads
- layer 2: automated literature search, ranking, tagging, and queue creation
- layer 3: local metadata/PDF library for future RAG and graph workflows
- layer 4: Kimi-powered research agent that proposes config patches and search plans

Recommended first use:

1. Ask the agent to refine your interests.
2. Click `应用配置并执行计划`.
3. Review the generated queue.
4. Run the browser extension download queue in Chrome.

## Kimi API key

The app also supports a project-local key file:

```text
.secrets/kimi_api_key.txt
```

The current load order is:

1. API key typed into the local web UI
2. `KIMI_API_KEY` environment variable
3. project file `.secrets/kimi_api_key.txt`

Set it as an environment variable:

```bash
export KIMI_API_KEY="your-real-key"
python3 scripts/run_capture_server.py
```

Or paste it into the local web UI for the current session only.

## Test the browser extension

Current status:

- this is a `capture` plugin, not a broad crawler-style `search` plugin yet
- the correct test is to open a real paper page, then let the extension extract and save it

Recommended test pages:

1. `https://arxiv.org/abs/1706.03762`
2. `https://openreview.net/forum?id=VtmBAGCN7o`
3. any publisher page that exposes `citation_*` meta tags and a visible PDF link

Test steps:

1. Start the local app with `python3 scripts/run_capture_server.py`.
2. Open `http://127.0.0.1:8765/health` and confirm the service is up.
3. Load the unpacked extension from `apps/browser-extension/`.
4. Visit one of the paper pages above.
5. Click the extension and use `Capture only` first.
6. Confirm a new JSON record appears under `data/library/inbox/`.
7. Run `Capture + download`.
8. Confirm the browser starts downloading into `Downloads/scientific-agent/...`.

What success looks like:

- the popup shows a classification result
- a metadata record is created under `data/library/inbox/`
- a canonical metadata file is created under `data/library/records/`
- if a PDF URL was detected, the browser download starts

## Batch search and download

The local app now supports automated search without opening paper pages manually.

Current first-stage behavior:

- searches open sources first: `OpenAlex` and `arXiv`
- scores each paper against your query and research profile
- checks match signals across `title`, `abstract`, `keywords`, `authors`, and `venue`
- assigns tags such as topic, venue tier, and keyword bucket
- can auto-download PDFs when a direct PDF URL is available
- can also create a browser-side download queue for institution-authenticated sources

How to test:

1. Start the local app with `python3 scripts/run_capture_server.py`.
2. Open `http://127.0.0.1:8765`.
3. First talk to the agent, for example:
   - `提高 llm、ccf-a 顶会、多模态遥感方向优先级，并生成适合近期检索的会议论文搜索词`
4. Click `应用配置并执行计划`.
5. Confirm the result panel shows ranked papers, scores, matched fields, tags, and queue path.
6. If queued institution-authenticated downloads are needed, open the Chrome extension and click `Run download queue`.
7. Confirm metadata appears under `data/library/records/` and queued downloads are triggered in the browser.

Artifacts written by the batch pipeline:

- search run logs: `data/library/search_runs/`
- browser download queues: `data/library/queue/`
- per-paper metadata: `data/library/records/.../metadata.json`
- downloaded PDFs: `data/library/records/.../paper.pdf`

## Attention automation workflow

The local app also includes an `elfeed`/`article-summarizer` style attention
pipeline for precise literature intake:

1. discover papers from RSS/Atom feeds, manual URLs, or OpenAlex/arXiv search
2. rank papers with your configured topics, venues, teams, keywords, and field weights
3. fetch readable article text in the background
4. summarize high-priority papers asynchronously with the configured Kimi/OpenAI-compatible API
5. fall back to extractive summaries when no API key is available
6. export JSON summaries and an Org-mode schedule file for follow-up reading

The original reference stack uses `elfeed` + `elfeed-score` for discovery and
priority ranking, then `article-summarizer` uses Playwright Firefox + Mozilla
Readability + an OpenAI-compatible chat API for webpage extraction and summary.
This project implements the same workflow inside the Python local service. The
current default fetcher is a standard-library HTML reader, while the browser
extension remains responsible for authenticated pages and PDF downloads.

How to run:

1. Start the service with `python3 scripts/run_capture_server.py`.
2. Open `http://127.0.0.1:8765`.
3. In `自动化精准抓取`, provide at least one of:
   - a literature query
   - RSS/Atom feed URLs, one per line
   - manual article or paper URLs, one per line
4. Set the summary threshold and summary count.
5. Click `启动全流程`.
6. Refresh the task panel until the job is completed.

Artifacts written by the attention pipeline:

- full run logs: `data/library/attention_runs/`
- high-priority summaries: `data/library/summaries/`
- Org-mode follow-up schedule: `data/library/schedules/`
- normalized searchable library: `data/library/library.sqlite3`

File-driven mode:

1. Edit subscriptions and attention thresholds in `configs/attention_feeds.yaml`.
2. Keep `configs/library_rules.example.yaml` pointing to it via `attention.feeds_config`.
3. Run the full pipeline without the web UI:

```bash
python3 scripts/run_attention_pipeline.py
```

For continuous automation:

```bash
python3 scripts/run_attention_daemon.py
```

The daemon reloads the config every cycle, so editing `configs/attention_feeds.yaml`
takes effect on the next run. It writes `data/library/attention_state/seen.json`
and skips already-seen feed entries by default. Set `force_refresh: true` in the
feed config if you intentionally want to reprocess everything.

This mode follows the reference `elfeed` workflow more closely: the feed file is the
source of truth, the configured score threshold decides which items are summarized,
and the generated `.org` file is the reading schedule.

## Searchable local summary DB

The literature intake layer now mirrors the referenced `elfeed` +
`article-summarizer` + `elfeed-summary-db` workflow inside the project:

1. feeds, manual URLs, and optional OpenAlex/arXiv search discover entries
2. configured relevance rules score entries like `elfeed-score`
3. selected entries are fetched through `tools/article-summarizer` with
   Playwright Firefox and Mozilla Readability when available
4. AI summaries are attached to the same paper record; if the LLM call fails, the run fails directly
5. follow-up reading tasks are written to both Org-mode and SQLite
6. titles, abstracts, authors, article text, and summaries are searchable

Inspect the local DB:

```bash
python3 scripts/search_library.py "smart agriculture" 10
```

The service also exposes:

```text
GET /api/library/search?q=smart%20agriculture&limit=10
```

Core tables in `data/library/library.sqlite3`:

- `workflow_runs`: every search or attention run
- `papers`: normalized paper/feed entries
- `paper_scores`: relevance score, priority, matched fields, and tags
- `article_texts`: fetched Readability/HTML text and fetch status
- `summaries`: AI-generated summaries
- `reading_tasks`: scheduled follow-up tasks exported to Org-mode

## Planner/runner RAG writing workflow

The project includes an end-to-end research writing workflow:

1. Kimi planner turns a writing goal into a structured workflow plan.
2. Optional literature intake crawls/searches fresh papers before writing.
3. Local RAG retrieves evidence from `data/library/library.sqlite3`.
4. Kimi runner writes a self-contained LaTeX manuscript or grant draft.
5. The local compiler writes `manuscript.pdf` when `xelatex`, `pdflatex`, or
   `tectonic` is installed.

The workflow uses LangGraph when the `langgraph` package is installed. If it is
not installed, the same nodes run sequentially instead of via LangGraph.

Runtime dependencies:

- `langgraph` for explicit graph orchestration
- `xelatex` for Chinese LaTeX output, or `pdflatex`/`tectonic` for English-only
  drafts
- `KIMI_API_KEY` for planner and runner model calls

Command-line example:

```bash
export KIMI_API_KEY="your-real-key"
python3 scripts/run_research_workflow.py \
  "基于本地遥感 VLM 文献库，撰写一份智慧农业多模态遥感综述草稿" \
  --query "remote sensing vision language smart agriculture" \
  --writing-type academic
```

Grant proposal mode:

```bash
python3 scripts/run_research_workflow.py \
  "撰写一个面向智慧农业多模态遥感基础模型的青年基金申请草稿" \
  --query "remote sensing foundation model smart agriculture" \
  --writing-type grant
```

Artifacts are written under:

```text
data/library/writing_runs/<run-id>/
```

Expected files:

- `plan.json`: planner workflow
- `evidence.json`: local RAG evidence
- `manuscript.tex`: runner LaTeX output
- `manuscript.pdf`: compiled PDF when a TeX engine is available

The web UI exposes the same workflow under `RAG 写作与 LaTeX`.

The dedicated writing workspace under `/writing` adds a template library layer:

- choose an existing local LaTeX template before writing
- ask the agent to resolve a missing template by name
- if the template is not already in the local library, the service can resolve
  a CTAN package or GitHub repository and download it into
  `data/library/template_library/`
- downloaded template directories are added to `TEXINPUTS` automatically during
  local LaTeX compilation so custom `.cls`/`.sty` files can be found

## Native elfeed workflow

For the workflow that directly follows the reference article, use:

```text
tools/elfeed/
tools/article-summarizer/
```

This path uses real `elfeed`, `elfeed-score`, Playwright Firefox, Mozilla
Readability, OpenAI-compatible summaries, elfeed metadata write-back, and Org
schedule export. See `tools/elfeed/README.md`.

Minimum setup:

```bash
cd tools/article-summarizer
npm install
npx playwright install firefox
```

Then load `tools/elfeed/init-example.el` from Emacs and run:

```elisp
M-x scientific-agent-configure-feeds
M-x elfeed-update
```

## Load the browser extension

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable developer mode.
3. Load unpacked extension from `apps/browser-extension/`.
4. Open a paper page, click the extension, and run `Capture + download`.

## Current design choice

The extension downloads PDFs through the browser session instead of the local server.
This is intentional for the first stage: institutional access and login cookies usually
live in the browser, not in a Docker container.

The local service still computes the storage path, records metadata, and prepares the
library structure needed by later import, graph construction, and LLM workflows.

## Docker

```bash
docker build -t scientific-agent .
docker run --rm -p 8765:8765 \
  -v "$PWD/configs:/app/configs" \
  -v "$PWD/data:/app/data" \
  -e KIMI_API_KEY=your-real-key \
  scientific-agent
```

The image includes a `/health` healthcheck. Mount `configs/` and `data/` when
you want user preferences and downloaded library artifacts to survive container
restarts. Secrets are intentionally excluded from the build context.

## Development and testing

Run `make test` (or `python3 -m unittest discover -s tests -p 'test_*.py'`) before submitting changes. If pytest is installed, `pytest -q` is also supported. The manual workflow
smoke test is intentionally excluded from automatic discovery because it writes a
temporary project and may need a local TeX compiler; run it explicitly with
`python3 scripts/e2e_deep_learning_check.py` when those prerequisites are available.
