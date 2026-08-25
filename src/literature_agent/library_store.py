from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from .config import AppConfig

KIMI_API_BASE = "https://api.moonshot.cn/v1"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[一-鿿぀-ゟ゠-ヿ]", text))


def _llm_search_keywords(query: str, api_key: str, provider: str, model: str = "kimi-k2.5") -> list[str]:
    """Translate a Chinese research query into English FTS keywords via LLM."""
    api_base = DEEPSEEK_API_BASE if provider == "ds" else KIMI_API_BASE
    system_prompt = (
        "You are a scientific literature search engine. "
        "Convert the user's research topic into 5-15 English search keywords or short phrases, "
        "one per line. Include synonyms, related technical terms, and common abbreviations. "
        "Output ONLY the keywords, no numbering, no markdown, no explanation."
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Research topic: {query}"},
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }
    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        return []
    keywords: list[str] = []
    seen = set()
    for line in text.split("\n"):
        token = re.sub(r"^[\d\.\-\*\s]+", "", line.strip()).strip().lower()
        token = re.sub(r"[\(\)\"':;,]", "", token).strip()
        if token and len(token) >= 2 and token not in seen and "keyword" not in token.lower() and "english" not in token.lower():
            keywords.append(token)
            seen.add(token)
    return keywords[:15]


SCHEMA_VERSION = 2
GRAPH_BUILD_VERSION = 2

TOPIC_PREFIXES = ("topic:", "keyword:")
SHORT_TOPIC_CODES = {
    "ai",
    "cv",
    "dl",
    "gee",
    "gis",
    "gnn",
    "gpu",
    "kg",
    "llm",
    "ml",
    "nlp",
    "rag",
    "rs",
    "sar",
    "uav",
    "vlm",
}
TOPIC_DISPLAY_OVERRIDES = {
    "ai": "AI",
    "digital-twin": "Digital Twin",
    "gee": "Google Earth Engine",
    "gis": "GIS",
    "gnn": "GNN",
    "kg": "Knowledge Graph",
    "llm": "LLM",
    "machine-learning": "Machine Learning",
    "nlp": "NLP",
    "rag": "RAG",
    "remote-sensing": "Remote Sensing",
    "smart-agriculture": "Smart Agriculture",
    "vlm": "VLM",
}
GENERIC_TOPIC_SLUGS = {
    "biology",
    "computer-science",
    "data-science",
    "field-mathematics",
    "field-biology",
    "general",
    "humanity",
    "identification-biology",
    "join-topology",
    "perspective-graphical",
    "science",
    "software",
    "unknown",
    "wonder",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("display_name")
                if name:
                    result.append(str(name).strip())
        return result
    return [str(value)]


def paper_identity(paper: dict[str, Any]) -> str:
    identity = str(
        paper.get("doi")
        or paper.get("page_url")
        or paper.get("id")
        or paper.get("title")
        or ""
    ).strip().lower()
    if identity:
        return identity
    return _json(paper)


def paper_id_for(paper: dict[str, Any]) -> str:
    return hashlib.sha1(paper_identity(paper).encode("utf-8")).hexdigest()


def _stable_id(*parts: str) -> str:
    seed = "\n".join(parts)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def _connect(config: AppConfig) -> sqlite3.Connection:
    config.library_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.library_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_library_store(config: AppConfig) -> Path:
    with _connect(config) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_info (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
              run_id TEXT PRIMARY KEY,
              workflow TEXT NOT NULL,
              status TEXT NOT NULL,
              query TEXT NOT NULL DEFAULT '',
              payload_json TEXT NOT NULL DEFAULT '{}',
              started_at TEXT NOT NULL,
              completed_at TEXT,
              metrics_json TEXT NOT NULL DEFAULT '{}',
              artifacts_json TEXT NOT NULL DEFAULT '{}',
              error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS papers (
              paper_id TEXT PRIMARY KEY,
              identity TEXT NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              abstract TEXT NOT NULL DEFAULT '',
              page_url TEXT NOT NULL DEFAULT '',
              pdf_url TEXT NOT NULL DEFAULT '',
              doi TEXT NOT NULL DEFAULT '',
              year TEXT NOT NULL DEFAULT '',
              venue TEXT NOT NULL DEFAULT '',
              source_name TEXT NOT NULL DEFAULT '',
              source_domain TEXT NOT NULL DEFAULT '',
              authors_json TEXT NOT NULL DEFAULT '[]',
              keywords_json TEXT NOT NULL DEFAULT '[]',
              paper_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_seen_at TEXT,
              last_seen_run_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title);
            CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source_name, source_domain);
            CREATE INDEX IF NOT EXISTS idx_papers_seen ON papers(last_seen_at);

            CREATE TABLE IF NOT EXISTS paper_scores (
              score_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              paper_id TEXT NOT NULL,
              score REAL NOT NULL DEFAULT 0,
              priority TEXT NOT NULL DEFAULT 'low',
              selected_for_summary INTEGER NOT NULL DEFAULT 0,
              tags_json TEXT NOT NULL DEFAULT '[]',
              matched_fields_json TEXT NOT NULL DEFAULT '{}',
              relevance_json TEXT NOT NULL DEFAULT '{}',
              scored_at TEXT NOT NULL,
              UNIQUE(run_id, paper_id),
              FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS article_texts (
              article_id TEXT PRIMARY KEY,
              paper_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              url TEXT NOT NULL DEFAULT '',
              fetcher TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              excerpt TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL DEFAULT '',
              content_sha1 TEXT NOT NULL DEFAULT '',
              length INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '',
              fetched_at TEXT NOT NULL,
              UNIQUE(run_id, paper_id),
              FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS summaries (
              summary_id TEXT PRIMARY KEY,
              paper_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              summary_text TEXT NOT NULL DEFAULT '',
              why_it_matters TEXT NOT NULL DEFAULT '',
              schedule_suggestion TEXT NOT NULL DEFAULT '',
              summary_json TEXT NOT NULL DEFAULT '{}',
              model TEXT NOT NULL DEFAULT '',
              fallback INTEGER NOT NULL DEFAULT 0,
              summarized_at TEXT NOT NULL,
              UNIQUE(run_id, paper_id),
              FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reading_tasks (
              task_id TEXT PRIMARY KEY,
              paper_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '',
              priority TEXT NOT NULL DEFAULT 'normal',
              schedule_date TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'todo',
              summary TEXT NOT NULL DEFAULT '',
              next_actions_json TEXT NOT NULL DEFAULT '[]',
              org_text TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(run_id, paper_id),
              FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS topic_nodes (
              topic_id TEXT PRIMARY KEY,
              slug TEXT NOT NULL UNIQUE,
              label TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'topic',
              paper_count INTEGER NOT NULL DEFAULT 0,
              avg_score REAL NOT NULL DEFAULT 0,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_topic_nodes_slug ON topic_nodes(slug);
            CREATE INDEX IF NOT EXISTS idx_topic_nodes_count ON topic_nodes(paper_count, avg_score);

            CREATE TABLE IF NOT EXISTS paper_topics (
              topic_id TEXT NOT NULL,
              paper_id TEXT NOT NULL,
              weight REAL NOT NULL DEFAULT 1,
              source TEXT NOT NULL DEFAULT '',
              label TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              PRIMARY KEY(topic_id, paper_id),
              FOREIGN KEY(topic_id) REFERENCES topic_nodes(topic_id) ON DELETE CASCADE,
              FOREIGN KEY(paper_id) REFERENCES papers(paper_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_paper_topics_paper ON paper_topics(paper_id);

            CREATE TABLE IF NOT EXISTS topic_edges (
              edge_id TEXT PRIMARY KEY,
              source_topic_id TEXT NOT NULL,
              target_topic_id TEXT NOT NULL,
              weight REAL NOT NULL DEFAULT 0,
              shared_papers INTEGER NOT NULL DEFAULT 0,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(source_topic_id, target_topic_id),
              FOREIGN KEY(source_topic_id) REFERENCES topic_nodes(topic_id) ON DELETE CASCADE,
              FOREIGN KEY(target_topic_id) REFERENCES topic_nodes(topic_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_topic_edges_pair
            ON topic_edges(source_topic_id, target_topic_id, shared_papers);
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_info(key, value) VALUES('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS library_fts USING fts5(
                  paper_id UNINDEXED,
                  title,
                  abstract,
                  authors,
                  keywords,
                  venue,
                  article,
                  summary
                )
                """
            )
        except sqlite3.OperationalError:
            pass
    return config.library_db_path


def record_run_start(
    config: AppConfig,
    run_id: str,
    workflow: str,
    query: str,
    payload: dict[str, Any],
) -> None:
    init_library_store(config)
    safe_payload = {key: value for key, value in payload.items() if key != "api_key"}
    with _connect(config) as conn:
        conn.execute(
            """
            INSERT INTO workflow_runs(
              run_id, workflow, status, query, payload_json, started_at
            )
            VALUES(?, ?, 'running', ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              workflow=excluded.workflow,
              status='running',
              query=excluded.query,
              payload_json=excluded.payload_json
            """,
            (run_id, workflow, query, _json(safe_payload), _utc_now()),
        )


def record_run_finish(
    config: AppConfig,
    run_id: str,
    status: str,
    metrics: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    init_library_store(config)
    with _connect(config) as conn:
        conn.execute(
            """
            UPDATE workflow_runs
            SET status=?, completed_at=?, metrics_json=?, artifacts_json=?, error=?
            WHERE run_id=?
            """,
            (status, _utc_now(), _json(metrics), _json(artifacts or {}), error, run_id),
        )


def upsert_paper(
    conn: sqlite3.Connection,
    paper: dict[str, Any],
    run_id: str,
) -> str:
    paper_id = paper_id_for(paper)
    now = _utc_now()
    venue = str(
        paper.get("venue")
        or paper.get("journal")
        or paper.get("conference")
        or paper.get("publisher")
        or ""
    )
    conn.execute(
        """
        INSERT INTO papers(
          paper_id, identity, title, abstract, page_url, pdf_url, doi, year, venue,
          source_name, source_domain, authors_json, keywords_json, paper_json,
          created_at, updated_at, last_seen_at, last_seen_run_id
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
          title=COALESCE(NULLIF(excluded.title, ''), papers.title),
          abstract=COALESCE(NULLIF(excluded.abstract, ''), papers.abstract),
          page_url=COALESCE(NULLIF(excluded.page_url, ''), papers.page_url),
          pdf_url=COALESCE(NULLIF(excluded.pdf_url, ''), papers.pdf_url),
          doi=COALESCE(NULLIF(excluded.doi, ''), papers.doi),
          year=COALESCE(NULLIF(excluded.year, ''), papers.year),
          venue=COALESCE(NULLIF(excluded.venue, ''), papers.venue),
          source_name=COALESCE(NULLIF(excluded.source_name, ''), papers.source_name),
          source_domain=COALESCE(NULLIF(excluded.source_domain, ''), papers.source_domain),
          authors_json=excluded.authors_json,
          keywords_json=excluded.keywords_json,
          paper_json=excluded.paper_json,
          updated_at=excluded.updated_at,
          last_seen_at=excluded.last_seen_at,
          last_seen_run_id=excluded.last_seen_run_id
        """,
        (
            paper_id,
            paper_identity(paper),
            str(paper.get("title") or ""),
            str(paper.get("abstract") or ""),
            str(paper.get("page_url") or ""),
            str(paper.get("pdf_url") or ""),
            str(paper.get("doi") or ""),
            str(paper.get("year") or ""),
            venue,
            str(paper.get("source_name") or ""),
            str(paper.get("source_domain") or ""),
            _json(_listify(paper.get("authors"))),
            _json(_listify(paper.get("keywords"))),
            _json(paper),
            now,
            now,
            now,
            run_id,
        ),
    )
    return paper_id


def record_discovered_papers(
    config: AppConfig,
    run_id: str,
    papers: list[dict[str, Any]],
) -> None:
    init_library_store(config)
    with _connect(config) as conn:
        for paper in papers:
            paper_id = upsert_paper(conn, paper, run_id)
            _sync_fts(conn, paper_id)


def record_ranked_items(
    config: AppConfig,
    run_id: str,
    ranked: list[dict[str, Any]],
    selected_paper_ids: set[str] | None = None,
) -> None:
    init_library_store(config)
    selected = selected_paper_ids or set()
    with _connect(config) as conn:
        for item in ranked:
            paper = item.get("paper", {})
            relevance = item.get("relevance", {})
            paper_id = upsert_paper(conn, paper, run_id)
            score_id = _stable_id("score", run_id, paper_id)
            conn.execute(
                """
                INSERT INTO paper_scores(
                  score_id, run_id, paper_id, score, priority, selected_for_summary,
                  tags_json, matched_fields_json, relevance_json, scored_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, paper_id) DO UPDATE SET
                  score=excluded.score,
                  priority=excluded.priority,
                  selected_for_summary=excluded.selected_for_summary,
                  tags_json=excluded.tags_json,
                  matched_fields_json=excluded.matched_fields_json,
                  relevance_json=excluded.relevance_json,
                  scored_at=excluded.scored_at
                """,
                (
                    score_id,
                    run_id,
                    paper_id,
                    float(relevance.get("score") or 0.0),
                    str(item.get("priority") or "low"),
                    1 if paper_id in selected else 0,
                    _json(relevance.get("tags") or []),
                    _json(relevance.get("matched_fields") or {}),
                    _json(relevance),
                    _utc_now(),
                ),
            )
            _sync_fts(conn, paper_id)


def record_summarized_items(
    config: AppConfig,
    run_id: str,
    items: list[dict[str, Any]],
    model: str = "",
) -> None:
    init_library_store(config)
    with _connect(config) as conn:
        for item in items:
            paper = item.get("paper", {})
            paper_id = upsert_paper(conn, paper, run_id)
            _upsert_article(conn, run_id, paper_id, paper, item)
            _upsert_summary(conn, run_id, paper_id, item, model)
            _upsert_reading_task(conn, run_id, paper_id, item)
            _sync_fts(conn, paper_id)


def _upsert_article(
    conn: sqlite3.Connection,
    run_id: str,
    paper_id: str,
    paper: dict[str, Any],
    item: dict[str, Any],
) -> None:
    article = item.get("article") or {}
    content = str(item.get("article_content") or "")
    article_id = _stable_id("article", run_id, paper_id)
    content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest() if content else ""
    conn.execute(
        """
        INSERT INTO article_texts(
          article_id, paper_id, run_id, url, fetcher, status, title, excerpt,
          content, content_sha1, length, error, fetched_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, paper_id) DO UPDATE SET
          url=excluded.url,
          fetcher=excluded.fetcher,
          status=excluded.status,
          title=excluded.title,
          excerpt=excluded.excerpt,
          content=excluded.content,
          content_sha1=excluded.content_sha1,
          length=excluded.length,
          error=excluded.error,
          fetched_at=excluded.fetched_at
        """,
        (
            article_id,
            paper_id,
            run_id,
            str(paper.get("page_url") or article.get("url") or ""),
            str(article.get("fetcher") or ""),
            str(article.get("status") or ""),
            str(article.get("title") or ""),
            str(article.get("excerpt") or ""),
            content,
            content_hash,
            int(article.get("length") or len(content)),
            str(article.get("error") or ""),
            _utc_now(),
        ),
    )


def _upsert_summary(
    conn: sqlite3.Connection,
    run_id: str,
    paper_id: str,
    item: dict[str, Any],
    model: str,
) -> None:
    summary = item.get("summary") or {}
    summary_text = ""
    why_it_matters = ""
    schedule_suggestion = ""
    fallback = 0
    if isinstance(summary, dict):
        summary_text = str(summary.get("summary") or "")
        why_it_matters = str(summary.get("why_it_matters") or "")
        schedule_suggestion = str(summary.get("schedule_suggestion") or "")
        fallback = 1 if summary.get("fallback") else 0
    else:
        summary_text = str(summary)
        summary = {"summary": summary_text}

    summary_id = _stable_id("summary", run_id, paper_id)
    conn.execute(
        """
        INSERT INTO summaries(
          summary_id, paper_id, run_id, summary_text, why_it_matters,
          schedule_suggestion, summary_json, model, fallback, summarized_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, paper_id) DO UPDATE SET
          summary_text=excluded.summary_text,
          why_it_matters=excluded.why_it_matters,
          schedule_suggestion=excluded.schedule_suggestion,
          summary_json=excluded.summary_json,
          model=excluded.model,
          fallback=excluded.fallback,
          summarized_at=excluded.summarized_at
        """,
        (
            summary_id,
            paper_id,
            run_id,
            summary_text,
            why_it_matters,
            schedule_suggestion,
            _json(summary),
            model,
            fallback,
            _utc_now(),
        ),
    )


def _upsert_reading_task(
    conn: sqlite3.Connection,
    run_id: str,
    paper_id: str,
    item: dict[str, Any],
) -> None:
    paper = item.get("paper", {})
    summary = item.get("summary") or {}
    priority = str(item.get("priority") or "normal")
    title = str(paper.get("title") or "Untitled")
    url = str(paper.get("page_url") or "")
    summary_text = str(summary.get("summary") or "") if isinstance(summary, dict) else str(summary)
    actions = summary.get("next_actions") if isinstance(summary, dict) else []
    if isinstance(actions, str):
        actions = [actions]
    if not isinstance(actions, list):
        actions = []
    schedule_date = _schedule_date(priority)
    task_id = _stable_id("task", run_id, paper_id)
    org_text = _render_task_org(title, url, priority, schedule_date, summary_text, actions)
    conn.execute(
        """
        INSERT INTO reading_tasks(
          task_id, paper_id, run_id, title, url, priority, schedule_date, status,
          summary, next_actions_json, org_text, created_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, 'todo', ?, ?, ?, ?)
        ON CONFLICT(run_id, paper_id) DO UPDATE SET
          title=excluded.title,
          url=excluded.url,
          priority=excluded.priority,
          schedule_date=excluded.schedule_date,
          summary=excluded.summary,
          next_actions_json=excluded.next_actions_json,
          org_text=excluded.org_text
        """,
        (
            task_id,
            paper_id,
            run_id,
            title,
            url,
            priority,
            schedule_date,
            summary_text,
            _json(actions),
            org_text,
            _utc_now(),
        ),
    )


def _schedule_date(priority: str) -> str:
    offsets = {
        "urgent": 1,
        "high": 3,
        "normal": 7,
        "low": 14,
    }
    target = datetime.now(timezone.utc).date() + timedelta(days=offsets.get(priority, 7))
    return target.isoformat()


def _render_task_org(
    title: str,
    url: str,
    priority: str,
    schedule_date: str,
    summary: str,
    actions: list[Any],
) -> str:
    clean_title = re.sub(r"[\r\n]+", " ", title).strip()
    clean_summary = re.sub(r"[\r\n]+", " ", summary).strip()
    lines = [
        f"* TODO [{priority.upper()}] 阅读：{clean_title}",
        f"SCHEDULED: <{schedule_date}>",
        ":PROPERTIES:",
        f":URL: {url}",
        ":END:",
        "",
        f"- 摘要：{clean_summary}",
    ]
    for action in actions[:5]:
        clean_action = re.sub(r"[\r\n]+", " ", str(action)).strip()
        lines.append(f"- 下一步：{clean_action}")
    return "\n".join(lines)


def _topic_slug(value: str) -> str:
    lowered = str(value or "").strip().lower()
    for prefix in TOPIC_PREFIXES:
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
            break
    lowered = lowered.replace("&", " and ").replace("_", "-")
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered


def _topic_label(value: str, slug: str | None = None) -> str:
    cleaned = str(value or "").strip()
    for prefix in TOPIC_PREFIXES:
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = re.sub(r"[\s_/]+", " ", cleaned).strip(" -")
    resolved_slug = slug or _topic_slug(cleaned)
    if resolved_slug in TOPIC_DISPLAY_OVERRIDES:
        return TOPIC_DISPLAY_OVERRIDES[resolved_slug]
    if cleaned:
        if cleaned.islower():
            if cleaned in SHORT_TOPIC_CODES:
                return cleaned.upper()
            if "-" in cleaned or " " not in cleaned:
                cleaned = resolved_slug
            else:
                return cleaned.title()
        if "-" not in cleaned and any(char.isupper() for char in cleaned):
            return cleaned
    parts: list[str] = []
    for part in resolved_slug.split("-"):
        if not part:
            continue
        if part in SHORT_TOPIC_CODES or len(part) <= 4:
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def _is_valid_topic(label: str, slug: str) -> bool:
    if not slug:
        return False
    if slug in GENERIC_TOPIC_SLUGS:
        return False
    if slug.isdigit():
        return False
    if len(slug) < 3 and slug not in SHORT_TOPIC_CODES:
        return False
    if label.strip().lower() in {"general", "unknown"}:
        return False
    return True


def _paper_topic_candidates(
    paper: sqlite3.Row,
    latest_score: sqlite3.Row | None,
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    def add_candidate(raw_label: str, weight: float, source: str) -> None:
        slug = _topic_slug(raw_label)
        label = _topic_label(raw_label, slug)
        if not _is_valid_topic(label, slug):
            return
        existing = candidates.get(slug)
        if existing:
            existing["weight"] = max(float(existing["weight"]), weight)
            if len(label) > len(existing["label"]):
                existing["label"] = label
            existing["sources"].add(source)
            return
        candidates[slug] = {
            "slug": slug,
            "label": label,
            "weight": weight,
            "sources": {source},
        }

    if latest_score:
        for tag in _loads(latest_score["tags_json"], []):
            if not isinstance(tag, str):
                continue
            lowered = tag.strip().lower()
            if lowered.startswith("topic:"):
                add_candidate(tag.split(":", 1)[1], 2.0, "score-tag")
            elif lowered.startswith("keyword:"):
                add_candidate(tag.split(":", 1)[1], 1.5, "score-keyword")

        relevance = _loads(latest_score["relevance_json"], {})
        classification = relevance.get("classification") if isinstance(relevance, dict) else {}
        if isinstance(classification, dict):
            primary_keyword = classification.get("primary_keyword")
            if primary_keyword:
                add_candidate(str(primary_keyword), 1.6, "classification")
            for keyword in classification.get("keyword_hits") or []:
                add_candidate(str(keyword), 1.3, "classification")

    for keyword in _loads(paper["keywords_json"], []):
        add_candidate(str(keyword), 1.0, "paper-keyword")

    results = sorted(
        candidates.values(),
        key=lambda item: (-float(item["weight"]), item["label"].lower()),
    )
    return [
        {
            "slug": item["slug"],
            "label": item["label"],
            "weight": float(item["weight"]),
            "source": ", ".join(sorted(item["sources"])),
        }
        for item in results[:6]
    ]


def _latest_score_row(conn: sqlite3.Connection, paper_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM paper_scores
        WHERE paper_id=?
        ORDER BY scored_at DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()


def _rebuild_graph_store(conn: sqlite3.Connection) -> None:
    now = _utc_now()
    conn.execute("DELETE FROM topic_edges")
    conn.execute("DELETE FROM paper_topics")
    conn.execute("DELETE FROM topic_nodes")

    topic_nodes: dict[str, dict[str, Any]] = {}
    paper_links: dict[str, list[tuple[str, float]]] = {}
    mapping_rows: list[tuple[str, str, float, str, str, str]] = []
    papers = conn.execute("SELECT * FROM papers ORDER BY last_seen_at DESC").fetchall()

    for paper in papers:
        paper_id = str(paper["paper_id"])
        latest_score = _latest_score_row(conn, paper_id)
        score_value = None
        if latest_score and latest_score["score"] is not None:
            score_value = float(latest_score["score"])
        topics = _paper_topic_candidates(paper, latest_score)
        if not topics:
            continue

        paper_links[paper_id] = []
        for topic in topics:
            topic_id = _stable_id("topic", topic["slug"])
            node = topic_nodes.get(topic_id)
            if not node:
                node = {
                    "topic_id": topic_id,
                    "slug": topic["slug"],
                    "label": topic["label"],
                    "kind": "topic",
                    "paper_ids": set(),
                    "score_total": 0.0,
                    "score_count": 0,
                    "sources": set(),
                }
                topic_nodes[topic_id] = node
            node["paper_ids"].add(paper_id)
            node["sources"].update(
                source.strip() for source in str(topic["source"]).split(",") if source.strip()
            )
            if score_value is not None:
                node["score_total"] += score_value
                node["score_count"] += 1
            mapping_rows.append(
                (
                    topic_id,
                    paper_id,
                    float(topic["weight"]),
                    str(topic["source"]),
                    str(topic["label"]),
                    now,
                )
            )
            paper_links[paper_id].append((topic_id, float(topic["weight"])))

    for node in topic_nodes.values():
        paper_count = len(node["paper_ids"])
        score_count = int(node["score_count"])
        avg_score = (node["score_total"] / score_count) if score_count else 0.0
        conn.execute(
            """
            INSERT INTO topic_nodes(
              topic_id, slug, label, kind, paper_count, avg_score, metadata_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node["topic_id"],
                node["slug"],
                node["label"],
                node["kind"],
                paper_count,
                avg_score,
                _json({"sources": sorted(node["sources"])}),
                now,
                now,
            ),
        )

    if mapping_rows:
        conn.executemany(
            """
            INSERT INTO paper_topics(topic_id, paper_id, weight, source, label, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            mapping_rows,
        )

    edge_map: dict[tuple[str, str], dict[str, Any]] = {}
    for paper_id, topics in paper_links.items():
        ordered_topics = sorted(topics, key=lambda item: item[0])
        for (left_topic_id, left_weight), (right_topic_id, right_weight) in combinations(
            ordered_topics, 2
        ):
            if left_topic_id == right_topic_id:
                continue
            key = (left_topic_id, right_topic_id)
            edge = edge_map.get(key)
            if not edge:
                edge = {
                    "weight": 0.0,
                    "paper_ids": set(),
                }
                edge_map[key] = edge
            edge["weight"] += min(left_weight, right_weight)
            edge["paper_ids"].add(paper_id)

    for (source_topic_id, target_topic_id), edge in edge_map.items():
        conn.execute(
            """
            INSERT INTO topic_edges(
              edge_id, source_topic_id, target_topic_id, weight, shared_papers,
              metadata_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _stable_id("edge", source_topic_id, target_topic_id),
                source_topic_id,
                target_topic_id,
                float(edge["weight"]),
                len(edge["paper_ids"]),
                _json({"paper_ids": sorted(edge["paper_ids"])}),
                now,
                now,
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO schema_info(key, value) VALUES('topic_graph_version', ?)",
        (str(GRAPH_BUILD_VERSION),),
    )


def ensure_graph_store(config: AppConfig) -> None:
    init_library_store(config)
    with _connect(config) as conn:
        paper_count = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
        topic_count = conn.execute("SELECT count(*) FROM topic_nodes").fetchone()[0]
        latest_graph_update = conn.execute("SELECT max(updated_at) FROM topic_nodes").fetchone()[0]
        latest_paper_update = conn.execute("SELECT max(updated_at) FROM papers").fetchone()[0]
        graph_version = conn.execute(
            "SELECT value FROM schema_info WHERE key='topic_graph_version'"
        ).fetchone()
        graph_version_value = int(graph_version[0]) if graph_version and graph_version[0] else 0
        if paper_count == 0:
            if topic_count:
                _rebuild_graph_store(conn)
            return
        if (
            graph_version_value != GRAPH_BUILD_VERSION
            or not topic_count
            or not latest_graph_update
            or (
            latest_paper_update and latest_graph_update < latest_paper_update
            )
        ):
            _rebuild_graph_store(conn)


def _topic_item(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    preview_rows = conn.execute(
        """
        SELECT p.title
        FROM paper_topics pt
        JOIN papers p ON p.paper_id=pt.paper_id
        WHERE pt.topic_id=?
        ORDER BY pt.weight DESC, p.last_seen_at DESC
        LIMIT 3
        """,
        (row["topic_id"],),
    ).fetchall()
    metadata = _loads(row["metadata_json"], {})
    return {
        "topic_id": row["topic_id"],
        "slug": row["slug"],
        "label": row["label"],
        "kind": row["kind"],
        "paper_count": row["paper_count"],
        "avg_score": row["avg_score"],
        "sources": metadata.get("sources") if isinstance(metadata, dict) else [],
        "top_papers": [preview["title"] for preview in preview_rows],
    }


def _resolve_topic_row(conn: sqlite3.Connection, topic_ref: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM topic_nodes
        WHERE topic_id=? OR slug=?
        LIMIT 1
        """,
        (topic_ref, topic_ref),
    ).fetchone()


def library_graph(
    config: AppConfig,
    query: str = "",
    limit: int = 18,
) -> dict[str, Any]:
    ensure_graph_store(config)
    cleaned_query = query.strip().lower()
    with _connect(config) as conn:
        if cleaned_query:
            pattern = f"%{cleaned_query}%"
            topic_rows = conn.execute(
                """
                SELECT *
                FROM topic_nodes
                WHERE lower(label) LIKE ? OR lower(slug) LIKE ?
                ORDER BY paper_count DESC, avg_score DESC, label ASC
                LIMIT ?
                """,
                (pattern, pattern, limit),
            ).fetchall()
        else:
            topic_rows = conn.execute(
                """
                SELECT *
                FROM topic_nodes
                ORDER BY paper_count DESC, avg_score DESC, label ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        nodes = [_topic_item(conn, row) for row in topic_rows]
        topic_ids = [row["topic_id"] for row in topic_rows]
        edges: list[dict[str, Any]] = []
        if len(topic_ids) > 1:
            placeholders = ", ".join("?" for _ in topic_ids)
            edge_rows = conn.execute(
                f"""
                SELECT
                  e.*,
                  source.slug AS source_slug,
                  source.label AS source_label,
                  target.slug AS target_slug,
                  target.label AS target_label
                FROM topic_edges e
                JOIN topic_nodes source ON source.topic_id=e.source_topic_id
                JOIN topic_nodes target ON target.topic_id=e.target_topic_id
                WHERE e.source_topic_id IN ({placeholders})
                  AND e.target_topic_id IN ({placeholders})
                ORDER BY e.shared_papers DESC, e.weight DESC
                LIMIT ?
                """,
                (*topic_ids, *topic_ids, max(limit * 4, 24)),
            ).fetchall()
            edges = [
                {
                    "edge_id": row["edge_id"],
                    "source_topic_id": row["source_topic_id"],
                    "target_topic_id": row["target_topic_id"],
                    "source_slug": row["source_slug"],
                    "target_slug": row["target_slug"],
                    "source_label": row["source_label"],
                    "target_label": row["target_label"],
                    "weight": row["weight"],
                    "shared_papers": row["shared_papers"],
                }
                for row in edge_rows
            ]

        return {
            "query": query,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "paper_count": conn.execute("SELECT count(*) FROM papers").fetchone()[0],
                "topic_count": conn.execute("SELECT count(*) FROM topic_nodes").fetchone()[0],
                "edge_count": conn.execute("SELECT count(*) FROM topic_edges").fetchone()[0],
            },
        }


def search_library_topics(
    config: AppConfig,
    query: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    ensure_graph_store(config)
    cleaned_query = query.strip().lower()
    with _connect(config) as conn:
        if cleaned_query:
            pattern = f"%{cleaned_query}%"
            rows = conn.execute(
                """
                SELECT *
                FROM topic_nodes
                WHERE lower(label) LIKE ? OR lower(slug) LIKE ?
                ORDER BY paper_count DESC, avg_score DESC, label ASC
                LIMIT ?
                """,
                (pattern, pattern, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM topic_nodes
                ORDER BY paper_count DESC, avg_score DESC, label ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_topic_item(conn, row) for row in rows]


def get_topic_library(
    config: AppConfig,
    topic_ref: str,
    limit: int = 30,
) -> dict[str, Any] | None:
    ensure_graph_store(config)
    with _connect(config) as conn:
        row = _resolve_topic_row(conn, topic_ref)
        if not row:
            return None

        paper_rows = conn.execute(
            """
            SELECT
              pt.paper_id,
              pt.weight,
              COALESCE(
                (
                  SELECT ps.score
                  FROM paper_scores ps
                  WHERE ps.paper_id=pt.paper_id
                  ORDER BY ps.scored_at DESC
                  LIMIT 1
                ),
                0
              ) AS latest_score
            FROM paper_topics pt
            WHERE pt.topic_id=?
            ORDER BY latest_score DESC, pt.weight DESC,
              (
                SELECT p.last_seen_at
                FROM papers p
                WHERE p.paper_id=pt.paper_id
              ) DESC
            LIMIT ?
            """,
            (row["topic_id"], limit),
        ).fetchall()

        related_rows = conn.execute(
            """
            SELECT
              other.*,
              e.shared_papers,
              e.weight
            FROM topic_edges e
            JOIN topic_nodes other
              ON other.topic_id = CASE
                WHEN e.source_topic_id=? THEN e.target_topic_id
                ELSE e.source_topic_id
              END
            WHERE e.source_topic_id=? OR e.target_topic_id=?
            ORDER BY e.shared_papers DESC, e.weight DESC, other.paper_count DESC
            LIMIT 8
            """,
            (row["topic_id"], row["topic_id"], row["topic_id"]),
        ).fetchall()

        papers: list[dict[str, Any]] = []
        for paper_row in paper_rows:
            item = _library_item(conn, paper_row["paper_id"], None)
            item["topic_weight"] = paper_row["weight"]
            papers.append(item)

        return {
            "topic": _topic_item(conn, row),
            "papers": papers,
            "related_topics": [
                {
                    **_topic_item(conn, related_row),
                    "shared_papers": related_row["shared_papers"],
                    "edge_weight": related_row["weight"],
                }
                for related_row in related_rows
            ],
        }


def get_library_paper_detail(config: AppConfig, paper_id: str) -> dict[str, Any] | None:
    init_library_store(config)
    with _connect(config) as conn:
        paper = conn.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
        if not paper:
            return None

        detail = _library_item(conn, paper_id, None)
        article = conn.execute(
            """
            SELECT *
            FROM article_texts
            WHERE paper_id=?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        summary = conn.execute(
            """
            SELECT *
            FROM summaries
            WHERE paper_id=?
            ORDER BY summarized_at DESC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        task = conn.execute(
            """
            SELECT *
            FROM reading_tasks
            WHERE paper_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        topic_rows = conn.execute(
            """
            SELECT t.topic_id, t.slug, t.label, pt.weight, pt.source
            FROM paper_topics pt
            JOIN topic_nodes t ON t.topic_id=pt.topic_id
            WHERE pt.paper_id=?
            ORDER BY pt.weight DESC, t.paper_count DESC, t.label ASC
            """,
            (paper_id,),
        ).fetchall()

        detail["paper_json"] = _loads(paper["paper_json"], {})
        detail["article"] = dict(article) if article else {}
        detail["summary"] = dict(summary) if summary else {}
        detail["task"] = dict(task) if task else {}
        detail["topics"] = [
            {
                "topic_id": row["topic_id"],
                "slug": row["slug"],
                "label": row["label"],
                "weight": row["weight"],
                "source": row["source"],
            }
            for row in topic_rows
        ]
        return detail


def _sync_fts(conn: sqlite3.Connection, paper_id: str) -> None:
    try:
        paper = conn.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
        if not paper:
            return
        article = conn.execute(
            """
            SELECT content FROM article_texts
            WHERE paper_id=?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        summary = conn.execute(
            """
            SELECT summary_text FROM summaries
            WHERE paper_id=?
            ORDER BY summarized_at DESC
            LIMIT 1
            """,
            (paper_id,),
        ).fetchone()
        conn.execute("DELETE FROM library_fts WHERE paper_id=?", (paper_id,))
        conn.execute(
            """
            INSERT INTO library_fts(
              paper_id, title, abstract, authors, keywords, venue, article, summary
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                paper["title"],
                paper["abstract"],
                " ".join(_loads(paper["authors_json"], [])),
                " ".join(_loads(paper["keywords_json"], [])),
                paper["venue"],
                article["content"] if article else "",
                summary["summary_text"] if summary else "",
            ),
        )
    except sqlite3.OperationalError:
        return


def search_library(
    config: AppConfig,
    query: str,
    limit: int = 10,
    api_key: str = "",
    model_provider: str = "ds",
    model: str = "",
) -> list[dict[str, Any]]:
    init_library_store(config)
    cleaned_query = query.strip()
    with _connect(config) as conn:
        if cleaned_query:
            effective_query = cleaned_query
            if api_key and _has_cjk(cleaned_query):
                llm_keywords = _llm_search_keywords(
                    cleaned_query, api_key, model_provider,
                    model or config.runner_model,
                )
                if llm_keywords:
                    effective_query = " OR ".join(llm_keywords) + " " + cleaned_query
            fts_results = _search_library_fts(conn, effective_query, limit)
            if fts_results:
                return fts_results
            return _search_library_like(conn, effective_query, limit)
        return _recent_library_items(conn, limit)


def _query_aliases(query: str) -> list[str]:
    aliases: list[str] = []
    mapping = {
        "智慧农业": ["smart agriculture", "precision agriculture", "crop monitoring"],
        "精准农业": ["precision agriculture", "smart agriculture"],
        "综述": ["review", "survey"],
        "遥感": ["remote sensing"],
        "作物": ["crop"],
        "脑机": ["brain computer interface", "brain machine interface"],
        "神经接口": ["neural interface", "electrocorticography", "intracortical"],
        "类脑": ["brain inspired", "neurotechnology"],
        "深脑刺激": ["deep brain stimulation"],
        "基金": ["grant", "proposal", "nsfc"],
        "电力系统": ["power system", "electric power"],
        "电力": ["electric power", "power system"],
        "新能源": ["renewable energy", "clean energy"],
        "可再生能源": ["renewable energy"],
        "智能电网": ["smart grid", "power grid"],
        "电网": ["power grid", "smart grid", "distribution network"],
        "配电网": ["distribution network", "power distribution"],
        "微电网": ["microgrid"],
        "变压器": ["transformer", "power electronics"],
        "电力电子": ["power electronics", "power electronic"],
        "优化控制": ["optimal control", "optimization"],
        "调度": ["economic dispatch", "dispatch", "scheduling"],
        "优化调度": ["economic dispatch", "optimal dispatch"],
        "潮流": ["power flow", "optimal power flow"],
        "最优潮流": ["optimal power flow"],
        "机组组合": ["unit commitment"],
        "故障诊断": ["fault diagnosis", "fault detection"],
        "稳定性": ["stability", "power system stability"],
        "韧性": ["resilience", "robustness"],
        "分布式能源": ["distributed energy", "distributed generation"],
        "源网荷储": ["source grid load storage", "power system"],
        "虚拟电厂": ["virtual power plant"],
        "电压": ["voltage"],
        "储能": ["energy storage"],
        "光伏": ["photovoltaic", "solar"],
        "风电": ["wind power", "wind energy"],
        "负荷": ["load", "demand response"],
        "需求响应": ["demand response"],
    }
    for key, values in mapping.items():
        if key in query:
            aliases.extend(values)
    return aliases


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}", query):
        cleaned = token.strip().lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    for alias in _query_aliases(query):
        cleaned = alias.strip().lower()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms[:24]


def _search_library_fts(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    tokens = re.findall(r"[\w\-]+", query, flags=re.UNICODE)
    if not tokens:
        return []
    quoted_tokens = ['"' + token.replace('"', '""') + '"' for token in tokens[:12]]
    match_query = " OR ".join(quoted_tokens)
    try:
        rows = conn.execute(
            """
            SELECT f.paper_id, bm25(library_fts) AS rank
            FROM library_fts f
            WHERE library_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _library_item(conn, row["paper_id"], row["rank"])
        if item:
            items.append(item)
    return items


def _search_library_like(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    if not terms:
        return []
    clauses = []
    where_params: list[Any] = []
    score_params: list[Any] = []
    score_parts = []
    for term in terms:
        clauses.append(
            """
            lower(p.title || ' ' || p.abstract || ' ' || p.venue || ' ' ||
                  p.authors_json || ' ' || p.keywords_json || ' ' ||
                  COALESCE(a.content, '') || ' ' || COALESCE(s.summary_text, ''))
            LIKE ?
            """
        )
        where_params.append(f"%{term}%")
        score_parts.append(
            """
            CASE WHEN lower(p.title || ' ' || p.abstract || ' ' || p.venue || ' ' ||
                            p.authors_json || ' ' || p.keywords_json || ' ' ||
                            COALESCE(a.content, '') || ' ' || COALESCE(s.summary_text, ''))
                      LIKE ?
                 THEN 1 ELSE 0 END
            """
        )
        score_params.append(f"%{term}%")
    where_sql = " OR ".join(clauses)
    score_sql = " + ".join(score_parts) if score_parts else "0"
    rows = conn.execute(
        f"""
        SELECT DISTINCT p.paper_id, ({score_sql}) AS match_score
        FROM papers p
        LEFT JOIN article_texts a ON a.paper_id=p.paper_id
        LEFT JOIN summaries s ON s.paper_id=p.paper_id
        WHERE {where_sql}
        ORDER BY match_score DESC, p.last_seen_at DESC
        LIMIT ?
        """,
        (*score_params, *where_params, limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _library_item(conn, row["paper_id"], None)
        if item:
            items.append(item)
    return items


def _recent_library_items(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT paper_id
        FROM papers
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _library_item(conn, row["paper_id"], None)
        if item:
            items.append(item)
    return items


def _library_item(
    conn: sqlite3.Connection,
    paper_id: str,
    rank: float | None,
) -> dict[str, Any] | None:
    paper = conn.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
    if paper is None:
        return None
    latest_score = conn.execute(
        """
        SELECT * FROM paper_scores
        WHERE paper_id=?
        ORDER BY scored_at DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    latest_article = conn.execute(
        """
        SELECT status, fetcher, title, excerpt, length, error, fetched_at
        FROM article_texts
        WHERE paper_id=?
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    latest_summary = conn.execute(
        """
        SELECT summary_text, why_it_matters, schedule_suggestion, summary_json, summarized_at
        FROM summaries
        WHERE paper_id=?
        ORDER BY summarized_at DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    latest_task = conn.execute(
        """
        SELECT status, priority, schedule_date
        FROM reading_tasks
        WHERE paper_id=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    return {
        "paper_id": paper_id,
        "rank": rank,
        "title": paper["title"],
        "abstract": paper["abstract"],
        "page_url": paper["page_url"],
        "pdf_url": paper["pdf_url"],
        "doi": paper["doi"],
        "year": paper["year"],
        "venue": paper["venue"],
        "source_name": paper["source_name"],
        "source_domain": paper["source_domain"],
        "authors": _loads(paper["authors_json"], []),
        "keywords": _loads(paper["keywords_json"], []),
        "score": latest_score["score"] if latest_score else None,
        "priority": latest_score["priority"] if latest_score else "",
        "tags": _loads(latest_score["tags_json"], []) if latest_score else [],
        "article": dict(latest_article) if latest_article else {},
        "summary": {
            "summary": latest_summary["summary_text"],
            "why_it_matters": latest_summary["why_it_matters"],
            "schedule_suggestion": latest_summary["schedule_suggestion"],
            "summary_json": _loads(latest_summary["summary_json"], {}),
            "summarized_at": latest_summary["summarized_at"],
        }
        if latest_summary
        else {},
        "task": dict(latest_task) if latest_task else {},
        "last_seen_at": paper["last_seen_at"],
    }


def library_stats(config: AppConfig) -> dict[str, Any]:
    ensure_graph_store(config)
    with _connect(config) as conn:
        return {
            "db_path": str(config.library_db_path),
            "papers": conn.execute("SELECT count(*) FROM papers").fetchone()[0],
            "articles": conn.execute("SELECT count(*) FROM article_texts").fetchone()[0],
            "summaries": conn.execute("SELECT count(*) FROM summaries").fetchone()[0],
            "tasks": conn.execute("SELECT count(*) FROM reading_tasks").fetchone()[0],
            "runs": conn.execute("SELECT count(*) FROM workflow_runs").fetchone()[0],
            "topics": conn.execute("SELECT count(*) FROM topic_nodes").fetchone()[0],
            "edges": conn.execute("SELECT count(*) FROM topic_edges").fetchone()[0],
        }
