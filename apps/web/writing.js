let currentProject = null;
let currentWorkflow = null;
let currentTemplates = [];
let selectedNegotiationChoice = "";
let selectedNegotiationLabel = "";
let selectedTopicId = "";
let currentPdfPreviewToken = 0;
let citationSelections = {};
let workflowPollTimer = 0;
let workflowEventSource = null;
const _dirtyFields = new Set();
let _manualMode = "";

function byId(id) {
  return document.getElementById(id);
}

function _safeSet(id, value) {
  const el = byId(id);
  if (!el) return;
  if (_dirtyFields.has(id)) return;
  if (el.tagName === "SELECT") {
    /* for <select> elements, try to match the value; fall back to no-op */
    if (Array.from(el.options).some((opt) => opt.value === value)) {
      el.value = value;
    }
    return;
  }
  el.value = value;
}

let _autoSaveTimer = 0;
let _overleafAutoSaveTimer = 0;
function _scheduleAutoSave() {
  if (_autoSaveTimer) window.clearTimeout(_autoSaveTimer);
  _autoSaveTimer = window.setTimeout(() => _autoSaveSection(), 3000);
}

function _scheduleOverleafAutoSave() {
  if (_overleafAutoSaveTimer) window.clearTimeout(_overleafAutoSaveTimer);
  _overleafAutoSaveTimer = window.setTimeout(() => _autoSaveOverleaf(), 3000);
}

async function _autoSaveSection() {
  if (!_dirtyFields.has("sectionEditor")) return;
  if (!currentProject?.project_id || !currentWorkflow?.current_section?.section_id) return;
  const content = byId("sectionEditor")?.value;
  if (content === undefined) return;
  try {
    const data = await api("/api/writing/workflow/section/save", {
      project_id: currentProject.project_id,
      section_id: currentWorkflow.current_section.section_id,
      content,
      prompt: "auto-save",
    });
    _dirtyFields.delete("sectionEditor");
    applyWorkflow(data.workflow);
  } catch (_error) {
    /* silent */
  }
}

async function _autoSaveOverleaf() {
  if (!_dirtyFields.has("overleafEditor")) return;
  if (!overleafCurrentPath || !currentProject?.project_id) return;
  const content = byId("overleafEditor")?.value;
  if (content === undefined) return;
  try {
    await api("/api/writing/project/file/save", {
      project_id: currentProject.project_id,
      path: overleafCurrentPath,
      content,
    });
    _dirtyFields.delete("overleafEditor");
  } catch (_error) {
    /* silent */
  }
}

async function _flushAllEditors() {
  await Promise.all([_autoSaveSection(), _autoSaveOverleaf()]);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/* ── LaTeX syntax highlighting ── */
const _SECTION_CMDS = new Set([
  "part", "chapter", "section", "subsection", "subsubsection",
  "paragraph", "subparagraph",
]);

function _parseLatexArg(text, i, len, open, close) {
  // Parse balanced open..close, respecting backslash-escaped delimiters
  if (i >= len || text[i] !== open) return [i, null];
  let depth = 1; i++;
  let start = i;
  while (i < len && depth > 0) {
    if (text[i] === open && text[i - 1] !== "\\") depth++;
    else if (text[i] === close && text[i - 1] !== "\\") depth--;
    i++;
  }
  return [i, text.slice(start - 1, i)]; // include outer delimiters
}

function highlightLatex(text) {
  let out = "";
  let i = 0;
  const len = text.length;

  while (i < len) {
    // Comment: % to end of line
    if (text[i] === "%") {
      let end = text.indexOf("\n", i);
      if (end === -1) end = len;
      out += `<span class="hl-comment">${escapeHtml(text.slice(i, end))}</span>`;
      i = end;
      continue;
    }

    // Inline math $...$ (skip \$, ensure balanced)
    if (text[i] === "$" && (i === 0 || text[i - 1] !== "\\")) {
      let j = i + 1;
      while (j < len) {
        if (text[j] === "$" && text[j - 1] !== "\\") break;
        j++;
      }
      if (j < len) j++; // include closing $
      else j = i + 1; // unbalanced, just one $
      out += `<span class="hl-math">${escapeHtml(text.slice(i, j))}</span>`;
      i = j;
      continue;
    }

    // LaTeX command: \name *? [...]? {...}?
    if (text[i] === "\\") {
      let start = i;
      i++;
      // command name (letters only)
      while (i < len && /[a-zA-Z@]/.test(text[i])) i++;
      let nameEnd = i;
      let name = text.slice(start + 1, nameEnd);
      // optional star
      if (i < len && text[i] === "*") i++;
      // optional [...]
      let optArg = null;
      if (i < len && text[i] === "[") {
        let res = _parseLatexArg(text, i, len, "[", "]");
        i = res[0]; optArg = res[1];
      }
      // optional {...}
      let manArg = null;
      if (i < len && text[i] === "{") {
        let res = _parseLatexArg(text, i, len, "{", "}");
        i = res[0]; manArg = res[1];
      }

      let cmd = text.slice(start, i);
      // Classify
      if (/^\\(begin|end)\{/.test(cmd)) {
        out += `<span class="hl-env">${escapeHtml(cmd)}</span>`;
      } else if (_SECTION_CMDS.has(name)) {
        out += `<span class="hl-section">${escapeHtml(cmd)}</span>`;
      } else {
        out += `<span class="hl-command">${escapeHtml(cmd)}</span>`;
      }
      continue;
    }

    // Special chars
    if ("&#_^~".includes(text[i])) {
      out += text[i] === "&"
        ? `<span class="hl-special">&amp;</span>`
        : `<span class="hl-special">${escapeHtml(text[i])}</span>`;
      i++;
      continue;
    }

    // Standalone braces (not consumed by command arg parsing)
    if (text[i] === "{" || text[i] === "}") {
      out += `<span class="hl-brace">${escapeHtml(text[i])}</span>`;
      i++;
      continue;
    }

    // Plain text span
    let s = i;
    while (i < len && !/[\\$%{}&#_^~]/.test(text[i])) i++;
    if (i > s) out += escapeHtml(text.slice(s, i));
  }

  return out;
}

function syncHighlight(textareaEl, codeEl) {
  if (!textareaEl || !codeEl) return;
  codeEl.innerHTML = highlightLatex(textareaEl.value || "");
}

function syncHighlightScroll(textareaEl, preEl) {
  if (!textareaEl || !preEl) return;
  preEl.scrollTop = textareaEl.scrollTop;
  preEl.scrollLeft = textareaEl.scrollLeft;
}

let _highlightTimers = {};
function _scheduleHighlight(textareaId, codeId) {
  const key = textareaId;
  if (_highlightTimers[key]) clearTimeout(_highlightTimers[key]);
  _highlightTimers[key] = setTimeout(() => {
    const ta = byId(textareaId);
    const code = byId(codeId);
    syncHighlight(ta, code);
    syncHighlightScroll(ta, code?.parentElement);
  }, 50);
}

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
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "request failed");
  return data;
}

async function getJson(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "request failed");
  return data;
}

function closeWorkflowStream() {
  if (workflowEventSource) {
    workflowEventSource.close();
    workflowEventSource = null;
  }
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

function setMode(mode) {
  const panels = {
    explore: byId("explorePanel"),
    negotiate: byId("negotiatePanel"),
    write: byId("writePanel"),
  };
  const buttons = {
    explore: byId("modeExploreButton"),
    negotiate: byId("modeNegotiateButton"),
    write: byId("modeWriteButton"),
  };
  Object.entries(panels).forEach(([key, panel]) => {
    if (!panel) return;
    panel.classList.toggle("is-hidden", key !== mode);
  });
  Object.entries(buttons).forEach(([key, button]) => {
    if (!button) return;
    button.classList.toggle("is-active", key === mode);
  });
}

function stageToMode(stage) {
  if (stage === "exploration") return "explore";
  if (stage === "outline" || stage === "ordering") return "negotiate";
  return "write";
}

function renderTemplateOptions(items) {
  currentTemplates = Array.isArray(items) ? items : [];
  const select = byId("newProjectTemplate");
  if (!select) return;
  const manualOption = '<option value="">手动上传模板 / 现有项目</option>';
  const options = [manualOption];
  let currentGroup = "";
  [...currentTemplates]
    .sort((a, b) => String(a.group || "").localeCompare(String(b.group || ""), "zh-CN"))
    .forEach((item) => {
      const group = String(item.group || "模板");
      if (group !== currentGroup) {
        if (currentGroup) options.push("</optgroup>");
        options.push(`<optgroup label="${escapeHtml(group)}">`);
        currentGroup = group;
      }
      options.push(`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`);
    });
  if (currentGroup) options.push("</optgroup>");
  select.innerHTML = options.join("");
}

function renderProjects(items) {
  const select = byId("projectSelect");
  if (!select) return;
  const currentId = currentProject?.project_id || "";
  const options = ['<option value="">选择项目…</option>'];
  for (const item of Array.isArray(items) ? items : []) {
    const selected = item.project_id === currentId ? " selected" : "";
    const compileStatus = item.compile?.status || "未编译";
    options.push(
      `<option value="${escapeHtml(item.project_id)}"${selected}>${escapeHtml(`${item.title || item.project_id} | ${compileStatus}`)}</option>`
    );
  }
  select.innerHTML = options.join("");
}

function renderSources(items) {
  const container = byId("chatSourceList");
  if (!container) return;
  if (!Array.isArray(items) || !items.length) {
    container.innerHTML = '<p class="empty-state">还没有上传材料。</p>';
    return;
  }
  container.innerHTML = items.map((item) => `
    <article class="source-card">
      <div class="source-card-header">
        <strong>${escapeHtml(item.name || "Untitled")}</strong>
        <span>${escapeHtml(item.kind || "")}</span>
      </div>
      <p class="source-card-excerpt">${escapeHtml(item.excerpt || "")}</p>
    </article>
  `).join("");
}

function renderWorkspaceSummary(project) {
  const container = byId("workspaceSummary");
  if (!container) return;
  const workspace = project?.workspace || project || {};
  const path = workspace.workspace_path || "";
  if (!path) {
    container.innerHTML = '<p class="empty-state">还没有导入代码工作区。</p>';
    return;
  }
  container.innerHTML = `
    <article class="source-card">
      <div class="source-card-header">
        <strong>${escapeHtml(workspace.workspace_name || "Workspace")}</strong>
        <span>${escapeHtml(String(workspace.workspace_file_count || 0))} files</span>
      </div>
      <p class="source-card-excerpt">${escapeHtml(path)}</p>
      <p class="source-card-excerpt">图片 ${escapeHtml(String(workspace.workspace_figure_count || 0))} 张</p>
    </article>
  `;
}

function syncCompileResult(compile) {
  if (!compile) return;

  /* expand the Overleaf panel if collapsed */
  const olPanel = byId("overleafPanel");
  if (olPanel && olPanel.classList.contains("is-collapsed")) {
    olPanel.classList.remove("is-collapsed");
    const toggleBtn = byId("overleafToggleButton");
    if (toggleBtn) toggleBtn.textContent = "收起";
  }

  /* update the Overleaf PDF preview */
  if (currentProject?.project_id) {
    const olFrame = byId("overleafPdfFrame");
    const olFallback = byId("overleafPreviewFallback");
    if (olFrame && olFallback) {
      const url = buildPdfPreviewUrl(currentProject.project_id);
      olFrame.src = url;
      olFrame.style.display = "";
      olFallback.style.display = "none";
    }
  }
}

function buildPdfPreviewUrl(projectId) {
  return `/api/writing/project/pdf?id=${encodeURIComponent(projectId)}&t=${Date.now()}`;
}

function syncPreview(project) {
  const frame = byId("pdfPreview");
  const fallback = byId("previewFallback");
  const link = byId("chatPdfLink");
  const pdfPath = project?.paths?.pdf || project?.compile?.pdf_path || "";
  if (!frame || !fallback) return;
  if (!project?.project_id || !pdfPath) {
    frame.src = "about:blank";
    fallback.classList.remove("is-hidden");
    if (link) link.href = "#";
    return;
  }
  const token = ++currentPdfPreviewToken;
  fallback.classList.add("is-hidden");
  const url = buildPdfPreviewUrl(project.project_id);
  frame.onload = () => {
    if (token !== currentPdfPreviewToken) return;
    fallback.classList.add("is-hidden");
  };
  frame.onerror = () => {
    if (token !== currentPdfPreviewToken) return;
    fallback.classList.remove("is-hidden");
  };
  frame.src = url;
  if (link) link.href = url;

  /* sync Overleaf panel preview too */
  const olFrame = byId("overleafPdfFrame");
  const olFallback = byId("overleafPreviewFallback");
  if (olFrame && olFallback && pdfPath) {
    olFrame.src = url;
    olFrame.style.display = "";
    olFallback.style.display = "none";
  }
}

function renderProjectHeader(project, workflow) {
  byId("studioProjectTitle").textContent = project?.title || "Writing Studio";
  byId("studioProjectMeta").textContent = project
    ? `${project.template_name || "手动导入项目"} / ${project.writing_type || ""} / ${project.writing_language || ""}`
    : "先创建或选择一个项目。";
  _safeSet("chatTitle", project?.title || "");
  _safeSet("writingType", project?.writing_type || "academic");
  _safeSet("explorationTopicInput", workflow?.exploration?.topic || project?.goal || "");
  byId("workflowStageBadge").textContent = `阶段：${workflow?.stage || "--"}`;
  if (!project) _safeSet("guardrailsYamlEditor", "");
}

function stageStatusLabel(status) {
  const map = {
    pending: "待处理",
    negotiating: "协商中",
    writing: "写作中",
    locked: "已锁定",
    unlocked: "已解锁",
    needs_review: "需复核",
  };
  return map[status] || status;
}

function renderStageCard(card) {
  const node = byId("stage1Card");
  if (!node) return;
  if (!card) {
    node.className = "writing-stage-card is-empty";
    node.innerHTML = `
      <div class="writing-stage-card-head">
        <span class="writing-stage-icon">🔍</span>
        <div><strong>STAGE 1 勘探与选题</strong><p>选择项目后开始勘探。</p></div>
      </div>
    `;
    return;
  }
  node.className = `writing-stage-card status-${escapeHtml(card.status || "pending")}`;
  node.innerHTML = `
    <div class="writing-stage-card-head">
      <span class="writing-stage-icon">${card.completed ? "✅" : "🔍"}</span>
      <div>
        <strong>${escapeHtml(card.title || "STAGE 1 勘探与选题")}</strong>
        <p>${escapeHtml(card.summary || "")}</p>
      </div>
    </div>
  `;
}

function sectionProgressLevel(summary) {
  const len = String(summary || "").length;
  if (len > 200) return "long";
  if (len > 60) return "medium";
  return "short";
}

function sectionProgressPercent(summary) {
  const len = String(summary || "").length;
  return Math.min(100, Math.round((len / 300) * 100));
}

function renderSectionCards(items) {
  const container = byId("sectionCardList");
  if (!container) return;
  if (!Array.isArray(items) || !items.length) {
    container.innerHTML = `
      <article class="writing-section-card is-empty">
        <strong>还没有检测到章节</strong>
        <p>导入 LaTeX 模板后点击「分析章节」，让 AI 自动识别章节结构。</p>
      </article>
    `;
    return;
  }
  container.innerHTML = items.map((item) => {
    const progressLvl = sectionProgressLevel(item.summary || "");
    const progressPct = sectionProgressPercent(item.summary || "");
    const statusIcon = item.status === "locked" ? " &#128274;" : item.status === "writing" ? " &#9997;" : "";
    return `
    <button class="writing-section-card status-${escapeHtml(item.status || "pending")}${item.active ? " is-active" : ""}" data-section-id="${escapeHtml(item.id)}">
      <div class="writing-section-card-top">
        <span class="writing-section-order">${escapeHtml(String(item.write_order || item.sort_order || ""))}</span>
        <span class="writing-section-state">${escapeHtml(stageStatusLabel(item.status || ""))}${statusIcon}</span>
      </div>
      <strong>${escapeHtml(item.title || "")}</strong>
      <p>${escapeHtml(item.summary || item.focus || item.path || "等待写作")}</p>
      <div class="writing-section-progress">
        <div class="writing-section-progress-bar is-${progressLvl}" style="width:${progressPct}%"></div>
      </div>
    </button>
  `}).join("");
  container.querySelectorAll("[data-section-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const sectionId = button.dataset.sectionId || "";
      if (!currentProject || !sectionId) return;
      try {
        const data = await api("/api/writing/workflow/section/start", {
          project_id: currentProject.project_id,
          section_id: sectionId,
        });
        applyWorkflow(data.workflow);
      } catch (error) {
        showToast("error", "切换章节失败", error.message);
      }
    });
  });
}

function renderExploration(workflow) {
  const coverage = workflow?.exploration?.coverage || [];
  const insights = workflow?.exploration?.insights || [];
  const suggestions = workflow?.exploration?.suggestions || [];
  const coverageNode = byId("explorationCoverage");
  const insightsNode = byId("explorationInsights");
  const listNode = byId("explorationSuggestionList");
  if (coverageNode) {
    coverageNode.innerHTML = coverage.length
      ? coverage.map((item) => `<span class="badge">${escapeHtml(`${item.label}: ${item.count}`)}</span>`).join("")
      : '<span class="badge">等待生成覆盖方向</span>';
  }
  if (insightsNode) {
    insightsNode.innerHTML = insights.length
      ? insights.map((item) => `<article class="insight-item insight-item-info"><strong>洞察</strong><p>${escapeHtml(item)}</p></article>`).join("")
      : `<article class="insight-item insight-item-info"><strong>操作提示</strong><p>生成勘探报告后确认选题方向。</p></article>`;
  }
  if (listNode) {
    listNode.innerHTML = suggestions.length
      ? suggestions.map((item) => `
        <button class="writing-choice-card${selectedTopicId === item.id ? " is-selected" : ""}" data-topic-id="${escapeHtml(item.id)}" data-topic-title="${escapeHtml(item.title || "")}">
          <div class="writing-choice-head">
            <strong>${escapeHtml(item.label || "")}</strong>
            <span class="badge">${escapeHtml(item.strength || "")}</span>
          </div>
          <p>${escapeHtml(item.title || "")}</p>
          <small>${escapeHtml(item.fit || "")}</small>
        </button>
      `).join("")
      : `<article class="writing-choice-card is-empty"><strong>还没有选题建议</strong><p>生成勘探报告后显示选题方向。</p></article>`;
    listNode.querySelectorAll("[data-topic-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedTopicId = button.dataset.topicId || "";
        _dirtyFields.delete("explorationCustomTopicInput");
        _safeSet("explorationCustomTopicInput", button.dataset.topicTitle || "");
        renderExploration(currentWorkflow);
      });
    });
  }
}

function renderNegotiation(section) {
  byId("negotiationSectionTitle").textContent = section?.title || "章节协商";
  byId("negotiationGuideText").textContent = section?.writing_guide || "等待选择章节。";
  const requirementNode = byId("negotiationRequiredList");
  if (requirementNode) {
    const elements = Array.isArray(section?.required_elements) ? section.required_elements : [];
    requirementNode.innerHTML = elements.length
      ? elements.map((item) => `<div class="writing-requirement-item">${escapeHtml(item)}</div>`).join("")
      : '<div class="writing-requirement-item">当前章节没有额外必写项。</div>';
  }
  const choiceNode = byId("negotiationChoiceList");
  const options = Array.isArray(section?.options) ? section.options : [];
  if (choiceNode) {
    choiceNode.innerHTML = options.length
      ? options.map((item) => `
        <button class="writing-choice-card${selectedNegotiationChoice === item.id ? " is-selected" : ""}" data-choice-id="${escapeHtml(item.id)}" data-choice-label="${escapeHtml(item.label)}">
          <div class="writing-choice-head">
            <strong>${escapeHtml(item.label || item.id || "")}</strong>
          </div>
          <p>${escapeHtml(item.description || "")}</p>
        </button>
      `).join("")
      : '<article class="writing-choice-card is-empty"><strong>当前章节没有预设策略</strong><p>可以直接在补充要求里写你的想法。</p></article>';
    choiceNode.querySelectorAll("[data-choice-id]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedNegotiationChoice = button.dataset.choiceId || "";
        selectedNegotiationLabel = button.dataset.choiceLabel || "";
        renderNegotiation(section);
      });
    });
  }
  if (!selectedNegotiationChoice && section?.strategy_id) {
    selectedNegotiationChoice = section.strategy_id;
    selectedNegotiationLabel = section.strategy_label || "";
  }
  _safeSet("negotiationCustomNote", section?.custom_note || "");
}

function renderOrderRecommendation(workflow) {
  const list = workflow?.order_recommendation?.sections || [];
  const container = byId("writingOrderList");
  if (!container) return;
  if (!list.length) {
    container.innerHTML = '<article class="writing-order-item is-empty"><strong>等待协商完成</strong><p>协商完成后给出推荐顺序。</p></article>';
    return;
  }
  container.innerHTML = list.map((item, index) => `
    <label class="writing-order-item" draggable="true" data-order-item="${escapeHtml(item.section_id)}">
      <span class="writing-order-handle">${index + 1}</span>
      <input type="hidden" value="${escapeHtml(item.section_id)}">
      <strong>${escapeHtml(item.title || "")}</strong>
      <button class="btn btn-ghost btn-sm" data-order-move="up" data-order-id="${escapeHtml(item.section_id)}">上移</button>
      <button class="btn btn-ghost btn-sm" data-order-move="down" data-order-id="${escapeHtml(item.section_id)}">下移</button>
    </label>
  `).join("");
  container.querySelectorAll("[data-order-move]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.orderId || "";
      const move = button.dataset.orderMove || "";
      const items = [...container.querySelectorAll(".writing-order-item")];
      const index = items.findIndex((item) => item.querySelector("input")?.value === id);
      if (index < 0) return;
      const target = move === "up" ? index - 1 : index + 1;
      if (target < 0 || target >= items.length) return;
      const current = items[index];
      const swap = items[target];
      if (move === "up") {
        container.insertBefore(current, swap);
      } else {
        container.insertBefore(swap, current);
      }
    });
  });
  let dragId = "";
  container.querySelectorAll("[data-order-item]").forEach((item) => {
    item.addEventListener("dragstart", () => {
      dragId = item.dataset.orderItem || "";
      item.classList.add("is-dragging");
    });
    item.addEventListener("dragend", () => {
      dragId = "";
      item.classList.remove("is-dragging");
    });
    item.addEventListener("dragover", (event) => {
      event.preventDefault();
    });
    item.addEventListener("drop", (event) => {
      event.preventDefault();
      const targetId = item.dataset.orderItem || "";
      if (!dragId || !targetId || dragId === targetId) return;
      const dragged = container.querySelector(`[data-order-item="${CSS.escape(dragId)}"]`);
      const target = container.querySelector(`[data-order-item="${CSS.escape(targetId)}"]`);
      if (!dragged || !target) return;
      container.insertBefore(dragged, target);
    });
  });
}

function renderCurrentSection(section) {
  const isLocked = section?.status === "locked" || section?.locked === true;
  byId("currentSectionTitle").textContent = section?.title || "选择项目开始写作";
  const metaBits = [];
  if (section?.path) metaBits.push(section.path);
  if (section?.status) metaBits.push(stageStatusLabel(section.status));
  if (section?.strategy_label) metaBits.push(section.strategy_label);
  byId("currentSectionMeta").textContent = metaBits.join(" / ") || "系统会根据阶段切换勘探、协商和写作模式。";
  byId("editorSectionTitle").textContent = (section?.title || "章节源码") + (isLocked ? " [已锁定]" : "");
  byId("editorSectionPath").textContent = (section?.path || "选择章节后在这里编辑源码。") + (isLocked ? " — 点击下方「解锁本章」后可编辑" : "");
  _safeSet("sectionEditor", section?.content || "");
  syncHighlight(byId("sectionEditor"), byId("sectionHighlightCode"));
  _safeSet("sectionPrompt", "");
  byId("figureHintText").textContent = section?.requires_figures
    ? "该章节适合插入技术路线图、实验结果图或流程图。"
    : "当前章节暂无图表特殊要求。";

  /* Locked sections: make editor read-only, change lock button to unlock */
  const editor = byId("sectionEditor");
  const lockBtn = byId("lockSectionButton");
  if (editor) editor.readOnly = isLocked;
  if (lockBtn) {
    lockBtn.textContent = isLocked ? "解锁本章" : "锁定本章";
  }
}

function renderEvidence(section) {
  const container = byId("evidenceList");
  if (!container) return;
  const items = Array.isArray(section?.evidence_cards) ? section.evidence_cards : [];
  if (!items.length) {
    container.innerHTML = '<article class="writing-evidence-card is-empty"><strong>当前章节还没有证据卡</strong><p>加载章节上下文后显示证据。</p></article>';
    return;
  }
  container.innerHTML = items.map((item) => `
    <article class="writing-evidence-card">
      <div class="writing-evidence-head">
        <strong>${escapeHtml(item.title || item.key || "Untitled")}</strong>
        <span class="badge">${escapeHtml(item.strength ? `★${item.strength}` : "证据")}</span>
      </div>
      <p>${escapeHtml(item.claim || "")}</p>
      <small>${escapeHtml(item.key || "")}${item.approved ? " / 已确认" : ""}</small>
    </article>
  `).join("");
}

function renderCitationReview(section) {
  const container = byId("citationReviewList");
  if (!container) return;
  const items = Array.isArray(section?.pending_citations) ? section.pending_citations : [];
  if (!items.length) {
    citationSelections = {};
    container.innerHTML = '<article class="writing-evidence-card is-empty"><strong>当前没有待审核引用</strong><p>出现 `[待引用:N]` 占位符时显示候选文献。</p></article>';
    return;
  }
  const nextSelections = {};
  container.innerHTML = items.map((item) => {
    const placeholder = item.placeholder || "";
    const approved = citationSelections[placeholder] || [];
    const candidates = Array.isArray(item.candidates) ? item.candidates : [];
    nextSelections[placeholder] = approved.filter((key) => candidates.some((candidate) => candidate.bib_key === key));
    return `
      <article class="writing-evidence-card writing-citation-card">
        <div class="writing-evidence-head">
          <strong>${escapeHtml(placeholder)}</strong>
          <span class="badge">${escapeHtml(candidates.length ? `${candidates.length} 个候选` : "待补充")}</span>
        </div>
        <p>${escapeHtml(item.claim || item.sentence || "")}</p>
        <div class="writing-citation-candidates">
          ${candidates.length ? candidates.map((candidate) => `
            <label class="writing-citation-option">
              <input
                type="checkbox"
                data-citation-placeholder="${escapeHtml(placeholder)}"
                data-citation-key="${escapeHtml(candidate.bib_key || "")}"
                ${nextSelections[placeholder].includes(candidate.bib_key) ? "checked" : ""}
              >
              <span>
                <strong>${escapeHtml(candidate.title || candidate.bib_key || "Untitled")}</strong>
                <small>${escapeHtml(`★${candidate.strength || 0} / ${candidate.bib_key || ""}`)}</small>
                <small>${escapeHtml(candidate.strength_reason || "")}</small>
              </span>
            </label>
          `).join("") : '<div class="writing-requirement-item">还没有找到满足强度要求的候选文献。</div>'}
        </div>
      </article>
    `;
  }).join("");
  citationSelections = nextSelections;
  container.querySelectorAll("[data-citation-placeholder]").forEach((input) => {
    input.addEventListener("change", () => {
      const placeholder = input.dataset.citationPlaceholder || "";
      const key = input.dataset.citationKey || "";
      if (!placeholder || !key) return;
      const current = new Set(citationSelections[placeholder] || []);
      if (input.checked) current.add(key);
      else current.delete(key);
      citationSelections[placeholder] = [...current];
    });
  });
}

function renderGuardrailViolations(section) {
  const container = byId("guardrailViolationList");
  if (!container) return;
  const items = Array.isArray(section?.guardrails?.violations) ? section.guardrails.violations : [];
  if (!items.length) {
    container.innerHTML = '<article class="insight-item insight-item-info"><strong>暂无护栏警告</strong><p>保存或生成后列出违规项。</p></article>';
    return;
  }
  container.innerHTML = items.map((item) => `
    <article class="insight-item insight-item-warn">
      <strong>${escapeHtml(item.code || "guardrail")}</strong>
      <p>${escapeHtml(item.message || "")}</p>
    </article>
  `).join("");
}

function renderGuardrails(project, workflow, section) {
  const checklist = byId("guardrailChecklist");
  if (!checklist) return;
  const compile = project?.compile || {};
  const guardrails = section?.guardrails || {};
  const items = [
    {
      ok: Boolean(project?.template_profile?.document_class?.name) || Boolean(guardrails.section_id),
      text: guardrails.section_id ? "章节护栏已加载" : "模板结构已识别",
    },
    {
      ok: section?.status === "locked" || section?.status === "writing" || section?.status === "negotiating",
      text: "当前章节已纳入工作流状态机",
    },
    {
      ok: compile.status === "compiled",
      text: compile.status ? `编译状态：${compile.status}` : "还没有编译结果",
    },
    {
      ok: (workflow?.counts?.citation_pending || 0) === 0,
      text: `待审核引用：${workflow?.counts?.citation_pending || 0}`,
    },
    {
      ok: !(section?.cross_chapter_hints || []).length,
      text: (section?.cross_chapter_hints || []).length ? `跨章引用提示：${section.cross_chapter_hints.length}` : "暂无跨章引用提示",
    },
  ];
  checklist.innerHTML = items.map((item) => `
    <div class="writing-check-item">
      <span class="status-dot ${item.ok ? "status-dot-ok" : "status-dot-warn"}"></span>
      <strong>${escapeHtml(item.text)}</strong>
    </div>
  `).join("");
  const promptNode = byId("guardrailPromptText");
  if (promptNode) {
    promptNode.textContent = section?.guardrails?.prompt || "等待项目加载...";
  }
}

function renderStatusBar(workflow) {
  byId("statusBarText").innerHTML = `<strong>${escapeHtml(workflow?.status_bar || "未加载项目")}</strong>`;
  byId("statusLockedCount").textContent = `已锁定 ${workflow?.counts?.locked || 0}`;
  byId("statusReviewCount").textContent = `待复核 ${workflow?.counts?.needs_review || 0}`;
  byId("statusCitationCount").textContent = `待审核引用 ${workflow?.counts?.citation_pending || 0}`;
}

function renderSectionAudit(issues) {
  const container = byId("sectionAuditList");
  if (!container) return;
  if (!Array.isArray(issues) || !issues.length) {
    container.innerHTML = '<article class="insight-item insight-item-info"><strong>写作提示</strong><p>可手动编辑，或用 AI 续写、重写。</p></article>';
    return;
  }
  container.innerHTML = issues.map((item) => `
    <article class="insight-item ${item.severity === "error" ? "insight-item-warn" : "insight-item-info"}">
      <strong>${escapeHtml(item.category || item.mode || "审计提示")}</strong>
      <p>${escapeHtml(item.description || "")}</p>
    </article>
  `).join("");
}

function applyWorkflow(workflow) {
  const prevStage = currentWorkflow?.stage;
  currentWorkflow = workflow || null;
  if (!workflow) return;

  /* If workflow stage actually changed (not just a poll refresh), release manual mode */
  if (prevStage && workflow.stage !== prevStage) {
    _manualMode = "";
  }

  const autoMode = stageToMode(workflow.stage);
  renderProjectHeader(currentProject, workflow);
  renderStageCard(workflow.stage_card);
  renderSectionCards(workflow.sections);
  renderExploration(workflow);
  renderNegotiation(workflow.current_section);
  renderOrderRecommendation(workflow);
  renderCurrentSection(workflow.current_section);
  renderEvidence(workflow.current_section);
  renderCitationReview(workflow.current_section);
  renderGuardrails(currentProject, workflow, workflow.current_section);
  renderGuardrailViolations(workflow.current_section);
  renderStatusBar(workflow);
  byId("sectionReply").textContent = "等待生成...";
  byId("lockSectionButton").disabled = !workflow.current_section?.section_id;
  if (!_manualMode) {
    setMode(autoMode);
  }
}

async function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const marker = "base64,";
      resolve(result.includes(marker) ? result.split(marker, 2)[1] : result);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function collectUploads(inputId) {
  const files = Array.from(byId(inputId).files || []);
  const payload = [];
  for (const file of files) {
    payload.push({
      name: file.name,
      path: file.webkitRelativePath || file.name,
      content_type: file.type,
      content_base64: await readFileAsBase64(file),
    });
  }
  return payload;
}

async function refreshTemplates() {
  const data = await getJson("/api/templates");
  renderTemplateOptions(data.items || []);
}

async function refreshProjects(selectLatest = false) {
  const data = await getJson("/api/writing/projects");
  renderProjects(data.items || []);
  if (selectLatest && data.items?.length) {
    await loadProject(data.items[0].project_id);
  }
}

async function loadWorkflow(projectId) {
  const data = await getJson(`/api/writing/workflow?project_id=${encodeURIComponent(projectId)}`);
  applyWorkflow(data.workflow);
}

async function loadGuardrails(projectId) {
  const data = await getJson(`/api/writing/project/guardrails?project_id=${encodeURIComponent(projectId)}`);
  _safeSet("guardrailsYamlEditor", data.yaml_text || "");
}

async function loadProject(projectId) {
  _dirtyFields.clear();
  _manualMode = "";
  if (!projectId) {
    closeWorkflowStream();
    currentProject = null;
    currentWorkflow = null;
    renderProjectHeader(null, null);
    renderSectionCards([]);
    syncPreview(null);
    renderSources([]);
    renderWorkspaceSummary(null);
    return;
  }
  _collapsedDirs = {};
  overleafCurrentPath = "";
  const data = await getJson(`/api/writing/project?id=${encodeURIComponent(projectId)}`);
  currentProject = data.project || null;
  renderProjectHeader(currentProject, currentWorkflow);
  syncPreview(currentProject);
  const context = await getJson(`/api/writing/project/context?project_id=${encodeURIComponent(projectId)}`);
  renderSources(context.context?.source_files || []);
  renderWorkspaceSummary(currentProject?.workspace || currentProject);
  await loadGuardrails(projectId);
  await loadWorkflow(projectId);
  await refreshOverleafFileList();
  connectWorkflowStream(projectId);
}

function createNewProject() {
  openNewProjectModal();
}

async function saveProjectMeta() {
  if (!currentProject) return;
  const button = byId("saveProjectMetaButton");
  setButtonLoading(button, true, "保存中...");
  try {
    const data = await api("/api/writing/project/meta", {
      project_id: currentProject.project_id,
      title: byId("chatTitle").value.trim(),
      writing_type: byId("writingType").value,
    });
    currentProject = data.project;
    await refreshProjects();
    await loadProject(currentProject.project_id);
    showToast("success", "已保存", "项目信息已更新。");
  } catch (error) {
    showToast("error", "保存失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

/* ═══════════════════════════════════════════════════════════════════════
   Overleaf Panel — source editor + PDF preview
   ═══════════════════════════════════════════════════════════════════════ */

let overleafCurrentPath = "";
let _collapsedDirs = {};

function buildFileTree(rawFiles) {
  const root = { name: "", children: [], isDir: true };
  const sorted = [...rawFiles].sort();
  for (const filePath of sorted) {
    const clean = filePath.replace(/^project_files\//, "");
    const parts = clean.split("/");
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const name = parts[i];
      const isDir = i < parts.length - 1;
      let child = node.children.find((c) => c.name === name && c.isDir === isDir);
      if (!child) {
        child = { name, isDir, path: isDir ? parts.slice(0, i + 1).join("/") : clean, children: [] };
        node.children.push(child);
      }
      node = child;
    }
  }
  return root;
}

function renderFileTreeNode(node, depth) {
  if (node.isDir) {
    const key = node.path || node.name;
    const collapsed = _collapsedDirs[key] || false;
    const hasChildren = node.children.length > 0;
    return `
      <div class="tree-folder${collapsed ? " is-collapsed" : ""}" data-dir-path="${escapeHtml(key)}">
        <button class="tree-folder-toggle" aria-label="${collapsed ? "展开" : "折叠"}">
          <span class="tree-arrow">${collapsed ? "&#9654;" : "&#9660;"}</span>
          <span class="tree-name">${escapeHtml(node.name)}</span>
        </button>
        <div class="tree-children" style="padding-left:${depth === 0 ? 0 : 14}px">
          ${node.children.map((c) => renderFileTreeNode(c, depth + 1)).join("")}
        </div>
      </div>`;
  }
  return `
    <button class="tree-file${node.path === overleafCurrentPath ? " is-active" : ""}"
            data-file-path="${escapeHtml(node.path)}"
            style="padding-left:${14 + depth * 14}px">
      ${escapeHtml(node.name)}
    </button>`;
}

async function refreshOverleafFileList() {
  const container = byId("overleafFileItems");
  if (!container || !currentProject?.project_id) return;

  const rawFiles = currentProject?.files || [];
  if (!rawFiles.length) {
    container.innerHTML = '<p class="empty-state">没有文件。</p>';
    return;
  }

  const tree = buildFileTree(rawFiles);

  /* root level: merge single-folder root into children */
  const topNodes = tree.children;
  if (topNodes.length === 1 && topNodes[0].isDir && topNodes[0].children.length) {
    const sole = topNodes[0];
    _collapsedDirs[sole.path] = false;
    container.innerHTML = `
      <div class="tree-folder" data-dir-path="${escapeHtml(sole.path)}">
        <button class="tree-folder-toggle">
          <span class="tree-arrow">&#9660;</span>
          <span class="tree-name">${escapeHtml(sole.name)}</span>
        </button>
        <div class="tree-children">
          ${sole.children.map((c) => renderFileTreeNode(c, 1)).join("")}
        </div>
      </div>`;
  } else {
    container.innerHTML = topNodes.map((c) => renderFileTreeNode(c, 0)).join("");
  }

  /* click: folder toggle */
  container.querySelectorAll(".tree-folder-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const folder = btn.closest(".tree-folder");
      if (!folder) return;
      const key = folder.dataset.dirPath || "";
      _collapsedDirs[key] = !_collapsedDirs[key];
      refreshOverleafFileList();
    });
  });

  /* click: file load */
  container.querySelectorAll(".tree-file").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const path = btn.dataset.filePath || "";
      if (!path) return;
      await loadOverleafFile(path);
      container.querySelectorAll(".tree-file").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
    });
  });

  /* auto-load first tex file if none selected */
  if (!overleafCurrentPath && rawFiles.length > 0) {
    const texFiles = rawFiles.filter((f) => f.toLowerCase().endsWith(".tex"));
    const main = texFiles.find((f) => f.toLowerCase().includes("main")) || texFiles[0];
    const first = main || rawFiles[0];
    await loadOverleafFile(first);
    const activeBtn = container.querySelector(`[data-file-path="${CSS.escape(first)}"]`);
    if (activeBtn) activeBtn.classList.add("is-active");
  }
}

async function loadOverleafFile(path) {
  if (!path || !currentProject?.project_id) return;
  overleafCurrentPath = path;
  const editor = byId("overleafEditor");
  _dirtyFields.delete("overleafEditor");
  editor.value = "加载中...";
  try {
    const data = await getJson(`/api/writing/project/file?project_id=${encodeURIComponent(currentProject.project_id)}&path=${encodeURIComponent(path)}`);
    const file = data.file || {};
    _safeSet("overleafEditor", file.content || file.text || "");
    syncHighlight(byId("overleafEditor"), byId("overleafHighlightCode"));
  } catch (error) {
    _safeSet("overleafEditor", `%% 加载失败: ${error.message}`);
    syncHighlight(byId("overleafEditor"), byId("overleafHighlightCode"));
  }
}

async function saveOverleafFile() {
  if (!overleafCurrentPath || !currentProject) return;
  try {
    const payload = {
      project_id: currentProject.project_id,
      path: overleafCurrentPath,
      content: byId("overleafEditor").value,
    };
    await api("/api/writing/project/file/save", payload);
    _dirtyFields.delete("overleafEditor");
    showToast("success", "已保存", overleafCurrentPath);
  } catch (error) {
    showToast("error", "保存失败", error.message);
  }
}

async function overleafCompile() {
  if (!currentProject) return;
  const button = byId("overleafCompileButton");
  setButtonLoading(button, true, "编译中...");
  try {
    await _flushAllEditors();
    const data = await api("/api/writing/project/compile", {
      project_id: currentProject.project_id,
    });
    currentProject = { ...currentProject, compile: data.compile };
    syncCompileResult(data.compile);
    syncPreview(currentProject);
    const c = data.compile || {};
    showToast(c.returncode === 0 ? "success" : "error", "编译完成", c.status || "");
  } catch (error) {
    showToast("error", "编译失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function toggleOverleafPanel() {
  const panel = byId("overleafPanel");
  const button = byId("overleafToggleButton");
  panel.classList.toggle("is-collapsed");
  button.textContent = panel.classList.contains("is-collapsed") ? "展开" : "收起";
}

function initOverleafResizer() {
  let dragging = null;

  function onMouseDown(event) {
    event.preventDefault();
    dragging = event.target;
    dragging.classList.add("is-active");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  function onMouseMove(event) {
    if (!dragging) return;
    const split = byId("overleafSplit");
    if (!split) return;
    const rect = split.getBoundingClientRect();

    if (dragging.id === "overleafResizerLeft") {
      const filelist = split.querySelector(".overleaf-panel-filelist");
      if (!filelist) return;
      const newWidth = event.clientX - rect.left;
      filelist.style.width = Math.min(400, Math.max(120, newWidth)) + "px";
    } else {
      const pct = ((event.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.min(80, Math.max(20, pct));
      const editor = split.querySelector(".overleaf-panel-editor");
      const preview = split.querySelector(".overleaf-panel-preview");
      if (editor) editor.style.flex = `${clamped} 1 0`;
      if (preview) preview.style.flex = `${100 - clamped} 1 0`;
    }
  }

  function onMouseUp() {
    if (!dragging) return;
    dragging.classList.remove("is-active");
    dragging = null;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  }

  const leftResizer = byId("overleafResizerLeft");
  const rightResizer = byId("overleafResizer");
  if (leftResizer) leftResizer.addEventListener("mousedown", onMouseDown);
  if (rightResizer) rightResizer.addEventListener("mousedown", onMouseDown);
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);
}

async function deleteCurrentProject() {
  if (!currentProject) return;
  if (!window.confirm(`确认删除项目 “${currentProject.title || currentProject.project_id}”？`)) return;
  const button = byId("deleteProjectButton");
  setButtonLoading(button, true, "删除中...");
  try {
    await api("/api/writing/project/delete", { project_id: currentProject.project_id });
    currentProject = null;
    currentWorkflow = null;
    await refreshProjects();
    await loadProject("");
    showToast("success", "项目已删除", "项目及其工作区数据已移除。");
  } catch (error) {
    showToast("error", "删除失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function importProject(replaceProject) {
  if (!currentProject) {
    showToast("error", "缺少项目", "请先创建项目。");
    return;
  }
  const files = await collectUploads("projectImportInput");
  if (!files.length) {
    showToast("error", "没有文件", "请选择要导入的项目文件。");
    return;
  }
  const button = replaceProject ? byId("replaceProjectButton") : byId("importProjectButton");
  setButtonLoading(button, true, replaceProject ? "覆盖中..." : "导入中...");
  try {
    const payload = {
      project_id: currentProject.project_id,
      replace_project: replaceProject,
    };
    if (files.length === 1 && String(files[0].name || "").toLowerCase().endsWith(".zip")) {
      payload.archive = { name: files[0].name, content_base64: files[0].content_base64 };
    } else {
      payload.files = files;
    }
    await api("/api/writing/project/import", payload);
    byId("projectImportInput").value = "";
    await loadProject(currentProject.project_id);
    showToast("success", replaceProject ? "覆盖导入完成" : "导入完成", "项目源码已写入。");
  } catch (error) {
    showToast("error", "导入失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function uploadSources() {
  if (!currentProject) {
    showToast("error", "缺少项目", "请先创建或选择项目。");
    return;
  }
  const files = await collectUploads("chatFileInput");
  if (!files.length) return;
  const button = byId("openSettingsButton");
  setButtonLoading(button, true, "上传材料...");
  try {
    await api("/api/writing/project/sources", {
      project_id: currentProject.project_id,
      files,
    });
    const context = await getJson(`/api/writing/project/context?project_id=${encodeURIComponent(currentProject.project_id)}`);
    renderSources(context.context?.source_files || []);
    showToast("success", "材料已上传", `新增 ${files.length} 份材料。`);
  } catch (error) {
    showToast("error", "上传失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function importWorkspace() {
  if (!currentProject) {
    showToast("error", "缺少项目", "请先创建或选择项目。");
    return;
  }
  const workspacePath = byId("workspacePathInput").value.trim();
  if (!workspacePath) {
    showToast("error", "缺少路径", "请输入本地代码工作区路径。");
    return;
  }
  const button = byId("importWorkspaceButton");
  setButtonLoading(button, true, "导入中...");
  try {
    const data = await api("/api/writing/project/workspace/import", {
      project_id: currentProject.project_id,
      workspace_path: workspacePath,
    });
    currentProject = data.project || currentProject;
    renderWorkspaceSummary(currentProject.workspace || currentProject);
    await loadWorkflow(currentProject.project_id);
    showToast("success", "工作区已导入", workspacePath);
  } catch (error) {
    showToast("error", "导入失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function runExploration() {
  if (!currentProject) {
    showToast("error", "缺少项目", "请先创建或选择项目。");
    return;
  }
  const button = byId("explorationSubmitButton");
  const topic = byId("explorationTopicInput").value.trim() || currentProject.goal || currentProject.title || "";
  if (!topic) {
    showToast("error", "缺少主题词", "请输入一个勘探主题。");
    return;
  }
  setButtonLoading(button, true, "勘探中...");
  try {
    const data = await api("/api/writing/workflow/exploration", {
      project_id: currentProject.project_id,
      topic,
    });
    selectedTopicId = "";
    currentWorkflow = data.workflow;
    applyWorkflow(currentWorkflow);
    showToast("success", "勘探完成", "已生成选题建议。");
  } catch (error) {
    showToast("error", "勘探失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function confirmTopic() {
  if (!currentProject) return;
  const topic = byId("explorationCustomTopicInput").value.trim() || currentWorkflow?.exploration?.selected_topic || currentWorkflow?.exploration?.topic || "";
  if (!topic) {
    showToast("error", "缺少选题", "请先从建议中选择，或输入自定义选题。");
    return;
  }
  const button = byId("confirmTopicButton");
  setButtonLoading(button, true, "确认中...");
  try {
    const data = await api("/api/writing/workflow/exploration/select", {
      project_id: currentProject.project_id,
      selected_topic: topic,
      selection_id: selectedTopicId,
    });
    applyWorkflow(data.workflow);
    showToast("success", "选题已确认", topic);
  } catch (error) {
    showToast("error", "确认失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function confirmNegotiation() {
  if (!currentProject || !currentWorkflow?.current_section?.section_id) return;
  const button = byId("confirmNegotiationButton");
  const customNote = byId("negotiationCustomNote").value.trim();
  if (!selectedNegotiationChoice && !customNote) {
    showToast("error", "缺少策略", "请至少选择一个协商策略，或填写补充要求。");
    return;
  }
  setButtonLoading(button, true, "提交中...");
  try {
    const data = await api("/api/writing/workflow/outline/confirm", {
      project_id: currentProject.project_id,
      section_id: currentWorkflow.current_section.section_id,
      choice: selectedNegotiationChoice,
      strategy_label: selectedNegotiationLabel,
      custom_note: customNote,
    });
    selectedNegotiationChoice = "";
    selectedNegotiationLabel = "";
    applyWorkflow(data.workflow);
    showToast("success", "本章策略已确认", currentWorkflow?.current_section?.title || "章节");
  } catch (error) {
    showToast("error", "提交失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function currentOrderIds() {
  return [...byId("writingOrderList").querySelectorAll(".writing-order-item input")]
    .map((input) => input.value)
    .filter(Boolean);
}

async function applyOrder(ids) {
  if (!currentProject) return;
  const data = await api("/api/writing/workflow/order", {
    project_id: currentProject.project_id,
    ordered_section_ids: ids,
  });
  applyWorkflow(data.workflow);
}

async function acceptRecommendedOrder() {
  if (!currentWorkflow?.order_recommendation?.recommended_order?.length) return;
  const button = byId("acceptRecommendedOrderButton");
  setButtonLoading(button, true, "应用中...");
  try {
    await applyOrder(currentWorkflow.order_recommendation.recommended_order);
    showToast("success", "顺序已应用", "已切换到推荐写作顺序。");
  } catch (error) {
    showToast("error", "应用失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function applyManualOrder() {
  const button = byId("applyManualOrderButton");
  setButtonLoading(button, true, "应用中...");
  try {
    await applyOrder(currentOrderIds());
    showToast("success", "顺序已更新", "当前顺序已生效。");
  } catch (error) {
    showToast("error", "应用失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function saveSectionDraft() {
  if (!currentProject || !currentWorkflow?.current_section?.section_id) return;
  const button = byId("saveSectionDraftButton");
  setButtonLoading(button, true, "保存中...");
  try {
    const data = await api("/api/writing/workflow/section/save", {
      project_id: currentProject.project_id,
      section_id: currentWorkflow.current_section.section_id,
      content: byId("sectionEditor").value,
      prompt: "manual workflow save",
    });
    _dirtyFields.delete("sectionEditor");
    applyWorkflow(data.workflow);
    showToast("success", "草稿已保存", currentWorkflow.current_section.title || "章节");
  } catch (error) {
    showToast("error", "保存失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function generateSection() {
  if (!currentProject || !currentWorkflow?.current_section?.path) return;
  const button = byId("generateSectionButton");
  const prompt = byId("sectionPrompt").value.trim();
  if (!prompt) {
    showToast("error", "缺少任务", "请先输入章节任务。");
    return;
  }
  setButtonLoading(button, true, "生成中...");
  try {
    const data = await api("/api/writing/section/generate", {
      project_id: currentProject.project_id,
      file_path: currentWorkflow.current_section.path,
      prompt,
      mode: byId("sectionMode").value,
      context: byId("sectionEditor").value,
    });
    _dirtyFields.delete("sectionEditor");
    _safeSet("sectionEditor", data.insert_text || byId("sectionEditor").value);
    syncHighlight(byId("sectionEditor"), byId("sectionHighlightCode"));
    byId("sectionReply").textContent = data.reply || "已生成章节内容。";
    renderSectionAudit(data.audit_issues || []);
    currentProject = data.project || currentProject;
    if (data.workflow) applyWorkflow(data.workflow);
    if (data.guardrails?.violations?.length) renderGuardrailViolations({ guardrails: data.guardrails });
    syncPreview(currentProject);
    await loadWorkflow(currentProject.project_id);
    showToast("success", "章节已生成", currentWorkflow?.current_section?.title || "当前章节");
  } catch (error) {
    byId("sectionReply").textContent = `生成失败：${error.message}`;
    showToast("error", "生成失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function applyCitationSelections() {
  if (!currentProject || !currentWorkflow?.current_section?.section_id) return;
  const normalized = Object.fromEntries(
    Object.entries(citationSelections)
      .map(([placeholder, keys]) => [placeholder, Array.isArray(keys) ? keys.filter(Boolean) : []])
      .filter(([placeholder, keys]) => placeholder && keys.length)
  );
  if (!Object.keys(normalized).length) {
    showToast("error", "没有已选引用", "请先勾选至少一个候选文献。");
    return;
  }
  const button = byId("applyCitationSelectionsButton");
  setButtonLoading(button, true, "应用中...");
  try {
    const data = await api("/api/writing/workflow/citations/apply", {
      project_id: currentProject.project_id,
      section_id: currentWorkflow.current_section.section_id,
      citation_decisions: normalized,
    });
    if (data.file?.content) {
      _dirtyFields.delete("sectionEditor");
      _safeSet("sectionEditor", data.file.content);
      syncHighlight(byId("sectionEditor"), byId("sectionHighlightCode"));
    }
    applyWorkflow(data.workflow);
    showToast("success", "引用已应用", "已将批准的引用写回章节正文。");
  } catch (error) {
    showToast("error", "应用失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function insertWorkspaceFigure() {
  if (!currentProject || !currentWorkflow?.current_section?.path) return;
  const figures = currentProject?.workspace?.workspace_figures || [];
  if (!figures.length) {
    showToast("error", "没有可用图片", "当前项目还没有导入工作区图片。");
    return;
  }
  const choices = figures
    .slice(0, 8)
    .map((item, index) => `${index + 1}. ${item.path || item.name || "figure"}`)
    .join("\n");
  const selected = window.prompt(`输入要插入的图片编号：\n${choices}`, "1");
  const index = Number.parseInt(selected || "", 10) - 1;
  if (!Number.isInteger(index) || index < 0 || index >= figures.length) return;
  const figure = figures[index];
  const caption = window.prompt("图片说明", figure.name || "工作区图片") || figure.name || "工作区图片";
  const label = window.prompt("图片标签", `fig:${(figure.name || "workspace").replace(/\W+/g, "-").toLowerCase()}`) || "";
  const button = byId("insertWorkspaceFigureButton");
  setButtonLoading(button, true, "插入中...");
  try {
    const data = await api("/api/writing/project/workspace/figure", {
      project_id: currentProject.project_id,
      target_path: currentWorkflow.current_section.path,
      figure_rel_path: figure.path,
      caption,
      label,
    });
    if (data.file?.content) {
      _dirtyFields.delete("sectionEditor");
      _safeSet("sectionEditor", data.file.content);
      syncHighlight(byId("sectionEditor"), byId("sectionHighlightCode"));
    }
    await loadWorkflow(currentProject.project_id);
    showToast("success", "图片已插入", figure.path || figure.name || "workspace figure");
  } catch (error) {
    showToast("error", "插入失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function connectWorkflowStream(projectId) {
  closeWorkflowStream();
  if (!projectId || !window.EventSource) {
    startWorkflowPolling();
    return;
  }
  const stream = new EventSource(`/api/writing/workflow/stream?project_id=${encodeURIComponent(projectId)}`);
  workflowEventSource = stream;
  stream.addEventListener("workflow", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      if (payload.workflow) applyWorkflow(payload.workflow);
    } catch (_error) {
      // ignore malformed events
    }
  });
  stream.onerror = () => {
    closeWorkflowStream();
    startWorkflowPolling();
  };
}

function startWorkflowPolling() {
  if (workflowPollTimer) window.clearInterval(workflowPollTimer);
  workflowPollTimer = window.setInterval(async () => {
    if (!currentProject?.project_id || document.hidden) return;
    try {
      const data = await getJson(`/api/writing/workflow?project_id=${encodeURIComponent(currentProject.project_id)}`);
      if (data.workflow) applyWorkflow(data.workflow);
    } catch (_error) {
      // ignore transient polling errors
    }
  }, 12000);
}

async function analyzeGuardrails() {
  if (!currentProject) return;
  const button = byId("analyzeGuardrailsButton");
  setButtonLoading(button, true, "分析中...");
  try {
    const data = await api("/api/writing/project/guardrails/analyze", {
      project_id: currentProject.project_id,
    });
    _dirtyFields.delete("guardrailsYamlEditor");
    _safeSet("guardrailsYamlEditor", data.yaml_text || "");
    await loadWorkflow(currentProject.project_id);
    showToast("success", "契约已分析", "已更新当前项目的 guardrails.yaml。");
  } catch (error) {
    showToast("error", "分析失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function saveGuardrailsYaml() {
  if (!currentProject) return;
  const button = byId("saveGuardrailsYamlButton");
  setButtonLoading(button, true, "保存中...");
  try {
    const data = await api("/api/writing/project/guardrails/save", {
      project_id: currentProject.project_id,
      yaml_text: byId("guardrailsYamlEditor").value,
    });
    _dirtyFields.delete("guardrailsYamlEditor");
    _safeSet("guardrailsYamlEditor", data.yaml_text || byId("guardrailsYamlEditor").value);
    await loadWorkflow(currentProject.project_id);
    showToast("success", "契约已保存", "guardrails.yaml 已写回项目 memory。");
  } catch (error) {
    showToast("error", "保存失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function lockSection() {
  if (!currentProject || !currentWorkflow?.current_section?.section_id) return;
  const section = currentWorkflow.current_section;
  const isLocked = section.status === "locked" || section.locked === true;
  const button = byId("lockSectionButton");

  if (isLocked) {
    /* unlock */
    setButtonLoading(button, true, "解锁中...");
    try {
      const data = await api("/api/writing/workflow/section/unlock", {
        project_id: currentProject.project_id,
        section_id: section.section_id,
        cascade: false,
      });
      applyWorkflow(data.workflow);
      showToast("success", "章节已解锁", section.title || "当前章节");
    } catch (error) {
      showToast("error", "解锁失败", error.message);
    } finally {
      setButtonLoading(button, false);
    }
    return;
  }

  /* lock — save → compile → sync both logs & previews */
  setButtonLoading(button, true, "锁定中...");
  try {
    await _flushAllEditors();
    const data = await api("/api/writing/workflow/section/lock", {
      project_id: currentProject.project_id,
      section_id: section.section_id,
    });
    currentProject = { ...currentProject, compile: data.compile || currentProject.compile };
    syncCompileResult(data.compile || null);
    syncPreview(currentProject);
    applyWorkflow(data.workflow);
    showToast("success", "章节已锁定", "已编译并同步到所有预览位置。");
  } catch (error) {
    showToast("error", "锁定失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function compileProject() {
  if (!currentProject) return;
  const button = byId("compileProjectButton");
  setButtonLoading(button, true, "编译中...");
  try {
    await _flushAllEditors();
    const data = await api("/api/writing/project/compile", {
      project_id: currentProject.project_id,
    });
    currentProject = { ...currentProject, compile: data.compile };
    syncCompileResult(data.compile);
    syncPreview(currentProject);
    showToast("success", "编译完成", data.compile.status || "unknown");
  } catch (error) {
    showToast("error", "编译失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

async function finalReview() {
  if (!currentProject) return;
  const button = byId("finalReviewButton");
  setButtonLoading(button, true, "终审中...");
  try {
    await _flushAllEditors();
    const data = await api("/api/writing/workflow/final-review", {
      project_id: currentProject.project_id,
    });
    currentProject = { ...currentProject, compile: data.review?.compile || currentProject.compile };
    syncCompileResult(currentProject.compile);
    syncPreview(currentProject);
    applyWorkflow(data.workflow);
    const verdict = data.review?.audit?.verdict || "UNKNOWN";
    showToast("success", "终审完成", `审计结论：${verdict}`);
  } catch (error) {
    showToast("error", "终审失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function openSectionWindow() {
  if (!currentProject || !currentWorkflow?.current_section?.path) return;
  const url = `/writing-section?project_id=${encodeURIComponent(currentProject.project_id)}&path=${encodeURIComponent(currentWorkflow.current_section.path)}`;
  window.open(url, "_blank", "noopener,noreferrer");
}

function openSettings() {
  byId("settingsDrawer").classList.remove("is-hidden");
  byId("settingsOverlay").classList.remove("is-hidden");
}

function closeSettings() {
  byId("settingsDrawer").classList.add("is-hidden");
  byId("settingsOverlay").classList.add("is-hidden");
}

/* ── New Project Modal ── */
function openNewProjectModal() {
  byId("modalProjectTitle").value = "";
  byId("modalWritingType").value = "academic";
  byId("modalTemplate").innerHTML = currentTemplates.length
    ? renderTemplateOptionsInline(currentTemplates)
    : '<option value="">手动上传 / 无模板</option>';
  byId("modalTopic").value = "";
  byId("modalFileInput").value = "";
  byId("modalUploadHint").textContent = "上传后自动分析章节结构。";
  byId("newProjectOverlay").classList.remove("is-hidden");
  byId("newProjectModal").classList.remove("is-hidden");
}

function closeNewProjectModal() {
  byId("newProjectOverlay").classList.add("is-hidden");
  byId("newProjectModal").classList.add("is-hidden");
}

function renderTemplateOptionsInline(items) {
  const parts = ['<option value="">手动上传 / 无模板</option>'];
  let currentGroup = "";
  [...items]
    .sort((a, b) => String(a.group || "").localeCompare(String(b.group || ""), "zh-CN"))
    .forEach((item) => {
      const group = String(item.group || "模板");
      if (group !== currentGroup) {
        if (currentGroup) parts.push("</optgroup>");
        parts.push(`<optgroup label="${escapeHtml(group)}">`);
        currentGroup = group;
      }
      parts.push(`<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`);
    });
  if (currentGroup) parts.push("</optgroup>");
  return parts.join("");
}

async function createProjectFromModal() {
  const button = byId("confirmNewProjectButton");
  setButtonLoading(button, true, "创建中...");
  try {
    const templateId = byId("modalTemplate").value;
    const title = byId("modalProjectTitle").value.trim() || "Untitled Project";
    const writingType = byId("modalWritingType").value || "academic";
    const files = await collectUploads("modalFileInput");
    const topic = byId("modalTopic").value.trim();
    const payload = {
      title,
      template_id: templateId,
      writing_type: writingType,
      writing_language: writingType === "grant" ? "zh" : "en",
      goal: topic,
    };
    if (files.length === 1 && String(files[0].name || "").toLowerCase().endsWith(".zip")) {
      payload.archive = { name: files[0].name, content_base64: files[0].content_base64 };
    } else if (files.length) {
      payload.files = files;
    }
    const data = await api("/api/writing/project/create", payload);
    currentProject = data.project;
    closeNewProjectModal();
    await refreshProjects();
    await loadProject(currentProject.project_id);
    showToast("success", "项目已创建", currentProject.title || currentProject.project_id);
  } catch (error) {
    showToast("error", "创建失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

/* ── LLM chapter analysis ── */
async function analyzeChapters() {
  if (!currentProject?.project_id) {
    showToast("error", "缺少项目", "请先选择或创建项目。");
    return;
  }
  const button = byId("analyzeChaptersButton");
  setButtonLoading(button, true, "分析中...");
  try {
    await api("/api/writing/project/analyze-chapters", {
      project_id: currentProject.project_id,
    });
    // Reload workflow to pick up new sections_manifest
    await loadWorkflow(currentProject.project_id);
    showToast("success", "章节分析完成", "已更新章节结构。");
  } catch (error) {
    showToast("error", "分析失败", error.message);
  } finally {
    setButtonLoading(button, false);
  }
}

function bindEvents() {
  window.addEventListener("beforeunload", () => closeWorkflowStream());
  byId("projectSelect").addEventListener("change", async (event) => {
    await loadProject(event.target.value || "");
  });
  byId("refreshProjectsButton").addEventListener("click", () => refreshProjects());
  byId("newProjectButton").addEventListener("click", createNewProject);
  byId("saveProjectMetaButton").addEventListener("click", saveProjectMeta);
  byId("deleteProjectButton").addEventListener("click", deleteCurrentProject);
  byId("importProjectButton").addEventListener("click", () => importProject(false));
  byId("replaceProjectButton").addEventListener("click", () => importProject(true));
  byId("chatFileInput").addEventListener("change", uploadSources);
  byId("importWorkspaceButton").addEventListener("click", importWorkspace);
  byId("analyzeGuardrailsButton").addEventListener("click", analyzeGuardrails);
  byId("saveGuardrailsYamlButton").addEventListener("click", saveGuardrailsYaml);
  byId("explorationSubmitButton").addEventListener("click", runExploration);
  byId("analyzeChaptersButton").addEventListener("click", analyzeChapters);
  byId("confirmTopicButton").addEventListener("click", confirmTopic);
  byId("confirmNegotiationButton").addEventListener("click", confirmNegotiation);
  byId("acceptRecommendedOrderButton").addEventListener("click", acceptRecommendedOrder);
  byId("applyManualOrderButton").addEventListener("click", applyManualOrder);
  byId("saveSectionDraftButton").addEventListener("click", saveSectionDraft);
  byId("generateSectionButton").addEventListener("click", generateSection);
  byId("insertWorkspaceFigureButton").addEventListener("click", insertWorkspaceFigure);
  byId("applyCitationSelectionsButton").addEventListener("click", applyCitationSelections);
  byId("lockSectionButton").addEventListener("click", lockSection);
  byId("compileProjectButton").addEventListener("click", compileProject);
  byId("finalReviewButton").addEventListener("click", finalReview);
  byId("openSectionWindowButton").addEventListener("click", openSectionWindow);
  byId("startOrderingButton").addEventListener("click", () => setMode("negotiate"));
  byId("modeExploreButton").addEventListener("click", () => { _manualMode = "explore"; setMode("explore"); });
  byId("modeNegotiateButton").addEventListener("click", () => { _manualMode = "negotiate"; setMode("negotiate"); });
  byId("modeWriteButton").addEventListener("click", () => { _manualMode = "write"; setMode("write"); });
  byId("openSettingsButton").addEventListener("click", openSettings);
  byId("closeDrawerButton").addEventListener("click", closeSettings);
  byId("settingsOverlay").addEventListener("click", closeSettings);

  /* New project modal */
  byId("confirmNewProjectButton").addEventListener("click", createProjectFromModal);
  byId("cancelNewProjectButton").addEventListener("click", closeNewProjectModal);
  byId("closeNewProjectModalButton").addEventListener("click", closeNewProjectModal);
  byId("newProjectOverlay").addEventListener("click", closeNewProjectModal);

  /* Upload zone in modal */
  const modalUploadZone = byId("modalUploadZone");
  if (modalUploadZone) {
    modalUploadZone.addEventListener("click", () => byId("modalFileInput").click());
    modalUploadZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      modalUploadZone.classList.add("has-files");
    });
    modalUploadZone.addEventListener("dragleave", () => modalUploadZone.classList.remove("has-files"));
    modalUploadZone.addEventListener("drop", (event) => {
      event.preventDefault();
      modalUploadZone.classList.remove("has-files");
      if (event.dataTransfer?.files?.length) {
        byId("modalFileInput").files = event.dataTransfer.files;
        byId("modalUploadHint").textContent = `已选择 ${event.dataTransfer.files.length} 个文件，创建项目后将自动分析章节。`;
      }
    });
  }
  byId("modalFileInput").addEventListener("change", () => {
    const count = byId("modalFileInput").files?.length || 0;
    if (count) {
      byId("modalUploadHint").textContent = `已选择 ${count} 个文件，创建项目后将自动分析章节。`;
    }
  });

  /* Track manual edits to prevent SSE/polling from overwriting user input */
  const _markDirty = (el) => {
    if (!el) return;
    _dirtyFields.add(el.id);
    if (el.id === "sectionEditor") _scheduleAutoSave();
    if (el.id === "overleafEditor") _scheduleOverleafAutoSave();
  };
  ["explorationTopicInput", "explorationCustomTopicInput", "sectionEditor",
   "sectionPrompt", "negotiationCustomNote", "sectionMode",
   "chatTitle", "writingType", "guardrailsYamlEditor", "overleafEditor"].forEach((id) => {
    const el = byId(id);
    if (el) el.addEventListener("input", () => _markDirty(el));
    if (el && (el.tagName === "SELECT" || el.tagName === "TEXTAREA")) {
      el.addEventListener("change", () => _markDirty(el));
    }
  });

  /* warn before leaving with unsaved editor changes */
  window.addEventListener("beforeunload", (event) => {
    if (_dirtyFields.has("sectionEditor") || _dirtyFields.has("overleafEditor")) {
      event.preventDefault();
      /* modern browsers show a generic message */
    }
  });

  /* Overleaf panel events */
  byId("overleafCompileButton").addEventListener("click", overleafCompile);
  byId("overleafDownloadPdfButton").addEventListener("click", () => {
    if (currentProject) {
      window.open(buildPdfPreviewUrl(currentProject.project_id), "_blank", "noopener,noreferrer");
    }
  });
  byId("overleafToggleButton").addEventListener("click", toggleOverleafPanel);
  initOverleafResizer();

  /* Overleaf editor: input → highlight sync */
  byId("overleafEditor").addEventListener("input", () => {
    _scheduleHighlight("overleafEditor", "overleafHighlightCode");
  });
  byId("overleafEditor").addEventListener("scroll", () => {
    syncHighlightScroll(byId("overleafEditor"), byId("overleafHighlight"));
  });
  /* Ctrl+S in Overleaf editor */
  byId("overleafEditor").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
      event.preventDefault();
      saveOverleafFile();
    }
  });

  /* Section editor: input → highlight sync */
  byId("sectionEditor").addEventListener("input", () => {
    _scheduleHighlight("sectionEditor", "sectionHighlightCode");
  });
  byId("sectionEditor").addEventListener("scroll", () => {
    syncHighlightScroll(byId("sectionEditor"), byId("sectionHighlightCode")?.parentElement);
  });

  const uploadZone = byId("uploadZone");
  if (uploadZone) {
    uploadZone.addEventListener("click", () => byId("chatFileInput").click());
    uploadZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      uploadZone.classList.add("has-files");
    });
    uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("has-files"));
    uploadZone.addEventListener("drop", (event) => {
      event.preventDefault();
      uploadZone.classList.remove("has-files");
      if (event.dataTransfer?.files?.length) {
        byId("chatFileInput").files = event.dataTransfer.files;
        uploadSources();
      }
    });
  }
}

async function bootstrap() {
  bindEvents();
  await Promise.all([refreshTemplates(), refreshProjects()]);
}

bootstrap().catch((error) => {
  showToast("error", "初始化失败", error.message);
});
