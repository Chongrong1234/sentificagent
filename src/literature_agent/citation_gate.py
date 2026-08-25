from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


PLACEHOLDER_PATTERN = re.compile(r"\[待引用:(\d+)\]")
CITE_PATTERN = re.compile(
    r"\\(?:parencite|textcite|autocite|smartcite|footcite|footcitetext|citep|citet|citeauthor|citeyearpar|citeyear|cite)\*?"
    r"(?:\[[^\]]*\]){0,2}\{([^}]+)\}"
)


@dataclass
class CitationCandidate:
    bib_key: str
    title: str
    authors: str
    year: int
    strength: int
    strength_reason: str
    abstract_snippet: str
    venue: str = ""
    bibtex: str = ""


@dataclass
class CitationPoint:
    placeholder: str
    index: int
    claim: str
    sentence: str
    span_start: int
    span_end: int


@dataclass
class CrossChapterHint:
    section_id: str
    title: str
    keys: list[str]
    message: str


def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    source = str(text or "")
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in re.finditer(r"[。！？!?]\s*|\n{2,}", source):
        end = match.end()
        sentence = source[start:end].strip()
        if sentence:
            spans.append((start, end, sentence))
        start = end
    tail = source[start:].strip()
    if tail:
        spans.append((start, len(source), tail))
    return spans


def _sentence_for_index(text: str, index: int) -> tuple[int, int, str]:
    for start, end, sentence in _split_sentences(text):
        if start <= index <= end:
            return start, end, sentence
    source = str(text or "")
    return 0, len(source), source.strip()


def _clean_claim(sentence: str, placeholder: str) -> str:
    text = str(sentence or "").replace(placeholder, "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[，、,;；]\s*$", "", text)
    return text[:280]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def detect_citation_need(
    paragraph: str,
    section_id: str,
) -> list[CitationPoint]:
    del section_id
    text = str(paragraph or "")
    points: list[CitationPoint] = []
    for match in PLACEHOLDER_PATTERN.finditer(text):
        placeholder = match.group(0)
        index = _safe_int(match.group(1), default=len(points) + 1)
        sent_start, sent_end, sentence = _sentence_for_index(text, match.start())
        points.append(
            CitationPoint(
                placeholder=placeholder,
                index=index,
                claim=_clean_claim(sentence, placeholder),
                sentence=sentence,
                span_start=sent_start,
                span_end=sent_end,
            )
        )
    return points


def rate_citation_strength(
    claim: str,
    candidate: dict,
) -> tuple[int, str]:
    claim_text = str(claim or "").lower()
    title = str(candidate.get("title") or "").lower()
    abstract = str(candidate.get("abstract") or candidate.get("summary") or candidate.get("claim") or "").lower()
    venue = str(candidate.get("venue") or "").lower()
    source = " ".join([title, abstract, venue])
    claim_terms = [token for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", claim_text) if len(token) >= 2]
    unique_terms = list(dict.fromkeys(claim_terms))
    overlap = sum(1 for token in unique_terms if token and token in source)
    if overlap >= 5:
        return 4, "标题或摘要与论点高度重合，属于直接支撑。"
    if overlap >= 3:
        return 3, "与论点核心术语有明显重叠，可作为较强支撑。"
    if overlap >= 1:
        return 2, "与论点存在相关背景联系，但支撑不够直接。"
    return 1, "仅有弱相关或主题邻近，不建议直接作为核心证据。"


def _candidate_bibtex(candidate: dict[str, Any]) -> str:
    bibtex = str(candidate.get("bibtex") or "").strip()
    if bibtex:
        return bibtex
    key = str(candidate.get("citation_key") or candidate.get("key") or "").strip()
    title = str(candidate.get("title") or "").strip()
    year_match = re.search(r"(19|20)\d{2}", str(candidate.get("year") or ""))
    year = year_match.group(0) if year_match else ""
    venue = str(candidate.get("venue") or "").strip()
    authors = candidate.get("authors") or []
    author_line = " and ".join(str(item).strip() for item in authors if str(item).strip())
    fields: list[str] = []
    if author_line:
        fields.append(f"  author = {{{author_line}}},")
    if title:
        fields.append(f"  title = {{{title}}},")
    if venue:
        fields.append(f"  journal = {{{venue}}},")
    if year:
        fields.append(f"  year = {{{year}}},")
    if not key or not fields:
        return ""
    return "@article{" + key + ",\n" + "\n".join(fields) + "\n}"


def search_candidates(
    claim: str,
    library_evidence: list[dict],
    min_strength: int = 2,
) -> list[CitationCandidate]:
    scored: list[CitationCandidate] = []
    for item in library_evidence or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("citation_key") or item.get("key") or "").strip()
        if not key:
            continue
        strength, reason = rate_citation_strength(claim, item)
        if strength < int(min_strength or 0):
            continue
        authors = item.get("authors") or []
        scored.append(
            CitationCandidate(
                bib_key=key,
                title=str(item.get("title") or ""),
                authors=", ".join(str(author).strip() for author in authors if str(author).strip()),
                year=_safe_int(re.search(r"(19|20)\d{2}", str(item.get("year") or "")) .group(0) if re.search(r"(19|20)\d{2}", str(item.get("year") or "")) else 0),
                strength=strength,
                strength_reason=reason,
                abstract_snippet=str(item.get("abstract") or item.get("summary") or item.get("claim") or "")[:260],
                venue=str(item.get("venue") or ""),
                bibtex=_candidate_bibtex(item),
            )
        )
    scored.sort(key=lambda item: (-item.strength, -item.year, item.title.lower()))
    return scored[:8]


def _citation_command(content: str) -> str:
    match = re.search(r"\\([A-Za-z]+)\*?(?:\[[^\]]*\]){0,2}\{[^}]+\}", str(content or ""))
    if match:
        return "\\" + str(match.group(1) or "cite")
    return r"\cite"


def apply_citations(
    content: str,
    citation_decisions: dict[str, list[str]],
) -> str:
    text = str(content or "")
    command = _citation_command(text)
    for placeholder, keys in (citation_decisions or {}).items():
        ordered = [str(key).strip() for key in (keys or []) if str(key).strip()]
        if not ordered:
            text = text.replace(str(placeholder), "[待补充引用]")
            continue
        cite = f"{command}{{{','.join(dict.fromkeys(ordered))}}}"
        text = text.replace(str(placeholder), cite)
    return text


def extract_bibtex_for_decisions(
    candidates: list[dict[str, Any]],
    citation_decisions: dict[str, list[str]],
) -> str:
    wanted = {
        str(key).strip()
        for keys in (citation_decisions or {}).values()
        for key in (keys or [])
        if str(key).strip()
    }
    seen: set[str] = set()
    entries: list[str] = []
    for item in candidates or []:
        key = str(item.get("bib_key") or item.get("citation_key") or item.get("key") or "").strip()
        if not key or key not in wanted or key in seen:
            continue
        bibtex = str(item.get("bibtex") or "").strip()
        if bibtex:
            entries.append(bibtex)
            seen.add(key)
    return "\n\n".join(entries).strip() + ("\n" if entries else "")


def check_cross_chapter_citations(
    current_section: str,
    locked_sections: dict[str, str],
) -> list[CrossChapterHint]:
    current_title = str(current_section or "")
    current_terms = {token.lower() for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", current_title)}
    hints: list[CrossChapterHint] = []
    for section_id, summary in (locked_sections or {}).items():
        keys = sorted({item.strip() for match in CITE_PATTERN.finditer(str(summary or "")) for item in str(match.group(1) or "").split(",") if item.strip()})
        if not keys:
            continue
        summary_terms = {token.lower() for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", str(summary or ""))}
        overlap = current_terms & summary_terms
        if current_terms and not overlap:
            continue
        hints.append(
            CrossChapterHint(
                section_id=str(section_id),
                title=str(section_id),
                keys=keys[:6],
                message=f"{section_id} 已引用 {', '.join(keys[:4])}，建议当前章节自然铺垫这些文献。",
            )
        )
    return hints[:6]


def summarize_pending_citations(
    content: str,
    section_id: str,
    library_evidence: list[dict[str, Any]],
    min_strength: int = 2,
) -> list[dict[str, Any]]:
    points = detect_citation_need(content, section_id)
    items: list[dict[str, Any]] = []
    for point in points:
        candidates = search_candidates(point.claim or point.sentence, library_evidence, min_strength=min_strength)
        items.append(
            {
                "placeholder": point.placeholder,
                "index": point.index,
                "claim": point.claim,
                "sentence": point.sentence,
                "candidate_count": len(candidates),
                "needs_attention": not bool(candidates),
                "candidates": [asdict(candidate) for candidate in candidates],
            }
        )
    return items
