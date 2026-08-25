function byId(id) {
  return document.getElementById(id);
}

const _dirtyFields = new Set();
let _autoSaveTimer = 0;

function _safeSet(id, value) {
  const el = byId(id);
  if (!el) return;
  if (_dirtyFields.has(id)) return;
  el.value = value;
}

function _scheduleAutoSave() {
  if (_autoSaveTimer) window.clearTimeout(_autoSaveTimer);
  _autoSaveTimer = window.setTimeout(() => {
    if (!_dirtyFields.has("sectionEditor")) return;
    const projectId = queryParam("project_id");
    const filePath = queryParam("path");
    if (!projectId || !filePath) return;
    const content = byId("sectionEditor")?.value;
    if (content === undefined) return;
    api("/api/writing/project/file/save", {
      project_id: projectId,
      path: filePath,
      content,
    }).then(() => {
      _dirtyFields.delete("sectionEditor");
    }).catch(() => {
      /* silent */
    });
  }, 3000);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const _SECTION_CMDS = new Set([
  "part", "chapter", "section", "subsection", "subsubsection",
  "paragraph", "subparagraph",
]);

function _parseLatexArg(text, i, len, open, close) {
  if (i >= len || text[i] !== open) return [i, null];
  let depth = 1; i++;
  while (i < len && depth > 0) {
    if (text[i] === open && text[i - 1] !== "\\") depth++;
    else if (text[i] === close && text[i - 1] !== "\\") depth--;
    i++;
  }
  return [i, text.slice(i > 0 ? text.lastIndexOf(open, i - 2) : 0, i)];
}

function highlightLatex(text) {
  let out = "";
  let i = 0;
  const len = text.length;

  while (i < len) {
    if (text[i] === "%") {
      let end = text.indexOf("\n", i);
      if (end === -1) end = len;
      out += `<span class="hl-comment">${escapeHtml(text.slice(i, end))}</span>`;
      i = end;
      continue;
    }
    if (text[i] === "$" && (i === 0 || text[i - 1] !== "\\")) {
      let j = i + 1;
      while (j < len) { if (text[j] === "$" && text[j - 1] !== "\\") break; j++; }
      if (j < len) j++; else j = i + 1;
      out += `<span class="hl-math">${escapeHtml(text.slice(i, j))}</span>`;
      i = j; continue;
    }
    if (text[i] === "\\") {
      let start = i; i++;
      while (i < len && /[a-zA-Z@]/.test(text[i])) i++;
      let nameEnd = i;
      let name = text.slice(start + 1, nameEnd);
      if (i < len && text[i] === "*") i++;
      if (i < len && text[i] === "[") { let d = 1; i++; while (i < len && d > 0) { if (text[i] === "[" && text[i-1] !== "\\") d++; else if (text[i] === "]" && text[i-1] !== "\\") d--; i++; } }
      if (i < len && text[i] === "{") { let d = 1; i++; while (i < len && d > 0) { if (text[i] === "{" && text[i-1] !== "\\") d++; else if (text[i] === "}" && text[i-1] !== "\\") d--; i++; } }
      let cmd = text.slice(start, i);
      if (/^\\(begin|end)\{/.test(cmd)) {
        out += `<span class="hl-env">${escapeHtml(cmd)}</span>`;
      } else if (_SECTION_CMDS.has(name)) {
        out += `<span class="hl-section">${escapeHtml(cmd)}</span>`;
      } else {
        out += `<span class="hl-command">${escapeHtml(cmd)}</span>`;
      }
      continue;
    }
    if ("&#_^~".includes(text[i])) {
      out += text[i] === "&" ? `<span class="hl-special">&amp;</span>` : `<span class="hl-special">${escapeHtml(text[i])}</span>`;
      i++; continue;
    }
    if (text[i] === "{" || text[i] === "}") {
      out += `<span class="hl-brace">${escapeHtml(text[i])}</span>`;
      i++; continue;
    }
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

function queryParam(name) {
  const url = new URL(window.location.href);
  return url.searchParams.get(name) || "";
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "request failed");
  }
  return data;
}

async function getJson(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "request failed");
  }
  return data;
}

function setLoading(button, isLoading, text) {
  if (!button) {
    return;
  }
  if (isLoading) {
    button.dataset.originalText = button.textContent;
    button.textContent = text;
    button.disabled = true;
    return;
  }
  button.textContent = button.dataset.originalText || button.textContent;
  button.disabled = false;
}

function renderEvidence(items) {
  const container = byId("sectionEvidence");
  if (!items || !items.length) {
    container.innerHTML = '<p class="empty">没有返回新增证据。</p>';
    return;
  }
  container.innerHTML = items.map((item) => `
    <article class="preview-card">
      <strong>${escapeHtml(item.title || "Untitled")}</strong>
      <p>${escapeHtml(item.year || "")} / ${escapeHtml(item.venue || "")}</p>
    </article>
  `).join("");
}

function summarizeBlock(title, items, emptyText) {
  if (!items || !items.length) {
    return `
      <article class="preview-card">
        <strong>${escapeHtml(title)}</strong>
        <p class="empty">${escapeHtml(emptyText)}</p>
      </article>
    `;
  }
  return `
    <article class="preview-card">
      <strong>${escapeHtml(title)}</strong>
      ${items.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
    </article>
  `;
}

function renderMemory(context) {
  const container = byId("sectionMemoryCards");
  if (!context) {
    container.innerHTML = '<article class="preview-card"><p class="empty">上下文为空。</p></article>';
    return;
  }
  const recent = (context.recent_context || []).slice(-8).map((item) => {
    return `[${item.role || ""}/${item.kind || ""}] ${item.file_path || "project"}: ${item.summary || ""}`;
  });
  const sectionMemories = (context.section_memories || []).slice(-8).map((item) => {
    return `${item.section || item.path || ""}: ${item.memory || ""}`;
  });
  const evidenceCards = ((context.evidence_memory || {}).cards || []).slice(0, 6).map((item) => {
    return `[${item.key || ""}] ${item.title || ""}: ${item.claim || ""}`;
  });
  const sources = (context.source_files || []).slice(0, 6).map((item) => {
    return `${item.name || ""}: ${item.excerpt || ""}`;
  });
  container.innerHTML = [
    summarizeBlock("最近上下文", recent, "暂无最近上下文。"),
    summarizeBlock("章节记忆", sectionMemories, "暂无章节记忆。"),
    summarizeBlock("证据卡", evidenceCards, "暂无证据卡。"),
    summarizeBlock("源材料", sources, "暂无源材料。"),
  ].join("");
}

async function loadSection() {
  const projectId = queryParam("project_id");
  const filePath = queryParam("path");
  if (!projectId || !filePath) {
    byId("sectionReply").textContent = "缺少 project_id 或 path。";
    return;
  }
  const [projectData, fileData, contextData] = await Promise.all([
    getJson(`/api/writing/project?id=${encodeURIComponent(projectId)}`),
    getJson(`/api/writing/project/file?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(filePath)}`),
    getJson(`/api/writing/project/context?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(filePath)}`),
  ]);
  byId("sectionWindowTitle").textContent = contextData.context?.section?.title || projectData.project?.title || "章节独立生成窗口";
  byId("sectionWindowMeta").textContent = `${projectData.project?.project_id || ""} / 手动上传项目`;
  byId("sectionFilePath").textContent = filePath;
  _dirtyFields.delete("sectionEditor");
  _safeSet("sectionEditor", fileData.file?.is_text ? (fileData.file?.content || "") : "当前文件是项目资源，不适合章节生成。");
  syncHighlight(byId("sectionEditor"), byId("sectionHighlightCode"));
  byId("sectionEditor").readOnly = !fileData.file?.is_text;
  renderMemory(contextData.context || null);
}

async function saveSection() {
  const projectId = queryParam("project_id");
  const filePath = queryParam("path");
  if (byId("sectionEditor").readOnly) {
    byId("sectionReply").textContent = "当前文件是项目资源，不能在章节窗口中保存。";
    return;
  }
  await api("/api/writing/project/file/save", {
    project_id: projectId,
    path: filePath,
    content: byId("sectionEditor").value,
  });
  _dirtyFields.delete("sectionEditor");
  byId("sectionReply").textContent = `已保存 ${filePath}`;
  await loadSection();
}

async function generateSection() {
  const projectId = queryParam("project_id");
  const filePath = queryParam("path");
  const prompt = byId("sectionPrompt").value.trim();
  if (!prompt) {
    byId("sectionReply").textContent = "请先输入章节任务。";
    return;
  }
  if (byId("sectionEditor").readOnly) {
    byId("sectionReply").textContent = "当前文件是项目资源，不适合章节生成。";
    return;
  }
  const button = byId("generateSectionButton");
  setLoading(button, true, "生成中...");
  try {
    const data = await api("/api/writing/section/generate", {
      project_id: projectId,
      file_path: filePath,
      prompt,
      mode: byId("sectionMode").value,
      context: byId("sectionEditor").value,
    });
    _dirtyFields.delete("sectionEditor");
    _safeSet("sectionEditor", data.insert_text || "");
    syncHighlight(byId("sectionEditor"), byId("sectionHighlightCode"));
    byId("sectionReply").textContent = data.reply || "已生成章节内容。";
    renderEvidence(data.evidence || []);
    renderMemory(data.context || null);
  } catch (error) {
    byId("sectionReply").textContent = `生成失败：${error.message}`;
  } finally {
    setLoading(button, false);
  }
}

function bootstrap() {
  byId("reloadSectionButton").addEventListener("click", loadSection);
  byId("saveSectionButton").addEventListener("click", saveSection);
  byId("generateSectionButton").addEventListener("click", generateSection);

  /* dirty tracking + auto-save + highlight sync */
  const editor = byId("sectionEditor");
  if (editor) {
    editor.addEventListener("input", () => {
      _dirtyFields.add("sectionEditor");
      _scheduleAutoSave();
      syncHighlight(editor, byId("sectionHighlightCode"));
    });
    editor.addEventListener("scroll", () => {
      syncHighlightScroll(editor, byId("sectionHighlightCode")?.parentElement);
    });
  }
  window.addEventListener("beforeunload", (event) => {
    if (_dirtyFields.has("sectionEditor")) {
      event.preventDefault();
    }
  });

  loadSection().catch((error) => {
    byId("sectionReply").textContent = `加载失败：${error.message}`;
  });
}

bootstrap();
