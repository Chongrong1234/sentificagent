from __future__ import annotations

from typing import Any


def detect_publisher(page_url: str) -> str:
    lowered = (page_url or "").lower()
    if "ieeexplore.ieee.org" in lowered:
        return "ieee"
    if "dl.acm.org" in lowered:
        return "acm"
    return "generic"


def canonical_pdf_url(paper: dict[str, Any]) -> str:
    page_url = str(paper.get("page_url") or "")
    source_domain = str(paper.get("source_domain") or "")
    pdf_url = str(paper.get("pdf_url") or "")
    document_id = str(paper.get("document_id") or "")
    doi = str(paper.get("doi") or "")

    if "ieeexplore.ieee.org" in page_url or "ieeexplore.ieee.org" in source_domain:
        if document_id.isdigit():
            return f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={document_id}"
        if "arnumber=" in pdf_url:
            return pdf_url

    if "dl.acm.org" in page_url or "dl.acm.org" in source_domain:
        if doi:
            return f"https://dl.acm.org/doi/pdf/{doi}"
        if "/doi/pdf/" in pdf_url:
            return pdf_url

    return pdf_url
