const DEFAULT_SERVER_URL = "http://127.0.0.1:8765";

function slugify(value) {
  return (value || "paper")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-") || "paper";
}

async function sendCapture(serverUrl, payload) {
  const response = await fetch(`${serverUrl}/api/capture`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "Capture failed");
  }
  return data;
}

async function downloadPdfIfAvailable(payload, captureResult) {
  const pdfUrl = payload.paper && payload.paper.pdf_url;
  if (!pdfUrl) {
    return { status: "skipped", reason: "No PDF URL detected" };
  }

  const suggestedFilename =
    captureResult.download_plan &&
    captureResult.download_plan.suggested_filename;

  const fallbackFilename = `scientific-agent/inbox/${slugify(payload.paper.title)}.pdf`;

  const downloadId = await chrome.downloads.download({
    url: pdfUrl,
    filename: suggestedFilename || fallbackFilename,
    conflictAction: "uniquify",
    saveAs: false
  });

  return {
    status: "started",
    downloadId,
    filename: suggestedFilename || fallbackFilename
  };
}

async function fetchDownloadQueue(serverUrl) {
  const response = await fetch(`${serverUrl}/api/download-queue`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || "Failed to load queue");
  }
  return data.batches || [];
}

async function processQueueBatch(serverUrl) {
  const batches = await fetchDownloadQueue(serverUrl);
  if (!batches.length) {
    return {
      status: "empty",
      queued: 0,
      downloads: []
    };
  }

  const batch = batches[0];
  const downloads = [];
  for (const item of batch.items || []) {
    if (!item.paper || !item.paper.pdf_url) {
      downloads.push({
        title: item.paper && item.paper.title ? item.paper.title : "unknown",
        status: "skipped",
        reason: "No pdf_url"
      });
      continue;
    }

    const fallbackFilename = `scientific-agent/queue/${slugify(item.paper.title)}.pdf`;
    try {
      const downloadId = await chrome.downloads.download({
        url: item.paper.pdf_url,
        filename: fallbackFilename,
        conflictAction: "uniquify",
        saveAs: false
      });
      downloads.push({
        title: item.paper.title || "unknown",
        status: "started",
        downloadId
      });
    } catch (error) {
      downloads.push({
        title: item.paper.title || "unknown",
        status: "failed",
        reason: error.message || String(error)
      });
    }
  }

  return {
    status: "processed",
    queued: (batch.items || []).length,
    downloads,
    queuePath: batch.queue_path || ""
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    try {
      if (message.type === "capturePaper") {
        const serverUrl = message.serverUrl || DEFAULT_SERVER_URL;
        const captureResult = await sendCapture(serverUrl, message.payload);
        let downloadResult = { status: "disabled" };

        if (message.downloadPdf) {
          downloadResult = await downloadPdfIfAvailable(message.payload, captureResult);
        }

        sendResponse({
          ok: true,
          capture: captureResult,
          download: downloadResult
        });
        return;
      }

      if (message.type === "processDownloadQueue") {
        const serverUrl = message.serverUrl || DEFAULT_SERVER_URL;
        const queueResult = await processQueueBatch(serverUrl);
        sendResponse({
          ok: true,
          queue: queueResult
        });
        return;
      }

      sendResponse({
        ok: false,
        error: "Unsupported message type"
      });
    } catch (error) {
      sendResponse({
        ok: false,
        error: error.message || String(error)
      });
    }
  })();

  return true;
});
