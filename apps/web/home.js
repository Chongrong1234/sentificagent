function byId(id) { return document.getElementById(id); }

function setText(id, value) { byId(id).textContent = value; }

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
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

function renderStats(stats) {
  setText("graphPaperCount", String(stats?.papers ?? 0));
  setText("topicCount", String(stats?.topics ?? 0));
  setText("graphEdgeCount", String(stats?.edges ?? 0));
}

async function fetchHomeHealth() {
  const response = await fetch("/health");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || "服务不可用");
  setText("configPath", data.config_path || "unknown");
  setText("libraryRoot", data.library_root || "unknown");
  setText("serviceStatus", data.status || "ok");
  setText("modelLabel", "kimi-k2.5");
  renderStats(data.stats || {});
}

fetchHomeHealth().catch((error) => {
  setText("serviceStatus", "offline");
  setText("configPath", error.message || String(error));
  showToast("error", "服务异常", error.message);
});
