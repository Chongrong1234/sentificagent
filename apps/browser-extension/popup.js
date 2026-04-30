const DEFAULT_SERVER_URL = "http://127.0.0.1:8765";

let currentPaper = null;

function setStatus(message) {
  document.getElementById("status").textContent = message;
}

function parseCommaSeparated(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function extractFromActiveTab() {
  const tab = await getActiveTab();
  const response = await chrome.tabs.sendMessage(tab.id, { type: "extractPaper" });
  if (!response || !response.ok) {
    throw new Error("Could not extract metadata from the current page");
  }
  return response.paper;
}

function renderPaperSummary(paper) {
  const title = paper.title || "Untitled page";
  const venue = paper.journal || paper.conference || paper.site_name || "Unknown venue";
  const pdfStatus = paper.pdf_url ? "PDF detected" : "No PDF detected";
  document.getElementById("paperSummary").textContent = `${title}\n${venue}\n${pdfStatus}`;
}

async function loadServerUrl() {
  const result = await chrome.storage.local.get(["serverUrl"]);
  const value = result.serverUrl || DEFAULT_SERVER_URL;
  document.getElementById("serverUrl").value = value;
}

async function saveServerUrl() {
  const value = document.getElementById("serverUrl").value.trim() || DEFAULT_SERVER_URL;
  await chrome.storage.local.set({ serverUrl: value });
  return value;
}

async function runCapture(downloadPdf) {
  if (!currentPaper) {
    currentPaper = await extractFromActiveTab();
    renderPaperSummary(currentPaper);
  }

  const serverUrl = await saveServerUrl();
  const payload = {
    captured_at: new Date().toISOString(),
    source: {
      trigger: "browser-extension"
    },
    paper: currentPaper,
    overrides: {
      team: document.getElementById("team").value.trim(),
      venue_tier: document.getElementById("venueTier").value.trim(),
      keywords: parseCommaSeparated(document.getElementById("keywords").value)
    }
  };

  setStatus("Submitting capture...");
  const response = await chrome.runtime.sendMessage({
    type: "capturePaper",
    serverUrl,
    payload,
    downloadPdf
  });

  if (!response || !response.ok) {
    throw new Error((response && response.error) || "Capture failed");
  }

  const capture = response.capture;
  const download = response.download;
  const lines = [
    `Classification: ${capture.classification.venue_tier} / ${capture.classification.primary_team} / ${capture.classification.primary_keyword}`,
    `Metadata: ${capture.paths.metadata}`,
    `Download: ${download.status}${download.filename ? ` -> ${download.filename}` : ""}`
  ];
  setStatus(lines.join("\n"));
}

async function runQueue() {
  const serverUrl = await saveServerUrl();
  setStatus("Processing queued downloads...");
  const response = await chrome.runtime.sendMessage({
    type: "processDownloadQueue",
    serverUrl
  });
  if (!response || !response.ok) {
    throw new Error((response && response.error) || "Queue processing failed");
  }

  const queue = response.queue;
  const lines = [
    `Queue: ${queue.status}`,
    `Queued items: ${queue.queued}`,
    ...(queue.downloads || []).slice(0, 8).map((item) => `${item.status}: ${item.title}`)
  ];
  setStatus(lines.join("\n"));
}

async function bootstrap() {
  await loadServerUrl();
  setStatus("Extracting current page...");
  try {
    currentPaper = await extractFromActiveTab();
    renderPaperSummary(currentPaper);
    setStatus("Ready");
  } catch (error) {
    setStatus(error.message || String(error));
  }
}

document.getElementById("captureOnly").addEventListener("click", async () => {
  try {
    await runCapture(false);
  } catch (error) {
    setStatus(error.message || String(error));
  }
});

document.getElementById("captureAndDownload").addEventListener("click", async () => {
  try {
    await runCapture(true);
  } catch (error) {
    setStatus(error.message || String(error));
  }
});

document.getElementById("processQueue").addEventListener("click", async () => {
  try {
    await runQueue();
  } catch (error) {
    setStatus(error.message || String(error));
  }
});

bootstrap();
