/* ═══════════════════════════════════════════════════════════════════════════
   Scientific Agent — Workspace (Control Panel)
   ═══════════════════════════════════════════════════════════════════════════ */

let latestPatch = {};
let latestPlan = {};
let latestAttentionJobId = "";
let attentionPollTimer = null;
let activeWorkspaceTab = "research";

/* ── DOM Helpers ── */

function byId(id) { return document.getElementById(id); }

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value;
}

function pretty(value) { return JSON.stringify(value, null, 2); }

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function setMission(status, detail, mode = "ok") {
  const statusNode = byId("missionStatus");
  if (statusNode) statusNode.textContent = status;
  setText("missionDetail", detail);
}

function setButtonLoading(button, isLoading, text) {
  if (!button) return;
  if (isLoading) {
    button.dataset.originalText = button.textContent;
    button.textContent = text;
    button.disabled = true;
    return;
  }
  button.textContent = button.dataset.originalText || button.textContent;
  button.disabled = false;
}

/* ── Toast ── */

function showToast(type, title, message, duration = 4000) {
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
  setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, duration);
}

/* ── Tab Switching ── */

function switchWorkspaceTab(tabName) {
  activeWorkspaceTab = tabName;
  document.querySelectorAll(".workspace-tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  // Toggle control panels
  ["research", "attention", "reports"].forEach((name) => {
    const controls = byId(`controls-${name}`);
    const output = byId(`output-${name}`);
    if (controls) controls.classList.toggle("is-hidden", name !== tabName);
    if (output) output.classList.toggle("is-hidden", name !== tabName);
  });
}

/* ── API ── */

let modelSettingsState = {};

async function getJson(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "request failed");
  return data;
}

function renderModelSettings(data) {
  modelSettingsState = data || {};
  const provider = modelSettingsState.provider || "kimi";
  if (byId("modelProvider")) byId("modelProvider").value = provider;
  if (byId("modelName") && modelSettingsState.model) byId("modelName").value = modelSettingsState.model;
  if (byId("apiBase")) byId("apiBase").value = modelSettingsState.api_base || "";
  renderProviderKeyStatus(provider);
}

function renderProviderKeyStatus(provider) {
  const isDs = provider === "ds";
  const masked = isDs ? modelSettingsState.ds_key_masked : modelSettingsState.kimi_key_masked;
  const hasKey = isDs ? modelSettingsState.has_ds_key : modelSettingsState.has_kimi_key;
  const base = isDs ? modelSettingsState.ds_api_base : modelSettingsState.kimi_api_base;
  if (byId("apiBase") && base) byId("apiBase").placeholder = base;
  const statusNode = byId("apiKeyStatus");
  if (statusNode) {
    statusNode.textContent = hasKey
      ? `已保存密钥（${masked || "***"}），输入新值可覆盖`
      : "尚未设置密钥，请粘贴后保存";
  }
}

async function syncModelSettings() {
  const data = await getJson("/api/model-settings");
  renderModelSettings(data);
  return data;
}

async function saveModelSettings(options = {}) {
  const provider = byId("modelProvider")?.value || "kimi";
  const payload = { model_provider: provider };
  if (!options.providerOnly) {
    payload.model = byId("modelName")?.value.trim() || "";
    payload.api_base = byId("apiBase")?.value.trim() || "";
    const apiKey = byId("apiKey")?.value.trim() || "";
    if (apiKey) payload.api_key = apiKey;
  }
  const response = await fetch("/api/model-settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "model settings update failed");
  renderModelSettings(data);
  if (payload.api_key && byId("apiKey")) byId("apiKey").value = "";
  setMission("模型设置已保存", `当前全局模型：${data.label} / ${data.model || ""}`);
  showToast("success", "模型设置已保存", `${data.label} · ${data.model || "默认模型"}`);
  return data;
}

/* ── Plan & Results Rendering ── */

function renderPlan(plan) {
  const container = byId("planCards");
  if (!container) return;
  if (!plan || Object.keys(plan).length === 0) {
    container.innerHTML = '<p class="empty-state">暂无计划。</p>';
    return;
  }
  const items = [
    ["检索主题", plan.query || "未生成"],
    ["最大结果数", plan.max_results ?? "默认 20"],
    ["下载阈值", plan.min_score ?? "使用配置默认值"],
    ["自动下载", plan.auto_download ? "开启" : "关闭"],
  ];
  container.innerHTML = items.map(([label, value]) => `
    <article class="insight-item" style="display:flex;justify-content:space-between;align-items:center">
      <span class="text-xs text-muted">${escapeHtml(label)}</span>
      <strong class="text-sm">${escapeHtml(value)}</strong>
    </article>
  `).join("");
}

function normalizeResults(searchData) {
  if (!searchData) return [];
  const rawResults = searchData.results || searchData.search?.results || [];
  return rawResults.slice(0, 10).map((item) => ({
    title: item.paper?.title || "Untitled paper",
    year: item.paper?.year || "",
    venue: item.paper?.venue || item.paper?.journal || item.paper?.source_name || "Unknown venue",
    score: item.relevance?.score ?? "",
    tags: item.relevance?.tags || [],
    matchedFields: item.relevance?.matched_fields || [],
    pdfUrl: item.paper?.pdf_url || "",
  }));
}

function renderResults(searchData) {
  const container = byId("resultList");
  if (!container) return;
  const results = normalizeResults(searchData);
  if (!results.length) {
    container.innerHTML = '<p class="empty-state">暂无结果。</p>';
    return;
  }
  container.innerHTML = results.map((item) => {
    const tags = item.tags.length ? item.tags.join(" / ") : "无标签";
    const fields = item.matchedFields.length ? item.matchedFields.join(", ") : "未命中字段";
    const score = item.score === "" ? "--" : Number(item.score).toFixed(1);
    const pdf = item.pdfUrl
      ? `<a class="btn btn-ghost btn-sm mt-sm" href="${escapeHtml(item.pdfUrl)}" target="_blank" rel="noreferrer" style="text-decoration:none">打开 PDF</a>`
      : "";
    return `
      <article class="result-item">
        <div class="score-badge">${escapeHtml(score)}</div>
        <div>
          <h3>${escapeHtml(item.title)}</h3>
          <div class="result-meta">
            <span>${escapeHtml(item.year || "Unknown year")}</span>
            <span>${escapeHtml(item.venue)}</span>
            <span>${escapeHtml(fields)}</span>
          </div>
          <div class="result-tags">${escapeHtml(tags)}</div>
          ${pdf}
        </div>
      </article>
    `;
  }).join("");
}

/* ── Attention Rendering ── */

function renderAttentionJobs(jobs) {
  const container = byId("attentionJobs");
  if (!container) return;
  if (!jobs || !jobs.length) {
    container.innerHTML = '<p class="empty-state">还没有自动化注意力任务。</p>';
    return;
  }
  container.innerHTML = jobs.slice(0, 6).map((job) => `
    <article class="result-item">
      <div class="score-badge">${job.status === "completed" ? "&#10003;" : job.status === "running" ? "&#8635;" : "&#9711;"}</div>
      <div>
        <h3>${escapeHtml(job.message || "Attention job")}</h3>
        <div class="result-meta">
          <span>${escapeHtml(job.status)}</span>
          <span>${escapeHtml(job.id)}</span>
        </div>
        <p class="text-xs text-muted">${escapeHtml(job.updated_at || job.created_at || "")}</p>
      </div>
    </article>
  `).join("");
}

function normalizeAttentionSummaries(job) {
  return job?.result?.summaries || [];
}

function renderAttentionResult(job) {
  const container = byId("attentionResults");
  if (!container) return;
  if (!job) {
    container.innerHTML = '<p class="empty-state">任务完成后会显示高优先级摘要和日程文件。</p>';
    return;
  }
  if (job.status !== "completed") {
    container.innerHTML = `<p class="empty-state">任务状态：${escapeHtml(job.status)}。${escapeHtml(job.message || "")}</p>`;
    return;
  }
  const summaries = normalizeAttentionSummaries(job);
  const artifacts = job.artifacts || job.result?.artifacts || {};
  const surveyReport = job.result?.survey_report || {};
  const artifactBlock = `
    <article class="result-item">
      <div class="score-badge">&#128196;</div>
      <div>
        <h3>Artifacts</h3>
        <div class="result-meta"><span>${summaries.length} summaries</span></div>
        <p class="text-xs text-muted">运行记录：${escapeHtml(artifacts.run_path || "")}</p>
        <p class="text-xs text-muted">摘要文件：${escapeHtml(artifacts.summary_path || "")}</p>
        <p class="text-xs text-muted">日程文件：${escapeHtml(artifacts.schedule_path || "")}</p>
        <p class="text-xs text-muted">调研报告：${escapeHtml(artifacts.report_markdown_path || "")}</p>
      </div>
    </article>
  `;
  if (!summaries.length) {
    container.innerHTML = artifactBlock + '<p class="empty-state">没有达到阈值的摘要结果。</p>';
    return;
  }
  container.innerHTML = artifactBlock + summaries.slice(0, 8).map((item) => {
    const paper = item.paper || {};
    const summary = item.summary || {};
    const relevance = item.relevance || {};
    const score = relevance.score === undefined ? "--" : Number(relevance.score).toFixed(1);
    const url = paper.page_url
      ? `<a class="btn btn-ghost btn-sm mt-sm" href="${escapeHtml(paper.page_url)}" target="_blank" rel="noreferrer" style="text-decoration:none">打开原文</a>`
      : "";
    return `
      <article class="result-item">
        <div class="score-badge">${escapeHtml(score)}</div>
        <div>
          <h3>${escapeHtml(paper.title || "Untitled")}</h3>
          <div class="result-meta">
            <span>${escapeHtml(item.priority || "normal")}</span>
            <span>${escapeHtml(summary.schedule_suggestion || "待安排")}</span>
          </div>
          <div class="result-tags">${escapeHtml(summary.summary || "")}</div>
          ${url}
        </div>
      </article>
    `;
  }).join("");
}

/* ── Survey Reports Rendering ── */

function renderSurveyReports(items) {
  const container = byId("reportList");
  if (!container) return;
  if (!items || !items.length) {
    container.innerHTML = '<p class="empty-state">尚未生成调研报告。</p>';
    return;
  }
  container.innerHTML = items.slice(0, 8).map((item) => `
    <article class="result-item" data-report-id="${escapeHtml(item.report_id || "")}" style="cursor:pointer">
      <div class="score-badge">&#128214;</div>
      <div>
        <h3>${escapeHtml(item.name || item.report_id || "survey-report")}</h3>
        <div class="result-meta"><span>report</span><span>${escapeHtml(item.report_id || "")}</span></div>
        <p class="text-xs text-muted">${escapeHtml(item.updated_at || "")}</p>
      </div>
    </article>
  `).join("");
}

function renderSurveyReport(report) {
  const container = byId("reportPreview");
  if (!container) return;
  if (!report) {
    container.textContent = "生成后会在这里显示最新报告摘要。";
    return;
  }
  const metadata = report.metadata || {};
  const content = String(report.content || "").trim();
  const preview = content.length > 1600 ? `${content.slice(0, 1600)}...` : content;
  container.innerHTML = `
    <h3>${escapeHtml(metadata.title || report.report_id || "文献调研报告")}</h3>
    <p class="text-xs text-muted">${escapeHtml(report.path || "")}</p>
    <pre>${escapeHtml(preview || "报告内容为空。")}</pre>
  `;
}

function renderWritingResult(data) {
  const container = byId("writingResults");
  if (!container) return;
  if (!data || !data.run_id) {
    container.innerHTML = '<p class="empty-state">等待写作任务...</p>';
    return;
  }
  const artifacts = data.artifacts || {};
  const compile = data.compile || {};
  const evidence = data.evidence || [];
  const surveyReport = data.survey_report || {};
  const reviewReport = data.review_report || {};
  const evidenceItems = evidence.slice(0, 6).map((item) => `
    <article class="result-item">
      <div class="score-badge">${escapeHtml(item.year || "?")}</div>
      <div>
        <h3>${escapeHtml(item.title || "Untitled")}</h3>
        <div class="result-meta"><span>${escapeHtml(item.key || "P?")}</span></div>
        <p class="text-xs text-muted">${escapeHtml(item.summary || item.abstract || "")}</p>
      </div>
    </article>
  `).join("");
  container.innerHTML = `
    <article class="result-item">
      <div class="score-badge">&#9997;</div>
      <div>
        <h3>${escapeHtml(data.plan?.title || data.goal || "写作任务")}</h3>
        <div class="result-meta"><span>${escapeHtml(data.engine || "workflow")}</span><span>${escapeHtml(data.run_id)}</span></div>
        <p class="text-xs text-muted">TeX：${escapeHtml(artifacts.tex_path || "")}</p>
        <p class="text-xs text-muted">PDF：${escapeHtml(artifacts.pdf_path || compile.pdf_path || "未生成")}</p>
        <p class="text-xs text-muted">编译：${escapeHtml(compile.status || "unknown")} ${escapeHtml(compile.reason || "")}</p>
        <p class="text-xs text-muted">Surveyor：${escapeHtml(surveyReport.title || "未生成")}</p>
        <p class="text-xs text-muted">Reviewer：${escapeHtml(reviewReport.status || "未执行")} ${escapeHtml((reviewReport.warnings || []).join(", "))}</p>
      </div>
    </article>
    ${evidenceItems || '<p class="empty-state">没有检索到本地证据。</p>'}
  `;
}

/* ── Survey Report Operations ── */

async function loadSurveyReport(reportId) {
  if (!reportId) {
    renderSurveyReport(null);
    setText("reportOutput", "{}");
    return null;
  }
  const data = await getJson(`/api/library/report?id=${encodeURIComponent(reportId)}`);
  renderSurveyReport(data.report || null);
  setText("reportOutput", pretty(data.report || {}));
  return data.report || null;
}

async function refreshSurveyReports() {
  const limit = Number(byId("reportLimit")?.value || "8");
  const data = await getJson(`/api/library/reports?limit=${encodeURIComponent(String(limit))}`);
  const items = data.items || [];
  renderSurveyReports(items);
  if (items[0]?.report_id) {
    await loadSurveyReport(items[0].report_id);
  } else {
    renderSurveyReport(null);
    setText("reportOutput", "{}");
  }
  return items;
}

async function generateSurveyReport() {
  const query = byId("reportQuery")?.value.trim()
    || byId("searchQuery")?.value.trim()
    || latestPlan.query || "";
  const limit = Number(byId("reportLimit")?.value || "8");
  const language = byId("reportLanguage")?.value || "zh";
  const button = byId("reportGenerateButton");
  setButtonLoading(button, true, "生成中...");
  renderSurveyReport(null);
  setText("reportOutput", "正在汇总本地文献库...");
  setMission("生成调研报告", query ? `主题：${query}` : "正在按本地文献库整体生成。");
  try {
    const response = await fetch("/api/library/report/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit, language, title: query ? "" : "本地文献库全景调研报告" }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "生成调研报告失败");
    setText("reportOutput", pretty(data));
    await refreshSurveyReports();
    await loadSurveyReport(data.report_id || "");
    setMission("调研报告已生成", data.markdown_path || data.report_id || "已写入 reports 目录。");
    showToast("success", "报告已生成", data.report_id || "");
  } catch (error) {
    setText("reportOutput", error.message || String(error));
    renderSurveyReport(null);
    setMission("报告生成失败", error.message || String(error), "error");
    showToast("error", "生成失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

/* ── Attention Operations ── */

async function refreshAttentionJobs() {
  const response = await fetch("/api/attention/jobs");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "注意力任务读取失败");
  renderAttentionJobs(data.jobs || []);
  if (!latestAttentionJobId && data.jobs && data.jobs[0]) {
    latestAttentionJobId = data.jobs[0].id;
  }
  const activeJob = (data.jobs || []).find((job) => job.id === latestAttentionJobId) || (data.jobs || [])[0];
  if (activeJob) {
    renderAttentionResult(activeJob);
    setText("attentionOutput", pretty(activeJob));
    const reportId = activeJob.result?.survey_report?.report_id || "";
    if (reportId) loadSurveyReport(reportId).catch(() => undefined);
  }
  return activeJob;
}

function scheduleAttentionPoll() {
  if (attentionPollTimer) clearTimeout(attentionPollTimer);
  attentionPollTimer = setTimeout(async () => {
    try {
      const job = await refreshAttentionJobs();
      if (job && ["queued", "running"].includes(job.status)) scheduleAttentionPoll();
    } catch (error) {
      setText("attentionOutput", error.message || String(error));
    }
  }, 2500);
}

async function runAttentionPipeline() {
  const payload = {
    model_provider: byId("modelProvider")?.value || "kimi",
    use_ai: byId("attentionUseAi")?.checked,
  };
  const button = byId("attentionRunButton");
  setButtonLoading(button, true, "已启动...");
  if (byId("attentionResults")) {
    byId("attentionResults").innerHTML = '<p class="empty-state">后台正在发现、排序、抓取、总结并生成日程...</p>';
  }
  setMission("注意力任务启动", "后台任务会异步执行，可继续使用页面。");
  try {
    const response = await fetch("/api/attention/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "注意力任务启动失败");
    latestAttentionJobId = data.id;
    setText("attentionOutput", pretty(data));
    await refreshAttentionJobs();
    scheduleAttentionPoll();
    showToast("success", "任务已启动", data.id);
  } catch (error) {
    setText("attentionOutput", error.message || String(error));
    setMission("注意力任务失败", error.message || String(error), "error");
    showToast("error", "启动失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

/* ── Search & Agent Operations ── */

function summarizeSearch(data) {
  const search = data.search || data;
  const resultCount = search.result_count ?? 0;
  const downloaded = search.downloaded_count ?? 0;
  const queued = search.queued_count ?? 0;
  setText("resultCount", String(resultCount));
  setText("resultDetail", `下载 ${downloaded} 篇，排队 ${queued} 篇，阈值 ${search.min_score ?? "默认"}。`);
}

function buildSearchPreview(data) {
  const results = data.results || [];
  return {
    query: data.query,
    result_count: data.result_count,
    downloaded_count: data.downloaded_count,
    queued_count: data.queued_count,
    min_score: data.min_score,
    search_run_path: data.search_run_path,
    queue_path: data.queue_path,
    top_results: results.slice(0, 10).map((item) => ({
      title: item.paper.title,
      year: item.paper.year,
      venue: item.paper.venue || item.paper.journal || item.paper.source_name,
      score: item.relevance.score,
      tags: item.relevance.tags,
      matched_fields: item.relevance.matched_fields,
      pdf_url: item.paper.pdf_url || "",
    })),
    downloaded: data.downloaded || [],
  };
}

async function fetchDownloadQueue() {
  const response = await fetch("/api/download-queue");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "下载队列不可用");
  const batches = data.batches || [];
  const total = batches.reduce((sum, batch) => sum + (batch.items || []).length, 0);
  setText("queueCount", String(total));
  setText("queueDetail", total ? `${batches.length} 个批次等待浏览器扩展处理。` : "当前没有待处理下载。");
}

async function sendChat() {
  const message = byId("prompt").value.trim();
  const button = byId("sendButton");
  if (!message) {
    setMission("缺少输入", "先描述研究方向、会议偏好或下载策略。", "error");
    return;
  }
  setButtonLoading(button, true, "生成中...");
  setText("chatOutput", "正在请求模型...");
  setMission("规划中", "系统正在把研究目标转成配置补丁和检索计划。");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_provider: byId("modelProvider")?.value || "kimi", message }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "请求失败");
    latestPatch = data.suggested_patch || {};
    latestPlan = data.agent_plan || {};
    setText("chatOutput", data.reply || "");
    setText("patchOutput", pretty(latestPatch));
    setText("planOutput", pretty(latestPlan));
    renderPlan(latestPlan);
    setText("configOutput", pretty(data.config_preview || {}));
    if (byId("applyButton")) byId("applyButton").disabled = Object.keys(latestPatch).length === 0;
    if (latestPlan.query) {
      byId("searchQuery").value = latestPlan.query;
      if (byId("reportQuery") && !byId("reportQuery").value.trim()) byId("reportQuery").value = latestPlan.query;
    }
    if (latestPlan.max_results) byId("maxResults").value = String(latestPlan.max_results);
    if (latestPlan.min_score !== undefined) byId("minScore").value = String(latestPlan.min_score);
    setMission("计划就绪", latestPlan.query ? `已生成检索主题：${latestPlan.query}` : "已生成回应。");
  } catch (error) {
    setText("chatOutput", error.message || String(error));
    setMission("规划失败", error.message || String(error), "error");
  } finally {
    setButtonLoading(button, false);
    if (byId("applyButton")) byId("applyButton").disabled = Object.keys(latestPatch).length === 0;
  }
}

async function applyPatch() {
  if (!latestPatch || Object.keys(latestPatch).length === 0) return;
  const button = byId("applyButton");
  setButtonLoading(button, true, "应用中...");
  setMission("写入配置", "正在把建议补丁应用到本地配置文件。");
  try {
    const response = await fetch("/api/config/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch: latestPatch }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "应用补丁失败");
    setText("configOutput", pretty(data.config || {}));
    setText("chatOutput", "配置已更新到本地文件。");
    setMission("配置已更新", data.config_path || "补丁已写入本地文件。");
    showToast("success", "配置已更新", data.config_path || "");
  } catch (error) {
    setText("chatOutput", error.message || String(error));
    setMission("写入失败", error.message || String(error), "error");
    showToast("error", "写入失败", error.message);
  } finally {
    setButtonLoading(button, false);
    if (byId("applyButton")) byId("applyButton").disabled = true;
  }
}

async function runSearch(autoDownload) {
  const query = byId("searchQuery").value.trim();
  const maxResults = Number(byId("maxResults").value || "50");
  const minScore = Number(byId("minScore").value || "3");
  const button = autoDownload ? byId("searchDownloadButton") : byId("searchButton");
  if (!query) {
    setMission("缺少检索主题", "先输入关键词或让智能体生成计划。", "error");
    return;
  }
  setButtonLoading(button, true, autoDownload ? "下载中..." : "检索中...");
  setText("searchOutput", autoDownload ? "正在检索并下载..." : "正在检索...");
  if (byId("resultList")) byId("resultList").innerHTML = '<p class="empty-state">正在连接开放文献源...</p>';
  if (byId("reportQuery") && !byId("reportQuery").value.trim()) byId("reportQuery").value = query;
  setMission(autoDownload ? "检索并下载中" : "检索中", `主题：${query}，最多 ${maxResults} 条结果。`);
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, max_results: maxResults, min_score: minScore, auto_download: autoDownload }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "检索失败");
    setText("searchOutput", pretty(buildSearchPreview(data)));
    renderResults(data);
    summarizeSearch(data);
    await fetchDownloadQueue().catch(() => undefined);
    setMission("检索完成", `已返回 ${data.result_count ?? 0} 篇候选文献。`);
    showToast("success", "检索完成", `${data.result_count ?? 0} 篇文献`);
  } catch (error) {
    setText("searchOutput", error.message || String(error));
    renderResults(null);
    setMission("检索失败", error.message || String(error), "error");
    showToast("error", "检索失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function runAgentFlow() {
  const searchQuery = byId("searchQuery")?.value?.trim() || "";
  const plan = (latestPlan && latestPlan.query)
    ? latestPlan
    : {
        query: searchQuery,
        max_results: Number(byId("maxResults")?.value || "50"),
        min_score: Number(byId("minScore")?.value || "3"),
      };

  if (!plan.query) {
    setMission("没有计划", "先生成包含 query 的智能体计划，或在上方检索框中输入关键词。", "error");
    return;
  }

  const button = byId("agentRunButton");
  setButtonLoading(button, true, "执行中...");
  setText("searchOutput", "正在检索并执行...");
  if (byId("resultList")) byId("resultList").innerHTML = '<p class="empty-state">智能体正在执行检索计划...</p>';
  setMission("执行计划", `检索：${plan.query}`);
  try {
    const response = await fetch("/api/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patch: latestPatch || {}, plan, apply_patch_first: Object.keys(latestPatch || {}).length > 0 }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.error || "智能体执行失败");
    setText("configOutput", pretty(data.config || {}));
    setText("chatOutput", "已按智能体计划更新配置并执行检索。");
    const preview = {
      applied_patch: data.applied_patch, query: data.query,
      result_count: data.search.result_count, downloaded_count: data.search.downloaded_count,
      queued_count: data.search.queued_count, min_score: data.search.min_score,
      search_run_path: data.search.search_run_path, queue_path: data.search.queue_path,
      top_results: (data.search.results || []).slice(0, 10).map((item) => ({
        title: item.paper.title, year: item.paper.year,
        venue: item.paper.venue || item.paper.journal || item.paper.source_name,
        score: item.relevance.score, tags: item.relevance.tags,
        matched_fields: item.relevance.matched_fields, pdf_url: item.paper.pdf_url || "",
      })),
    };
    setText("searchOutput", pretty(preview));
    renderResults(data.search);
    summarizeSearch(data.search);
    await fetchDownloadQueue().catch(() => undefined);
    setMission("计划完成", `已返回 ${data.search.result_count ?? 0} 篇候选文献。`);
    showToast("success", "计划完成", `${data.search.result_count ?? 0} 篇文献`);
  } catch (error) {
    setText("searchOutput", error.message || String(error));
    renderResults(null);
    setMission("执行失败", error.message || String(error), "error");
    showToast("error", "执行失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function fetchHealth() {
  const response = await fetch("/health");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "服务不可用");
  return data;
}

/* ── Bootstrap ── */

function bindEvents() {
  // Tab switching
  document.querySelectorAll(".workspace-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchWorkspaceTab(btn.dataset.tab));
  });

  // Research
  byId("sendButton").addEventListener("click", sendChat);
  byId("applyButton").addEventListener("click", applyPatch);
  byId("agentRunButton").addEventListener("click", runAgentFlow);
  byId("searchButton").addEventListener("click", () => runSearch(false));
  byId("searchDownloadButton").addEventListener("click", () => runSearch(true));
  byId("modelProvider").addEventListener("change", () => {
    renderProviderKeyStatus(byId("modelProvider").value);
    saveModelSettings({ providerOnly: true }).catch((error) => {
      setMission("模型切换失败", error.message || String(error), "error");
    });
  });
  byId("modelSettingsSave").addEventListener("click", () => {
    saveModelSettings().catch((error) => {
      setMission("模型设置保存失败", error.message || String(error), "error");
      showToast("error", "保存失败", error.message || String(error));
    });
  });

  // Attention
  byId("attentionRunButton").addEventListener("click", runAttentionPipeline);
  byId("attentionRefreshButton").addEventListener("click", () => {
    refreshAttentionJobs().catch((error) => setText("attentionOutput", error.message || String(error)));
  });

  // Reports
  byId("reportGenerateButton").addEventListener("click", generateSurveyReport);
  byId("reportRefreshButton").addEventListener("click", () => {
    refreshSurveyReports().catch((error) => setText("reportOutput", error.message || String(error)));
  });
  const reportList = byId("reportList");
  if (reportList) {
    reportList.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const card = target ? target.closest("[data-report-id]") : null;
      const reportId = card?.dataset?.reportId || "";
      if (!reportId) return;
      loadSurveyReport(reportId).catch((error) => setText("reportOutput", error.message || String(error)));
    });
  }

  // Keyboard shortcuts
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (activeWorkspaceTab === "research") sendChat();
    }
  });
}

fetchHealth()
  .then(() => {
    setMission("服务在线", "本地捕获服务已连接，可以开始编排任务。");
    return Promise.all([
      syncModelSettings().catch(() => undefined),
      fetchDownloadQueue(),
      refreshAttentionJobs().catch(() => undefined),
      refreshSurveyReports().catch(() => undefined),
    ]);
  })
  .catch((error) => {
    setText("configOutput", error.message || String(error));
    setMission("服务异常", error.message || String(error), "error");
  });

bindEvents();
switchWorkspaceTab("research");
