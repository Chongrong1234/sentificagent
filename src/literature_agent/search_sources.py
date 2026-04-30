from __future__ import annotations

import json
import math
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any
from urllib import error, request


ARXIV_API_URL = "http://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
USER_AGENT = "scientific-agent/0.1"


def _http_get(url: str) -> str:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8")


def _normalize_year(raw_value: str) -> str:
    if len(raw_value) >= 4 and raw_value[:4].isdigit():
        return raw_value[:4]
    return "unknown-year"


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    position_map: dict[int, str] = {}
    for token, positions in inverted_index.items():
        for position in positions:
            position_map[int(position)] = token
    return " ".join(position_map[index] for index in sorted(position_map))


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or ""
    except ValueError:
        return ""


def search_arxiv(query: str, max_results: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    payload = _http_get(f"{ARXIV_API_URL}?{params}")
    root = ET.fromstring(payload)

    atom_ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", atom_ns):
        page_url = entry.findtext("atom:id", default="", namespaces=atom_ns).strip()
        title = entry.findtext("atom:title", default="", namespaces=atom_ns).strip()
        summary = entry.findtext("atom:summary", default="", namespaces=atom_ns).strip()
        published = entry.findtext("atom:published", default="", namespaces=atom_ns).strip()
        doi = entry.findtext("arxiv:doi", default="", namespaces=atom_ns).strip()
        journal_ref = entry.findtext("arxiv:journal_ref", default="", namespaces=atom_ns).strip()
        categories = [node.attrib.get("term", "") for node in entry.findall("atom:category", atom_ns)]
        authors = [
            node.findtext("atom:name", default="", namespaces=atom_ns).strip()
            for node in entry.findall("atom:author", atom_ns)
        ]

        pdf_url = ""
        for link in entry.findall("atom:link", atom_ns):
            href = link.attrib.get("href", "").strip()
            title_attr = link.attrib.get("title", "").strip().lower()
            if title_attr == "pdf" or href.endswith(".pdf"):
                pdf_url = href
                break
        if not pdf_url and page_url:
            pdf_url = page_url.replace("/abs/", "/pdf/") + ".pdf"

        items.append(
            {
                "id": page_url or title,
                "source_name": "arxiv",
                "source_domain": "arxiv.org",
                "page_url": page_url,
                "pdf_url": pdf_url,
                "title": title,
                "abstract": summary,
                "authors": authors,
                "affiliations": [],
                "keywords": [item for item in categories if item],
                "journal": journal_ref,
                "conference": "",
                "venue": journal_ref or "arXiv",
                "publisher": "arXiv",
                "year": _normalize_year(published),
                "doi": doi,
            }
        )
    return items


def search_openalex(query: str, max_results: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "search": query,
            "per-page": max_results,
            "mailto": "scientific-agent@example.local",
        }
    )
    payload = _http_get(f"{OPENALEX_API_URL}?{params}")
    response = json.loads(payload)
    results = response.get("results", [])

    items: list[dict[str, Any]] = []
    for result in results:
        ids = result.get("ids", {}) or {}
        best_oa = result.get("best_oa_location") or {}
        primary_location = result.get("primary_location") or {}
        primary_source = (primary_location.get("source") or {})
        best_source = (best_oa.get("source") or {})
        venue_name = (
            primary_source.get("display_name")
            or best_source.get("display_name")
            or ""
        )
        authorships = result.get("authorships", []) or []
        authors = [
            ((item.get("author") or {}).get("display_name") or "").strip()
            for item in authorships
            if (item.get("author") or {}).get("display_name")
        ]
        affiliations: list[str] = []
        for item in authorships:
            for institution in item.get("institutions", []) or []:
                display_name = (institution or {}).get("display_name")
                if display_name:
                    affiliations.append(str(display_name).strip())

        concepts = result.get("concepts", []) or []
        keywords = [
            (concept.get("display_name") or "").strip()
            for concept in concepts[:8]
            if concept.get("display_name")
        ]
        doi = str(result.get("doi") or ids.get("doi") or "").strip()
        page_url = (
            best_oa.get("landing_page_url")
            or primary_location.get("landing_page_url")
            or doi
            or str(result.get("id") or "")
        )
        pdf_url = str(best_oa.get("pdf_url") or "").strip()
        if not pdf_url:
            pdf_url = str(primary_location.get("pdf_url") or "").strip()
        title = str(result.get("display_name") or "").strip()
        abstract = _reconstruct_abstract(result.get("abstract_inverted_index"))

        items.append(
            {
                "id": str(result.get("id") or doi or title),
                "source_name": "openalex",
                "source_domain": _domain_from_url(page_url) or "openalex.org",
                "page_url": page_url,
                "pdf_url": pdf_url,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "affiliations": affiliations,
                "keywords": keywords,
                "journal": venue_name,
                "conference": "",
                "venue": venue_name,
                "publisher": "",
                "year": str(result.get("publication_year") or "unknown-year"),
                "doi": doi,
                "cited_by_count": int(result.get("cited_by_count") or 0),
            }
        )
    return items


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("doi") or item.get("id") or item.get("title") or "").strip().lower()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def search_literature(
    query: str,
    max_results: int,
    sources: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_sources = sources or ["openalex", "arxiv"]
    per_source = max(1, math.ceil(max_results / max(len(selected_sources), 1)))

    results: list[dict[str, Any]] = []
    for source_name in selected_sources:
        try:
            if source_name == "openalex":
                results.extend(search_openalex(query, per_source))
            elif source_name == "arxiv":
                results.extend(search_arxiv(query, per_source))
        except error.HTTPError as exc:
            if exc.code == 429:
                continue
            raise

    return _dedupe(results)[:max_results]
