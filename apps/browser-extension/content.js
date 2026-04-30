function firstContent(selectors) {
  for (const selector of selectors) {
    const node = document.querySelector(selector);
    if (node && node.content) {
      return node.content.trim();
    }
    if (node && node.textContent) {
      return node.textContent.trim();
    }
  }
  return "";
}

function collectMetaContents(selectors) {
  const values = [];
  for (const selector of selectors) {
    const nodes = document.querySelectorAll(selector);
    for (const node of nodes) {
      const value = (node.content || node.textContent || "").trim();
      if (value) {
        values.push(value);
      }
    }
  }
  return [...new Set(values)];
}

function splitKeywords(rawValues) {
  const parts = [];
  for (const value of rawValues) {
    value
      .split(/[,;|]/)
      .map((item) => item.trim())
      .filter(Boolean)
      .forEach((item) => parts.push(item));
  }
  return [...new Set(parts)];
}

function parseJsonLd() {
  const scripts = document.querySelectorAll('script[type="application/ld+json"]');
  for (const script of scripts) {
    try {
      const parsed = JSON.parse(script.textContent);
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          if (item && typeof item === "object") {
            return item;
          }
        }
      }
      if (parsed && typeof parsed === "object") {
        return parsed;
      }
    } catch (_error) {
      continue;
    }
  }
  return {};
}

function detectPdfUrl() {
  if (window.location.hostname.includes("ieeexplore.ieee.org")) {
    const arnumberMatch = window.location.pathname.match(/\/document\/(\d+)/);
    if (arnumberMatch) {
      return `https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=${arnumberMatch[1]}`;
    }
  }

  if (window.location.hostname.includes("dl.acm.org")) {
    const doiMeta = firstContent(['meta[name="citation_doi"]']);
    if (doiMeta) {
      return `https://dl.acm.org/doi/pdf/${doiMeta}`;
    }
  }

  const metaPdf = firstContent([
    'meta[name="citation_pdf_url"]',
    'meta[property="citation_pdf_url"]'
  ]);
  if (metaPdf) {
    return metaPdf;
  }

  const anchors = Array.from(document.querySelectorAll('a[href]'));
  const pdfLink = anchors.find((anchor) => {
    const href = anchor.href || "";
    const text = (anchor.textContent || "").toLowerCase();
    return href.toLowerCase().includes(".pdf") || text.includes("pdf");
  });
  return pdfLink ? pdfLink.href : "";
}

function extractPaper() {
  const jsonLd = parseJsonLd();
  const documentMatch = window.location.pathname.match(/\/document\/(\d+)/);
  const title =
    firstContent([
      'meta[name="citation_title"]',
      'meta[property="og:title"]',
      'meta[name="dc.title"]'
    ]) ||
    jsonLd.headline ||
    document.title;

  const doi =
    firstContent([
      'meta[name="citation_doi"]',
      'meta[name="dc.identifier"]'
    ]) || "";

  const authors = collectMetaContents([
    'meta[name="citation_author"]',
    'meta[name="dc.creator"]'
  ]);

  const affiliations = collectMetaContents([
    'meta[name="citation_author_institution"]'
  ]);

  const abstractText =
    firstContent([
      'meta[name="description"]',
      'meta[property="og:description"]',
      'meta[name="citation_abstract"]',
      'meta[name="dc.description"]'
    ]) ||
    jsonLd.description ||
    "";

  const journal = firstContent([
    'meta[name="citation_journal_title"]',
    'meta[name="dc.source"]'
  ]);

  const conference = firstContent([
    'meta[name="citation_conference_title"]'
  ]);

  const publisher = firstContent([
    'meta[name="citation_publisher"]'
  ]);

  const year =
    firstContent([
      'meta[name="citation_publication_date"]',
      'meta[name="citation_date"]',
      'meta[name="dc.date"]'
    ]) || "";

  const siteName =
    firstContent([
      'meta[property="og:site_name"]'
    ]) || window.location.hostname;

  const keywords = splitKeywords(
    collectMetaContents([
      'meta[name="keywords"]',
      'meta[name="citation_keywords"]'
    ])
  );

  return {
    page_url: window.location.href,
    source_domain: window.location.hostname,
    publisher_hint: window.location.hostname,
    document_id: documentMatch ? documentMatch[1] : "",
    title,
    doi,
    authors,
    affiliations,
    abstract: abstractText,
    journal,
    conference,
    publisher,
    year,
    site_name: siteName,
    keywords,
    pdf_url: detectPdfUrl()
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "extractPaper") {
    return false;
  }

  sendResponse({
    ok: true,
    paper: extractPaper()
  });
  return false;
});
