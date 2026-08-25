# Architecture Notes

## Phase 1: Browser-first capture

Why start here:

- many publisher PDFs depend on browser login state or institutional access
- browser extensions can reliably detect the current page and trigger a PDF download
- capture metadata early, then iterate on organization rules without rebuilding the crawler

Flow:

1. User opens a paper landing page.
2. Browser extension extracts metadata and possible PDF URLs.
3. Extension posts the capture payload to the local service.
4. Local service classifies the paper with `configs/library_rules.yaml`.
5. Local service returns a suggested relative download path.
6. Extension downloads the PDF to `Downloads/<configured-prefix>/<classified-path>/`.

## Phase 2: Local library service

Main responsibilities:

- keep the user-owned classification config
- persist capture records under a stable local schema
- import or reconcile downloaded PDFs
- expose library search and graph APIs
- run the `elfeed`-style attention workflow: feed discovery, score filtering, article
  fetch, AI summary, and scheduled reading tasks

Recommended storage model:

- metadata is canonical
- PDFs are files attached to a paper record
- graph edges are stored separately from raw paper metadata
- normalized SQLite tables keep the searchable attention DB:
  `workflow_runs`, `papers`, `paper_scores`, `article_texts`, `summaries`, and
  `reading_tasks`

Reference workflow mapping:

- GPT2Org/browser capture -> `apps/browser-extension/` and `/api/capture`
- elfeed feed discovery -> `configs/attention_feeds.yaml` and `discover_papers`
- elfeed-score ranking -> `score_paper_relevance`
- article-summarizer -> `tools/article-summarizer/` with Playwright + Readability
- elfeed metadata summary -> `summaries` table plus JSON summary artifacts
- org-capture schedule -> `reading_tasks` table plus generated `.org` files
- elfeed-summary-db/RAG seed -> `data/library/library.sqlite3` and
  `/api/library/search`

## Phase 3: LangGraph runtime

Use LangGraph for workflow orchestration, not as the graph database itself.

Suggested roles:

- planner LLM: decides which workflow to run, decomposes research tasks, and selects tools
- runner LLM: executes bounded steps such as reading papers, extracting entities, and writing graph edges

Suggested graph nodes:

- `paper`
- `venue`
- `author`
- `team`
- `keyword`
- `method`
- `dataset`

Current writing graph:

- `planner`: Kimi planner turns a user writing goal into structured workflow JSON.
- `literature`: optionally runs the attention pipeline to crawl/summarize fresh
  literature.
- `rag`: retrieves local evidence from `data/library/library.sqlite3`.
- `runner`: Kimi runner writes evidence-grounded LaTeX.
- `compile`: local TeX engine compiles `manuscript.tex` to PDF when available.

This graph uses LangGraph when installed and falls back to the same ordered node
execution when the dependency is unavailable, so the workflow remains runnable in
minimal local environments.

Suggested edge types:

- `cites`
- `published_in`
- `written_by`
- `belongs_to_team`
- `hits_keyword`
- `uses_dataset`
- `extends_method`
- `similar_topic`

## Why not let the Docker service download all PDFs first

That works well for open-access sources and APIs, but it breaks quickly on sites that
depend on browser cookies, campus VPN, or SSO.

The pragmatic split is:

- browser extension handles authenticated page-context capture
- local crawler later handles scheduled refresh, API-based search, and open-access backfill

## Immediate next steps after this MVP

1. Add a file importer that watches the browser download directory and moves PDFs into the library root.
2. Add scheduled search connectors for OpenAlex, Crossref, arXiv, and PubMed.
3. Add a normalized SQLite or DuckDB index for search and graph queries.
4. Add LangGraph workflows for summarization, citation expansion, and relationship extraction.
