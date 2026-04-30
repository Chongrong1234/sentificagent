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

## Run the local app

```bash
python3 scripts/run_capture_server.py
```

The service listens on `http://127.0.0.1:8765`.
Open `http://127.0.0.1:8765` for the local chat UI.

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
docker run --rm -p 8765:8765 -e KIMI_API_KEY=your-real-key scientific-agent
```
