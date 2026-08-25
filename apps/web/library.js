let latestLibraryGraph = null;
let latestTopicSearchItems = [];
let activeTopicId = "";
let activePaperId = "";

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  byId(id).textContent = value;
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(type, title, message) {
  const container = byId("toastContainer");
  if (!container) return;
  const icons = { success: "&#10003;", error: "&#10007;", info: "&#8505;" };
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span class="toast-msg"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></span>
    <button class="toast-close">&times;</button>
  `;
  toast.querySelector(".toast-close").addEventListener("click", () => toast.remove());
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, 4000);
}

function formatScore(value) {
  if (value === null || value === undefined || value === "") {
    return "--";
  }
  const number = Number(value);
  if (Number.isNaN(number)) {
    return String(value);
  }
  return number.toFixed(1);
}

function summarizeTopic(topic) {
  if (!topic) {
    return "";
  }
  return `${topic.paper_count || 0} 篇论文 / 平均分 ${formatScore(topic.avg_score)}`;
}

function renderLibraryStats(stats) {
  setText("topicCount", String(stats?.topic_count ?? 0));
  setText("graphEdgeCount", String(stats?.edge_count ?? 0));
  setText("graphPaperCount", String(stats?.paper_count ?? 0));
}

function renderTopicSearchList(items) {
  latestTopicSearchItems = items || [];
  const container = byId("topicSearchList");
  if (!items || !items.length) {
    container.innerHTML = '<p class="empty-state">没有匹配的主题。</p>';
    return;
  }
  container.innerHTML = items
    .map((topic) => `
      <button
        class="topic-card${activeTopicId === topic.topic_id ? " active" : ""}"
        data-topic-id="${escapeHtml(topic.topic_id)}"
        type="button"
      >
        <strong>${escapeHtml(topic.label)}</strong>
        <span>${escapeHtml(summarizeTopic(topic))}</span>
      </button>
    `)
    .join("");
}

function edgeIntensity(edge, maxSharedPapers) {
  const baseline = maxSharedPapers > 0 ? edge.shared_papers / maxSharedPapers : 0;
  return Math.max(0.18, Math.min(0.92, baseline));
}

function renderGraphCanvas(graph) {
  const container = byId("graphCanvas");
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!nodes.length) {
    container.innerHTML = '<p class="empty-state">当前文献库里还没有足够的主题数据可视化。</p>';
    return;
  }

  const maxPaperCount = Math.max(...nodes.map((node) => Number(node.paper_count) || 1), 1);
  const maxSharedPapers = Math.max(...edges.map((edge) => Number(edge.shared_papers) || 1), 1);
  const centerX = 620;
  const centerY = 360;
  const svgNodes = nodes.map((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1);
    const orbit = 210 + ((index % 4) * 52);
    const x = centerX + Math.cos(angle) * orbit;
    const y = centerY + Math.sin(angle) * (orbit * 0.74);
    const size = 28 + Math.round((Number(node.paper_count || 0) / maxPaperCount) * 34);
    return {
      ...node,
      x,
      y,
      size,
    };
  });
  const nodeMap = new Map(svgNodes.map((node) => [node.topic_id, node]));

  const edgeMarkup = edges
    .map((edge) => {
      const source = nodeMap.get(edge.source_topic_id);
      const target = nodeMap.get(edge.target_topic_id);
      if (!source || !target) {
        return "";
      }
      const opacity = edgeIntensity(edge, maxSharedPapers);
      return `
        <line
          x1="${source.x}"
          y1="${source.y}"
          x2="${target.x}"
          y2="${target.y}"
          stroke="rgba(15, 122, 91, ${opacity})"
          stroke-width="${1 + Math.min(5, Number(edge.shared_papers || 1))}"
        />
      `;
    })
    .join("");

  const nodeMarkup = svgNodes
    .map((node) => `
      <g class="graph-node${activeTopicId === node.topic_id ? " active" : ""}" data-topic-id="${escapeHtml(node.topic_id)}">
        <circle cx="${node.x}" cy="${node.y}" r="${node.size}" />
        <text x="${node.x}" y="${node.y - 4}" text-anchor="middle">${escapeHtml(node.label)}</text>
        <text class="graph-node-count" x="${node.x}" y="${node.y + 17}" text-anchor="middle">${escapeHtml(String(node.paper_count || 0))}</text>
      </g>
    `)
    .join("");

  container.innerHTML = `
    <svg viewBox="0 0 1240 720" role="img" aria-label="文献主题关系图">
      <defs>
        <radialGradient id="graphGlow" cx="50%" cy="50%" r="65%">
          <stop offset="0%" stop-color="rgba(201, 142, 34, 0.3)" />
          <stop offset="100%" stop-color="rgba(15, 122, 91, 0.04)" />
        </radialGradient>
      </defs>
      <rect x="0" y="0" width="1240" height="720" fill="url(#graphGlow)" rx="40" />
      ${edgeMarkup}
      ${nodeMarkup}
    </svg>
  `;
}

function paperListItemMarkup(item) {
  const tags = Array.isArray(item.tags) && item.tags.length ? item.tags.join(" / ") : "无标签";
  return `
    <button
      class="paper-card${activePaperId === item.paper_id ? " active" : ""}"
      data-paper-id="${escapeHtml(item.paper_id)}"
      type="button"
    >
      <div class="paper-card-score">${escapeHtml(formatScore(item.score))}</div>
      <div>
        <strong>${escapeHtml(item.title || "Untitled paper")}</strong>
        <div class="result-meta">
          <span>${escapeHtml(item.year || "Unknown year")}</span>
          <span>${escapeHtml(item.venue || item.source_name || "Unknown venue")}</span>
        </div>
        <p>${escapeHtml(tags)}</p>
      </div>
    </button>
  `;
}

function renderTopicPanel(topicData) {
  if (!topicData || !topicData.topic) {
    setText("activeTopicTitle", "尚未选择主题");
    setText("activeTopicMeta", "点击图谱节点或左侧主题列表");
    byId("topicPaperList").innerHTML = '<p class="empty-state">选中主题后显示相关论文。</p>';
    return;
  }

  const topic = topicData.topic;
  setText("activeTopicTitle", topic.label || "未命名主题");
  setText("activeTopicMeta", summarizeTopic(topic));
  const papers = topicData.papers || [];
  byId("topicPaperList").innerHTML = papers.length
    ? papers.map(paperListItemMarkup).join("")
    : '<p class="empty-state">该主题下暂时没有论文。</p>';
}

function detailMetaLine(label, value) {
  if (!value && value !== 0) {
    return "";
  }
  return `<div class="detail-meta-line"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function renderPaperDetail(item) {
  const container = byId("paperDetailPanel");
  if (!item) {
    container.innerHTML = '<p class="empty-state">选择论文后显示详情。</p>';
    return;
  }

  const topicChips = (item.topics || [])
    .map((topic) => `<span class="topic-tag">${escapeHtml(topic.label)}</span>`)
    .join("");
  const authorLine = Array.isArray(item.authors) && item.authors.length ? item.authors.join(", ") : "未知作者";
  const summary = item.summary?.summary_text || item.summary?.summary || "";
  const whyItMatters = item.summary?.why_it_matters || "";
  const articleExcerpt = item.article?.excerpt || "";
  const links = [
    item.page_url
      ? `<a class="result-link" href="${escapeHtml(item.page_url)}" target="_blank" rel="noreferrer">查看原文</a>`
      : "",
    item.pdf_url
      ? `<a class="result-link" href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noreferrer">下载 PDF</a>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  container.innerHTML = `
    <article class="paper-detail-card">
      <div class="paper-detail-head">
        <div class="score-badge">${escapeHtml(formatScore(item.score))}</div>
        <div>
          <h3>${escapeHtml(item.title || "Untitled paper")}</h3>
          <p class="paper-detail-authors">${escapeHtml(authorLine)}</p>
        </div>
      </div>
      <div class="paper-detail-meta">
        ${detailMetaLine("年份", item.year || "未知")}
        ${detailMetaLine("期刊/会议", item.venue || item.source_name || "未知")}
        ${detailMetaLine("优先级", item.priority || "未评分")}
        ${detailMetaLine("DOI", item.doi || "无")}
      </div>
      <div class="paper-detail-links">${links || '<span class="empty-state">当前没有原文或 PDF 链接。</span>'}</div>
      <div class="paper-detail-tags">${topicChips || '<span class="empty-state">该论文还没有主题标签。</span>'}</div>
      <section class="paper-detail-section">
        <h4>摘要</h4>
        <p>${escapeHtml(item.abstract || summary || "暂无摘要。")}</p>
      </section>
      <section class="paper-detail-section">
        <h4>智能体总结</h4>
        <p>${escapeHtml(summary || "暂无总结。")}</p>
      </section>
      <section class="paper-detail-section">
        <h4>Why It Matters</h4>
        <p>${escapeHtml(whyItMatters || articleExcerpt || "暂无附加说明。")}</p>
      </section>
    </article>
  `;
}

async function fetchLibraryGraph(query = "") {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  params.set("limit", "18");
  const response = await fetch(`/api/library/graph?${params.toString()}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "图谱读取失败");
  }
  latestLibraryGraph = data;
  renderLibraryStats(data.stats || {});
  renderGraphCanvas(data);
  if (!query) {
    setText("searchOutput", pretty(data));
  }
  return data;
}

async function fetchLibraryTopics(query = "") {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  params.set("limit", "14");
  const response = await fetch(`/api/library/topics?${params.toString()}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "主题检索失败");
  }
  renderTopicSearchList(data.items || []);
  setText("searchOutput", pretty(data));
  return data;
}

async function openTopic(topicId) {
  const response = await fetch(`/api/library/topic?id=${encodeURIComponent(topicId)}&limit=24`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "主题读取失败");
  }
  activeTopicId = data.topic?.topic_id || "";
  activePaperId = "";
  renderTopicSearchList(latestTopicSearchItems);
  renderGraphCanvas(latestLibraryGraph || { nodes: [], edges: [] });
  renderTopicPanel(data);
  renderPaperDetail(null);
  setText("searchOutput", pretty(data));
  return data;
}

async function openPaper(paperId) {
  const response = await fetch(`/api/library/paper?id=${encodeURIComponent(paperId)}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "论文详情读取失败");
  }
  activePaperId = paperId;
  byId("topicPaperList")
    .querySelectorAll("[data-paper-id]")
    .forEach((node) => node.classList.toggle("active", node.dataset.paperId === paperId));
  renderPaperDetail(data.paper || null);
  setText("searchOutput", pretty(data));
  return data;
}

async function refreshLibraryWorkspace(query = "") {
  const [graph, topics] = await Promise.all([
    fetchLibraryGraph(query),
    fetchLibraryTopics(query),
  ]);
  const firstTopic = topics.items?.[0] || graph.nodes?.[0];
  if (firstTopic) {
    await openTopic(firstTopic.topic_id);
  } else {
    renderTopicPanel(null);
    renderPaperDetail(null);
  }
}

byId("libraryTopicSearchButton").addEventListener("click", () => {
  refreshLibraryWorkspace(byId("libraryTopicQuery").value.trim()).catch((error) => {
    setText("searchOutput", error.message || String(error));
  });
});

byId("libraryGraphRefreshButton").addEventListener("click", () => {
  refreshLibraryWorkspace(byId("libraryTopicQuery").value.trim()).catch((error) => {
    setText("searchOutput", error.message || String(error));
  });
});

byId("topicSearchList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-topic-id]");
  if (!button) {
    return;
  }
  openTopic(button.dataset.topicId).catch((error) => {
    setText("searchOutput", error.message || String(error));
  });
});

byId("graphCanvas").addEventListener("click", (event) => {
  const node = event.target.closest("[data-topic-id]");
  if (!node) {
    return;
  }
  openTopic(node.dataset.topicId).catch((error) => {
    setText("searchOutput", error.message || String(error));
  });
});

byId("topicPaperList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-paper-id]");
  if (!button) {
    return;
  }
  openPaper(button.dataset.paperId).catch((error) => {
    setText("searchOutput", error.message || String(error));
  });
});

byId("libraryTopicQuery").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") {
    return;
  }
  event.preventDefault();
  refreshLibraryWorkspace(byId("libraryTopicQuery").value.trim()).catch((error) => {
    setText("searchOutput", error.message || String(error));
  });
});

refreshLibraryWorkspace().catch((error) => {
  setText("searchOutput", error.message || String(error));
});
