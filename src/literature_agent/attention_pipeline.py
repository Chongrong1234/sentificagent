from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import threading
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
import time
from typing import Any
from urllib import error, request

from .chat import load_default_model_provider, load_provider_api_key, normalize_model_provider, provider_api_base
from .config import PROJECT_ROOT, AppConfig, load_config
from .library_store import (
    paper_id_for,
    record_discovered_papers,
    record_ranked_items,
    record_run_finish,
    record_run_start,
    record_summarized_items,
)
from .relevance import score_paper_relevance
from .search_sources import search_literature, semantic_scholar_references
from .survey_reporting import generate_attention_survey_report


USER_AGENT = "scientific-agent/0.1"
MAX_SUMMARY_INPUT_CHARS = 18000
MAX_FETCH_CHARS = 1_200_000
ARTICLE_SUMMARIZER_DIR = PROJECT_ROOT / "tools" / "article-summarizer"
JOBS: dict[str, "AttentionJob"] = {}
JOBS_LOCK = threading.Lock()


@dataclass
class AttentionJob:
    id: str
    status: str
    created_at: str
    updated_at: str
    payload: dict[str, Any]
    message: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "payload": self.payload,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "artifacts": self.artifacts,
        }


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._block_tags = {
            "article",
            "br",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "li",
            "main",
            "p",
            "section",
            "td",
            "th",
            "tr",
        }
        self._skip_tags = {
            "aside",
            "canvas",
            "footer",
            "form",
            "header",
            "iframe",
            "nav",
            "noscript",
            "script",
            "style",
            "svg",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if normalized in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if normalized == "title":
            self._in_title = True
        if normalized == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            content = attr_map.get("content", "").strip()
            if name and content:
                self.meta[name] = content
        if normalized in self._block_tags:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self._skip_tags and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if normalized == "title":
            self._in_title = False
        if normalized in self._block_tags:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = html.unescape(data).strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
            return
        self.text_parts.append(text)

    def readable_text(self) -> str:
        text = " ".join(self.text_parts)
        text = re.sub(r"\s*\n\s*", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def readable_title(self) -> str:
        return " ".join(self.title_parts).strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _stable_job_id(payload: dict[str, Any]) -> str:
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True) + _iso_now()
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _http_get(url: str, timeout: int = 45) -> tuple[str, str]:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        raw = response.read(MAX_FETCH_CHARS)
    charset = "utf-8"
    match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    return raw.decode(charset, errors="replace"), content_type


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc
    except ValueError:
        return ""


def _first_text(node: ET.Element, candidates: list[str]) -> str:
    for candidate in candidates:
        value = node.findtext(candidate)
        if value:
            return value.strip()
    return ""


def _entry_links(node: ET.Element) -> tuple[str, str]:
    page_url = ""
    pdf_url = ""
    for link in node.findall("{*}link"):
        href = link.attrib.get("href", "").strip()
        rel = link.attrib.get("rel", "").lower()
        typ = link.attrib.get("type", "").lower()
        title = link.attrib.get("title", "").lower()
        if href and not page_url and rel in ("", "alternate"):
            page_url = href
        if href and ("pdf" in typ or "pdf" in title or href.lower().endswith(".pdf")):
            pdf_url = href
    return page_url, pdf_url


def fetch_feed_entries(feed_url: str, limit: int) -> list[dict[str, Any]]:
    payload, _content_type = _http_get(feed_url)
    root = ET.fromstring(payload)
    entries = root.findall(".//{*}entry")
    if not entries:
        entries = root.findall(".//{*}item")

    papers: list[dict[str, Any]] = []
    for index, entry in enumerate(entries[:limit]):
        page_url, pdf_url = _entry_links(entry)
        rss_link = _first_text(entry, ["link", "{*}link"])
        if not page_url and rss_link:
            page_url = rss_link
        title = _first_text(entry, ["title", "{*}title"])
        abstract = _first_text(entry, ["summary", "{*}summary", "description", "{*}description"])
        published = _first_text(entry, ["published", "{*}published", "updated", "{*}updated", "pubDate", "{*}pubDate"])
        authors = [
            value.strip()
            for value in [
                author.findtext("{*}name") or author.text or ""
                for author in entry.findall("{*}author")
            ]
            if value and value.strip()
        ]
        categories = [
            category.attrib.get("term") or category.text or ""
            for category in entry.findall("{*}category")
        ]
        papers.append(
            {
                "id": page_url or f"{feed_url}#{index}",
                "source_name": "rss",
                "source_domain": _domain_from_url(feed_url),
                "page_url": page_url,
                "pdf_url": pdf_url,
                "title": html.unescape(title),
                "abstract": re.sub(r"<[^>]+>", " ", html.unescape(abstract)).strip(),
                "authors": authors,
                "affiliations": [],
                "keywords": [item.strip() for item in categories if item and item.strip()],
                "journal": _domain_from_url(feed_url),
                "conference": "",
                "venue": _domain_from_url(feed_url),
                "publisher": _domain_from_url(feed_url),
                "year": _year_from_text(published),
                "published_at": published,
            }
        )
    return papers


def _year_from_text(value: str) -> str:
    match = re.search(r"(19|20)\d{2}", value or "")
    return match.group(0) if match else "unknown-year"


def extract_article_text(url: str) -> dict[str, Any]:
    if not url:
        return {
            "status": "skipped",
            "fetcher": "stdlib-html",
            "title": "",
            "content": "",
            "excerpt": "",
            "length": 0,
            "error": "Missing URL",
        }

    try:
        payload, content_type = _http_get(url)
    except Exception as exc:
        return {
            "status": "error",
            "fetcher": "stdlib-html",
            "title": "",
            "content": "",
            "excerpt": "",
            "length": 0,
            "error": str(exc),
        }

    if "pdf" in content_type.lower():
        return {
            "status": "skipped",
            "fetcher": "stdlib-html",
            "title": "",
            "content": "",
            "excerpt": "",
            "length": 0,
            "error": "PDF text extraction is not enabled in the standard-library fetcher.",
        }

    parser = ReadableHTMLParser()
    parser.feed(payload)
    content = parser.readable_text()
    title = parser.meta.get("citation_title") or parser.meta.get("og:title") or parser.readable_title()
    excerpt = (
        parser.meta.get("citation_abstract")
        or parser.meta.get("description")
        or parser.meta.get("og:description")
        or content[:420]
    )
    return {
        "status": "success" if content else "empty",
        "fetcher": "stdlib-html",
        "title": title.strip(),
        "content": content,
        "excerpt": excerpt.strip(),
        "length": len(content),
        "error": "",
    }


def fetch_articles_with_readability(
    urls: list[str],
    payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not urls:
        return {}
    if bool(payload.get("disable_readability_fetch", False)):
        return {}
    if not ARTICLE_SUMMARIZER_DIR.exists():
        return {}

    node_modules = ARTICLE_SUMMARIZER_DIR / "node_modules"
    if not node_modules.exists():
        return {}

    timeout_seconds = int(payload.get("fetch_timeout_seconds") or 1800)
    with tempfile.TemporaryDirectory(prefix="scientific-agent-articles-") as temp_dir:
        input_path = Path(temp_dir) / "urls.txt"
        output_path = Path(temp_dir) / "articles.json"
        input_path.write_text("\n".join(urls), encoding="utf-8")
        env = os.environ.copy()
        env["FETCH_ONLY"] = "1"
        env.setdefault("PAGE_TIMEOUT_MS", str(payload.get("page_timeout_ms") or 25000))
        env.setdefault("SELECTOR_TIMEOUT_MS", str(payload.get("selector_timeout_ms") or 15000))
        command = ["node", "src/fetch-articles.js", str(input_path), str(output_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=ARTICLE_SUMMARIZER_DIR,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except Exception:
            return {}
        if completed.returncode != 0 or not output_path.exists():
            return {}
        try:
            rows = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    results: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return results
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        results[url] = {
            "status": str(row.get("status") or ""),
            "fetcher": "playwright-readability",
            "title": str(row.get("title") or ""),
            "content": str(row.get("content") or ""),
            "excerpt": str(row.get("excerpt") or ""),
            "length": int(row.get("length") or 0),
            "error": str(row.get("error") or ""),
        }
    return results


def _article_failure(article: dict[str, Any] | None) -> bool:
    if not article:
        return True
    status = str(article.get("status") or "").lower()
    return status not in {"success"} or not str(article.get("content") or "").strip()


def _load_api_key(payload: dict[str, Any]) -> str:
    provider = normalize_model_provider(str(payload.get("model_provider") or load_default_model_provider()))
    return load_provider_api_key(provider, str(payload.get("api_key") or "").strip())


def _chat_completion(config: AppConfig, payload: dict[str, Any], article_text: str) -> str:
    api_key = _load_api_key(payload)
    if not api_key:
        provider = normalize_model_provider(str(payload.get("model_provider") or load_default_model_provider()))
        raise RuntimeError(f"Missing API key for provider: {provider}")

    provider = normalize_model_provider(str(payload.get("model_provider") or load_default_model_provider()))
    base_url = str(payload.get("base_url") or os.environ.get("OPENAI_BASE_URL") or provider_api_base(provider)).rstrip("/")
    model = str(payload.get("model") or ("deepseek-chat" if provider == "ds" else config.runner_model or "kimi-k2.5"))
    prompt = str(
        payload.get("summary_prompt")
        or config.raw.get("attention", {}).get("summary_prompt")
        or """
你是科研注意力助手。请阅读论文或网页正文，输出 JSON object，不要使用 markdown。
字段：
- summary: 150字以内中文摘要
- why_it_matters: 这篇文献为什么值得关注
- problem: 论文试图解决什么问题
- novelty: 与已有工作的关键差异或新意
- hypothesis: 作者要验证的核心假设或判断
- related_work: 相关工作脉络与本文所在位置
- key_solution: 方法或系统方案的关键机制
- experimental_design: 实验设计、对比设置与评价思路
- datasets_code: 数据集、实验材料与代码开放情况
- evidence_support: 结果是否充分支撑假设，以及证据强弱
- contributions: 主要贡献数组
- future_work: 值得继续跟进的方向数组
- methods: 方法、模型或实验设计要点数组
- datasets: 数据集、作物、地区或实验材料数组
- limitations: 局限或风险数组
- next_actions: 后续阅读、复现、引用或加入课题组任务的行动数组
- schedule_suggestion: 建议安排，例如 精读/泛读/暂存
""".strip()
    )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": article_text[:MAX_SUMMARY_INPUT_CHARS]},
        ],
    }
    attempts = max(1, int(payload.get("summary_retries") or 3))
    data: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        req = request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code in {408, 409, 429, 500, 502, 503, 504} and attempt < attempts:
                time.sleep(min(30, 3 * attempt * attempt))
                continue
            raise RuntimeError(f"AI summary error: {exc.code} {detail}") from exc
        except Exception as exc:
            if attempt < attempts:
                time.sleep(min(30, 3 * attempt * attempt))
                continue
            raise RuntimeError(f"AI summary error: {exc}") from exc

    return (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def _top_keywords(text: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "also",
        "and",
        "are",
        "based",
        "between",
        "can",
        "for",
        "from",
        "has",
        "have",
        "into",
        "its",
        "more",
        "our",
        "paper",
        "study",
        "that",
        "the",
        "their",
        "these",
        "this",
        "using",
        "with",
    }
    counts: dict[str, int] = {}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text.lower()):
        if token in stopwords:
            continue
        counts[token] = counts.get(token, 0) + 1
    return [key for key, _value in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:12]]


def _parse_ai_summary(raw: str, text: str) -> dict[str, Any]:
    if raw:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            raise RuntimeError(f"AI summary returned invalid JSON: {cleaned[:300]}")
    raise RuntimeError("AI summary returned empty content")


def _attention_query(config: AppConfig, payload: dict[str, Any]) -> str:
    query = str(payload.get("query") or config.raw.get("attention", {}).get("query") or "").strip()
    if query:
        return query
    configured = config.raw.get("attention", {}).get("default_query")
    if configured:
        return str(configured)
    terms: list[str] = []
    for topic in config.raw.get("research_profile", {}).get("priority_topics", []):
        if topic.get("name"):
            terms.append(str(topic["name"]))
        terms.extend(str(item) for item in topic.get("aliases", [])[:2])
    return " ".join(terms[:18]) or "scientific literature"


def _unique_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for paper in papers:
        key = str(paper.get("doi") or paper.get("page_url") or paper.get("id") or paper.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(paper)
    return result


def _paper_key(paper: dict[str, Any]) -> str:
    raw = str(
        paper.get("doi")
        or paper.get("page_url")
        or paper.get("id")
        or paper.get("title")
        or ""
    ).strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() if raw else ""


def _seen_path(config: AppConfig) -> Path:
    return config.attention_state_dir / "seen.json"


def _load_seen(config: AppConfig) -> dict[str, Any]:
    path = _seen_path(config)
    if not path.exists():
        return {"items": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {"items": {}}
    if not isinstance(payload, dict):
        return {"items": {}}
    payload.setdefault("items", {})
    return payload


def _filter_seen(config: AppConfig, papers: list[dict[str, Any]], force: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen = _load_seen(config)
    if force:
        return papers, seen
    items = seen.get("items", {})
    filtered = [
        paper
        for paper in papers
        if paper.get("source_name") == "manual-url" or _paper_key(paper) not in items
    ]
    return filtered, seen


def _mark_seen(config: AppConfig, seen: dict[str, Any], items: list[dict[str, Any]]) -> None:
    seen_items = seen.setdefault("items", {})
    now = _iso_now()
    for item in items:
        paper = item.get("paper", item)
        key = _paper_key(paper)
        if not key:
            continue
        seen_items[key] = {
            "title": paper.get("title", ""),
            "page_url": paper.get("page_url", ""),
            "seen_at": now,
        }
    path = _seen_path(config)
    _write_json(path, seen)


def discover_papers(config: AppConfig, payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    query = _attention_query(config, payload)
    max_results = int(payload.get("max_results") or config.raw.get("attention", {}).get("max_results") or 20)
    papers: list[dict[str, Any]] = []

    configured_attention = config.raw.get("attention", {})
    if "urls" in payload:
        manual_urls = payload.get("urls") or []
    elif "manual_urls" in payload:
        manual_urls = payload.get("manual_urls") or []
    else:
        manual_urls = configured_attention.get("manual_urls") or []
    if isinstance(manual_urls, str):
        manual_urls = [manual_urls]
    for url in manual_urls:
        papers.append(
            {
                "id": str(url),
                "source_name": "manual-url",
                "source_domain": _domain_from_url(str(url)),
                "page_url": str(url),
                "pdf_url": "",
                "title": str(url),
                "abstract": "",
                "authors": [],
                "affiliations": [],
                "keywords": [],
                "journal": "",
                "conference": "",
                "venue": _domain_from_url(str(url)),
                "publisher": _domain_from_url(str(url)),
                "year": "unknown-year",
            }
        )
    if len(papers) >= max_results:
        return query, _unique_papers(papers)[:max_results]

    include_search = bool(
        payload.get(
            "include_search",
            configured_attention.get("include_search", False),
        )
    )
    if include_search:
        try:
            search_sources = payload.get("search_sources")
            if not search_sources:
                search_sources = config.raw.get("search", {}).get("sources") or None
            papers.extend(search_literature(query, max_results, search_sources))
        except Exception as exc:
            papers.append(
                {
                    "id": f"search-error:{query}",
                    "source_name": "search-error",
                    "source_domain": "",
                    "page_url": "",
                    "title": f"Search failed: {query}",
                    "abstract": str(exc),
                    "authors": [],
                    "affiliations": [],
                    "keywords": ["search-error"],
                    "journal": "",
                    "conference": "",
                    "venue": "",
                    "publisher": "",
                    "year": "unknown-year",
                    "pdf_url": "",
                    "fetch_error": str(exc),
                }
            )

    if "feed_urls" in payload:
        feed_urls = payload.get("feed_urls") or []
    else:
        feed_urls = configured_attention.get("feed_urls") or []
    if isinstance(feed_urls, str):
        feed_urls = [feed_urls]
    per_feed = max(1, max_results)
    for feed_url in feed_urls:
        try:
            papers.extend(fetch_feed_entries(str(feed_url), per_feed))
        except Exception as exc:
            papers.append(
                {
                    "id": str(feed_url),
                    "source_name": "rss",
                    "source_domain": _domain_from_url(str(feed_url)),
                    "page_url": str(feed_url),
                    "title": f"Feed fetch failed: {feed_url}",
                    "abstract": str(exc),
                    "authors": [],
                    "affiliations": [],
                    "keywords": ["fetch-error"],
                    "journal": "",
                    "conference": "",
                    "venue": "",
                    "publisher": "",
                    "year": "unknown-year",
                    "pdf_url": "",
                    "fetch_error": str(exc),
                }
            )

    unique = _unique_papers(papers)
    manual = [paper for paper in unique if paper.get("source_name") == "manual-url"]
    automatic = [paper for paper in unique if paper.get("source_name") != "manual-url"]
    return query, (manual + automatic)[:max_results]


def prioritize_papers(config: AppConfig, papers: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for paper in papers:
        if paper.get("fetch_error") or paper.get("source_name") == "search-error":
            continue
        relevance = score_paper_relevance(config, paper, query)
        ranked.append(
            {
                "paper": paper,
                "relevance": relevance,
                "priority": _priority_label(float(relevance["score"])),
            }
        )
    ranked.sort(key=lambda item: float(item["relevance"]["score"]), reverse=True)
    return ranked


def expand_reference_papers(
    config: AppConfig,
    payload: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attention = config.raw.get("attention", {}) or {}
    enabled = bool(payload.get("include_references", attention.get("include_references", False)))
    if not enabled:
        return []
    seed_limit = int(payload.get("reference_seed_limit") or attention.get("reference_seed_limit") or 4)
    per_paper_limit = int(payload.get("references_per_paper") or attention.get("references_per_paper") or 20)
    min_seed_score = float(payload.get("reference_min_seed_score") or attention.get("reference_min_seed_score") or 0)
    references: list[dict[str, Any]] = []
    seeds = [
        item
        for item in ranked
        if float(item.get("relevance", {}).get("score") or 0.0) >= min_seed_score
    ][:seed_limit]
    for item in seeds:
        references.extend(semantic_scholar_references(item["paper"], limit=per_paper_limit))
    return _unique_papers(references)


def _priority_label(score: float) -> str:
    if score >= 30:
        return "urgent"
    if score >= 18:
        return "high"
    if score >= 9:
        return "normal"
    return "low"


def _schedule_offset(priority: str) -> int:
    if priority == "urgent":
        return 1
    if priority == "high":
        return 3
    if priority == "normal":
        return 7
    return 14


def _org_date(offset_days: int) -> str:
    target = _utc_now().astimezone() + timedelta(days=offset_days)
    return target.strftime("<%Y-%m-%d %a>")


def _org_escape(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value or "").strip()


def build_org_schedule(items: list[dict[str, Any]]) -> str:
    lines = [
        "#+TITLE: Scientific Agent Attention Schedule",
        f"#+DATE: {_utc_now().date().isoformat()}",
        "",
    ]
    for item in items:
        paper = item["paper"]
        relevance = item["relevance"]
        summary = item.get("summary", {})
        title = _org_escape(str(paper.get("title") or "Untitled"))
        priority = item.get("priority", "normal")
        schedule_date = _org_date(_schedule_offset(priority))
        lines.extend(
            [
                f"* TODO [{priority.upper()}] 阅读：{title}",
                f"SCHEDULED: {schedule_date}",
                ":PROPERTIES:",
                f":URL: {paper.get('page_url', '')}",
                f":SCORE: {relevance.get('score', 0)}",
                f":TAGS: {','.join(relevance.get('tags', []))}",
                ":END:",
                "",
                f"- 摘要：{_org_escape(str(summary.get('summary', '')))}",
                f"- 价值：{_org_escape(str(summary.get('why_it_matters', '')))}",
                "- 下一步：",
            ]
        )
        actions = summary.get("next_actions") or []
        if isinstance(actions, str):
            actions = [actions]
        for action in actions[:5]:
            lines.append(f"  - { _org_escape(str(action)) }")
        lines.append("")
    return "\n".join(lines)


def summarize_ranked_item(
    config: AppConfig,
    payload: dict[str, Any],
    item: dict[str, Any],
    prefetched_article: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paper = item["paper"]
    page_url = str(paper.get("page_url") or "")
    article = prefetched_article or {}
    if _article_failure(article):
        article = extract_article_text(page_url)
    article_text = article.get("content") or paper.get("abstract") or paper.get("title") or ""
    if article.get("title") and (not paper.get("title") or str(paper.get("title")).startswith("http")):
        paper["title"] = article["title"]
    if article.get("excerpt") and not paper.get("abstract"):
        paper["abstract"] = article["excerpt"]

    raw_ai = ""
    use_ai = bool(payload.get("use_ai", True))
    if not use_ai:
        raise RuntimeError("Attention summarization requires AI and does not support fallback mode")
    raw_ai = _chat_completion(config, payload, article_text)
    summary = _parse_ai_summary(raw_ai, article_text)
    return {
        **item,
        "article": {
            key: value
            for key, value in article.items()
            if key != "content"
        },
        "article_content": article.get("content", ""),
        "content_preview": article_text[:1500],
        "summary": summary,
    }


def run_attention_pipeline(config: AppConfig, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("job_id") or _stable_job_id(payload))
    query, papers = discover_papers(config, payload)
    record_run_start(config, run_id, "attention", query, payload)
    force_refresh = bool(payload.get("force_refresh", config.raw.get("attention", {}).get("force_refresh", False)))
    fresh_papers, seen = _filter_seen(config, papers, force_refresh)
    record_discovered_papers(config, run_id, papers)
    ranked = prioritize_papers(config, fresh_papers, query)
    reference_papers = expand_reference_papers(config, payload, ranked)
    if reference_papers:
        papers = _unique_papers(papers + reference_papers)
        fresh_reference_papers, _seen = _filter_seen(config, reference_papers, force_refresh)
        record_discovered_papers(config, run_id, reference_papers)
        ranked = prioritize_papers(config, _unique_papers(fresh_papers + fresh_reference_papers), query)
    threshold = float(
        payload.get("min_score")
        if payload.get("min_score") not in (None, "")
        else config.raw.get("attention", {}).get("summarize_min_score", 3.0)
    )
    summarize_limit = int(payload.get("summarize_limit") or config.raw.get("attention", {}).get("summarize_limit") or 8)
    force = bool(payload.get("force_summarize", False))
    selected = [
        item
        for item in ranked
        if force
        or float(item["relevance"]["score"]) >= threshold
        or item["paper"].get("source_name") == "manual-url"
    ][:summarize_limit]
    selected_paper_ids = {paper_id_for(item["paper"]) for item in selected}
    record_ranked_items(config, run_id, ranked, selected_paper_ids)

    selected_urls = [
        str(item["paper"].get("page_url") or "").strip()
        for item in selected
        if str(item["paper"].get("page_url") or "").strip()
    ]
    prefetched_articles = fetch_articles_with_readability(selected_urls, payload)
    summarized: list[dict[str, Any]] = []
    worker_count = max(1, min(4, int(payload.get("summary_workers") or 3), len(selected) or 1))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                summarize_ranked_item,
                config,
                payload,
                item,
                prefetched_articles.get(str(item["paper"].get("page_url") or "")),
            ): index
            for index, item in enumerate(selected)
        }
        ordered: dict[int, dict[str, Any]] = {}
        for future in as_completed(future_map):
            ordered[future_map[future]] = future.result()
        summarized = [ordered[index] for index in sorted(ordered)]

    timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    run_path = config.attention_runs_dir / f"{timestamp}-{run_id}.json"
    summary_path = config.summaries_dir / f"{timestamp}-{run_id}.json"
    schedule_path = config.schedules_dir / f"{timestamp}-{run_id}.org"
    schedule_text = build_org_schedule(summarized)

    result = {
        "query": query,
        "discovered_count": len(papers),
        "fresh_count": len(fresh_papers),
        "ranked_count": len(ranked),
        "reference_expanded_count": len(reference_papers),
        "summarized_count": len(summarized),
        "min_score": threshold,
        "discovery_errors": [
            {
                "source": paper.get("page_url") or paper.get("id") or paper.get("source_domain", ""),
                "title": paper.get("title", ""),
                "error": paper.get("fetch_error", ""),
            }
            for paper in papers
            if paper.get("fetch_error")
        ],
        "discovered_preview": [
            {
                "title": paper.get("title", ""),
                "source_name": paper.get("source_name", ""),
                "page_url": paper.get("page_url", ""),
                "fetch_error": paper.get("fetch_error", ""),
            }
            for paper in papers[:20]
        ],
        "ranked": ranked,
        "summaries": summarized,
        "artifacts": {
            "run_path": str(run_path),
            "summary_path": str(summary_path),
            "schedule_path": str(schedule_path),
            "library_db_path": str(config.library_db_path),
            "reference_expanded_count": str(len(reference_papers)),
        },
    }
    report_meta = generate_attention_survey_report(config, payload, result)
    result["survey_report"] = {
        "report_id": report_meta.get("report_id", ""),
        "title": report_meta.get("title", ""),
        "generated_at": report_meta.get("generated_at", ""),
        "stats": report_meta.get("stats", {}),
        "preview": report_meta.get("preview", ""),
    }
    result["artifacts"]["report_markdown_path"] = str(report_meta.get("markdown_path") or "")
    result["artifacts"]["report_json_path"] = str(report_meta.get("json_path") or "")
    _write_json(run_path, result)
    _write_json(summary_path, {"summaries": summarized})
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(schedule_text, encoding="utf-8")
    _mark_seen(config, seen, ranked)
    record_summarized_items(config, run_id, summarized, model=str(payload.get("model") or config.runner_model or ""))
    record_run_finish(
        config,
        run_id,
        "completed",
        {
            "discovered_count": len(papers),
            "fresh_count": len(fresh_papers),
            "ranked_count": len(ranked),
            "reference_expanded_count": len(reference_papers),
            "summarized_count": len(summarized),
            "min_score": threshold,
        },
        result["artifacts"],
    )
    return result


def _update_job(job: AttentionJob, **changes: Any) -> None:
    with JOBS_LOCK:
        for key, value in changes.items():
            setattr(job, key, value)
        job.updated_at = _iso_now()
        JOBS[job.id] = job


def start_attention_job(payload: dict[str, Any], config_path: str | None = None) -> dict[str, Any]:
    job_id = _stable_job_id(payload)
    created = _iso_now()
    job = AttentionJob(
        id=job_id,
        status="queued",
        created_at=created,
        updated_at=created,
        payload={key: value for key, value in payload.items() if key != "api_key"},
        message="Attention pipeline queued.",
    )
    with JOBS_LOCK:
        JOBS[job_id] = job

    thread = threading.Thread(
        target=_run_job_thread,
        args=(job_id, payload, config_path),
        daemon=True,
    )
    thread.start()
    return job.as_dict()


def _run_job_thread(job_id: str, payload: dict[str, Any], config_path: str | None) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
    _update_job(job, status="running", message="Discovering, ranking, fetching, summarizing, and scheduling.")
    try:
        active_payload = dict(payload)
        active_payload["job_id"] = job_id
        config = load_config(config_path)
        result = run_attention_pipeline(config, active_payload)
        _update_job(
            job,
            status="completed",
            message="Attention pipeline completed.",
            result={
                key: value
                for key, value in result.items()
                if key not in {"ranked", "summaries"}
            }
            | {
                "top_ranked": result["ranked"][:10],
                "summaries": result["summaries"],
            },
            artifacts=result["artifacts"],
        )
    except Exception as exc:
        try:
            config = load_config(config_path)
            record_run_finish(config, job_id, "failed", {}, error=str(exc))
        except Exception:
            pass
        _update_job(job, status="failed", message="Attention pipeline failed.", error=str(exc))


def list_attention_jobs() -> list[dict[str, Any]]:
    with JOBS_LOCK:
        return sorted(
            [job.as_dict() for job in JOBS.values()],
            key=lambda item: item["created_at"],
            reverse=True,
        )


def get_attention_job(job_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return job.as_dict() if job else None
