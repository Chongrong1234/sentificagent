from __future__ import annotations

import json
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any
from urllib import error, request


ARXIV_API_URL = "http://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1/paper"
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


def _semantic_scholar_id(paper: dict[str, Any]) -> str:
    doi = str(paper.get("doi") or "").strip()
    if doi:
        return f"DOI:{doi.removeprefix('https://doi.org/')}"
    arxiv_match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", str(paper.get("page_url") or paper.get("pdf_url") or ""), re.I)
    if arxiv_match:
        return f"ARXIV:{arxiv_match.group(1).removesuffix('.pdf')}"
    return str(paper.get("semantic_scholar_id") or paper.get("id") or "").strip()


def semantic_scholar_references(paper: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    paper_id = _semantic_scholar_id(paper)
    if not paper_id:
        return []
    fields = "references.title,references.abstract,references.year,references.venue,references.authors,references.url,references.externalIds,references.openAccessPdf,references.publicationVenue"
    url = f"{SEMANTIC_SCHOLAR_API_URL}/{urllib.parse.quote(paper_id, safe=':')}"
    params = urllib.parse.urlencode({"fields": fields})
    try:
        payload = _http_get(f"{url}?{params}")
    except Exception:
        return []
    response = json.loads(payload)
    references = response.get("references") or []
    items: list[dict[str, Any]] = []
    for ref in references[:limit]:
        if not isinstance(ref, dict) or not ref.get("title"):
            continue
        external = ref.get("externalIds") or {}
        doi = str(external.get("DOI") or "").strip()
        arxiv = str(external.get("ArXiv") or "").strip()
        page_url = str(ref.get("url") or "").strip()
        if doi and not page_url:
            page_url = f"https://doi.org/{doi}"
        if arxiv and not page_url:
            page_url = f"https://arxiv.org/abs/{arxiv}"
        pdf_url = ""
        oa_pdf = ref.get("openAccessPdf") or {}
        if isinstance(oa_pdf, dict):
            pdf_url = str(oa_pdf.get("url") or "").strip()
        if arxiv and not pdf_url:
            pdf_url = f"https://arxiv.org/pdf/{arxiv}"
        venue = str(ref.get("venue") or "").strip()
        publication_venue = ref.get("publicationVenue") or {}
        if isinstance(publication_venue, dict) and publication_venue.get("name"):
            venue = str(publication_venue["name"])
        authors = [
            str(author.get("name") or "").strip()
            for author in (ref.get("authors") or [])
            if isinstance(author, dict) and author.get("name")
        ]
        items.append(
            {
                "id": str(ref.get("paperId") or doi or arxiv or ref.get("title")),
                "source_name": "semantic-scholar-reference",
                "source_domain": _domain_from_url(page_url) or "semanticscholar.org",
                "page_url": page_url,
                "pdf_url": pdf_url,
                "title": str(ref.get("title") or "").strip(),
                "abstract": str(ref.get("abstract") or "").strip(),
                "authors": authors,
                "affiliations": [],
                "keywords": [],
                "journal": venue,
                "conference": "",
                "venue": venue,
                "publisher": "",
                "year": str(ref.get("year") or "unknown-year"),
                "doi": f"https://doi.org/{doi}" if doi else "",
                "semantic_scholar_id": str(ref.get("paperId") or ""),
                "reference_of": paper.get("title", ""),
            }
        )
    return _dedupe(items)


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


def search_semantic_scholar(query: str, max_results: int) -> list[dict[str, Any]]:
    fields = "title,abstract,year,authors,venue,url,externalIds,openAccessPdf,publicationVenue,journal"
    params = urllib.parse.urlencode(
        {
            "query": query,
            "limit": min(max_results, 100),
            "fields": fields,
        }
    )
    payload = _http_get(f"{SEMANTIC_SCHOLAR_API_URL}/search?{params}")
    response = json.loads(payload)
    data = response.get("data", [])

    items: list[dict[str, Any]] = []
    for paper in data:
        if not isinstance(paper, dict) or not paper.get("title"):
            continue
        external = paper.get("externalIds") or {}
        doi = str(external.get("DOI") or "").strip()
        arxiv_id = str(external.get("ArXiv") or "").strip()
        page_url = str(paper.get("url") or "").strip()
        if doi and not page_url:
            page_url = f"https://doi.org/{doi}"
        if arxiv_id and not page_url:
            page_url = f"https://arxiv.org/abs/{arxiv_id}"
        pdf_url = ""
        oa_pdf = paper.get("openAccessPdf") or {}
        if isinstance(oa_pdf, dict):
            pdf_url = str(oa_pdf.get("url") or "").strip()
        if arxiv_id and not pdf_url:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        authors = [
            str(a.get("name") or "").strip()
            for a in (paper.get("authors") or [])
            if isinstance(a, dict) and a.get("name")
        ]
        venue = str(paper.get("venue") or "").strip()
        pub_venue = paper.get("publicationVenue") or {}
        if isinstance(pub_venue, dict) and pub_venue.get("name"):
            venue = str(pub_venue["name"])
        journal = paper.get("journal") or {}
        journal_name = str(journal.get("name") or venue or "").strip()

        items.append(
            {
                "id": str(paper.get("paperId") or doi or paper.get("title")),
                "source_name": "semantic-scholar",
                "source_domain": "semanticscholar.org",
                "page_url": page_url,
                "pdf_url": pdf_url,
                "title": str(paper.get("title") or "").strip(),
                "abstract": str(paper.get("abstract") or "").strip(),
                "authors": authors,
                "affiliations": [],
                "keywords": [],
                "journal": journal_name,
                "conference": "",
                "venue": journal_name or venue,
                "publisher": "",
                "year": str(paper.get("year") or "unknown-year"),
                "doi": f"https://doi.org/{doi}" if doi else "",
                "semantic_scholar_id": str(paper.get("paperId") or ""),
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
    selected_sources = sources or ["openalex", "arxiv", "semantic-scholar"]
    per_source = max(1, math.ceil(max_results / max(len(selected_sources), 1)))

    results: list[dict[str, Any]] = []
    for source_name in selected_sources:
        try:
            if source_name == "openalex":
                results.extend(search_openalex(query, per_source))
            elif source_name == "arxiv":
                results.extend(search_arxiv(query, per_source))
            elif source_name == "semantic-scholar":
                results.extend(search_semantic_scholar(query, per_source))
        except error.HTTPError as exc:
            if exc.code == 429:
                continue
            raise

    return _dedupe(results)[:max_results]
