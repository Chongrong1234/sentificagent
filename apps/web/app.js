let latestPatch = {};
let latestPlan = {};

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

async function fetchHealth() {
  const response = await fetch("/health");
  const data = await response.json();
  setText("configPath", data.config_path);
}

async function sendChat() {
  const apiKey = document.getElementById("apiKey").value.trim();
  const message = document.getElementById("prompt").value.trim();
  if (!message) {
    setText("chatOutput", "请输入消息。");
    return;
  }

  setText("chatOutput", "正在请求 Kimi...");
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      api_key: apiKey,
      message
    })
  });

  const data = await response.json();
  if (!response.ok) {
    setText("chatOutput", data.detail || data.error || "请求失败");
    return;
  }

  latestPatch = data.suggested_patch || {};
  latestPlan = data.agent_plan || {};
  setText("chatOutput", data.reply || "");
  setText("patchOutput", pretty(latestPatch));
  setText("planOutput", pretty(latestPlan));
  setText("configOutput", pretty(data.config_preview || {}));
  setText("modelLabel", data.model || "kimi-k2.5");
  document.getElementById("applyButton").disabled = Object.keys(latestPatch).length === 0;
  if (latestPlan.query) {
    document.getElementById("searchQuery").value = latestPlan.query;
  }
  if (latestPlan.max_results) {
    document.getElementById("maxResults").value = String(latestPlan.max_results);
  }
  if (latestPlan.min_score !== undefined) {
    document.getElementById("minScore").value = String(latestPlan.min_score);
  }
}

async function applyPatch() {
  if (!latestPatch || Object.keys(latestPatch).length === 0) {
    return;
  }

  const response = await fetch("/api/config/apply", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      patch: latestPatch
    })
  });
  const data = await response.json();
  if (!response.ok) {
    setText("chatOutput", data.detail || data.error || "应用补丁失败");
    return;
  }

  setText("configOutput", pretty(data.config || {}));
  setText("chatOutput", "配置已更新到本地文件。");
  document.getElementById("applyButton").disabled = true;
}

async function runSearch(autoDownload) {
  const query = document.getElementById("searchQuery").value.trim();
  const maxResults = Number(document.getElementById("maxResults").value || "20");
  const minScore = Number(document.getElementById("minScore").value || "8");
  if (!query) {
    setText("searchOutput", "请输入检索主题。");
    return;
  }

  setText("searchOutput", autoDownload ? "正在检索并下载..." : "正在检索...");
  const response = await fetch("/api/search", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      max_results: maxResults,
      min_score: minScore,
      auto_download: autoDownload
    })
  });
  const data = await response.json();
  if (!response.ok) {
    setText("searchOutput", data.detail || data.error || "检索失败");
    return;
  }

  const preview = {
    query: data.query,
    result_count: data.result_count,
    downloaded_count: data.downloaded_count,
    queued_count: data.queued_count,
    min_score: data.min_score,
    search_run_path: data.search_run_path,
    queue_path: data.queue_path,
    top_results: (data.results || []).slice(0, 10).map((item) => ({
      title: item.paper.title,
      year: item.paper.year,
      venue: item.paper.venue || item.paper.journal || item.paper.source_name,
      score: item.relevance.score,
      tags: item.relevance.tags,
      matched_fields: item.relevance.matched_fields,
      pdf_url: item.paper.pdf_url || ""
    })),
    downloaded: data.downloaded || []
  };
  setText("searchOutput", pretty(preview));
}

async function runAgentFlow() {
  if (!latestPlan || !latestPlan.query) {
    setText("searchOutput", "当前没有可执行的智能体计划，请先聊天。");
    return;
  }

  setText("searchOutput", "正在应用配置并执行智能体计划...");
  const response = await fetch("/api/agent/run", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      patch: latestPatch,
      plan: latestPlan,
      apply_patch_first: true
    })
  });
  const data = await response.json();
  if (!response.ok) {
    setText("searchOutput", data.detail || data.error || "智能体执行失败");
    return;
  }

  setText("configOutput", pretty(data.config || {}));
  setText("chatOutput", "已按智能体计划更新配置并执行检索。");
  const preview = {
    applied_patch: data.applied_patch,
    query: data.query,
    result_count: data.search.result_count,
    downloaded_count: data.search.downloaded_count,
    queued_count: data.search.queued_count,
    min_score: data.search.min_score,
    search_run_path: data.search.search_run_path,
    queue_path: data.search.queue_path,
    top_results: (data.search.results || []).slice(0, 10).map((item) => ({
      title: item.paper.title,
      year: item.paper.year,
      venue: item.paper.venue || item.paper.journal || item.paper.source_name,
      score: item.relevance.score,
      tags: item.relevance.tags,
      matched_fields: item.relevance.matched_fields,
      pdf_url: item.paper.pdf_url || ""
    }))
  };
  setText("searchOutput", pretty(preview));
}

document.getElementById("sendButton").addEventListener("click", sendChat);
document.getElementById("applyButton").addEventListener("click", applyPatch);
document.getElementById("agentRunButton").addEventListener("click", runAgentFlow);
document.getElementById("searchButton").addEventListener("click", () => runSearch(false));
document.getElementById("searchDownloadButton").addEventListener("click", () => runSearch(true));

fetchHealth().catch((error) => {
  setText("configOutput", error.message || String(error));
});
