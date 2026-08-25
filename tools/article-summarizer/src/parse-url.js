import { JSDOM } from "jsdom";
import { Readability } from "@mozilla/readability";
import { firefox } from "playwright";

function normalizeBioRxivUrl(originalUrl = "") {
  try {
    const url = new URL(originalUrl);
    if (url.hostname.includes("biorxiv") && url.searchParams.get("rss") === "1") {
      url.searchParams.delete("rss");
      if (!url.pathname.endsWith(".full")) {
        url.pathname += ".full";
      }
    }
    return url.toString();
  } catch (_error) {
    return originalUrl;
  }
}

function safeSelector() {
  return process.env.SELECTORS ? decodeURIComponent(process.env.SELECTORS) : "body";
}

function cleanText(value = "") {
  return value
    .replace(/\u00a0/g, " ")
    .replace(/\s+[\r\n]\s+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export async function parseWithPlaywright(urls) {
  const browser = await firefox.launch({
    headless: true,
    firefoxUserPrefs: {
      "javascript.enabled": true,
      "permissions.default.image": 2,
      "network.http.redirection-limit": 32,
      "media.volume_scale": "0.0"
    },
    args: ["--disable-gpu", "--disable-software-rasterizer"]
  });

  const results = [];
  try {
    for (const [index, url] of urls.entries()) {
      const context = await browser.newContext({
        userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:90.0) Gecko/20100101 Firefox/90.0"
      });
      await context.addInitScript(() => {
        const originalResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
        Object.defineProperty(Intl.DateTimeFormat.prototype, "resolvedOptions", {
          value() {
            const result = Reflect.apply(originalResolvedOptions, this, arguments);
            result.timeZone = "Asia/Shanghai";
            return result;
          }
        });
      });

      const page = await context.newPage();
      await page.route(/.*google.*/, (route) => route.abort());
      await page.setViewportSize({
        width: 1280 + Math.floor(Math.random() * 100),
        height: 800 + Math.floor(Math.random() * 100)
      });

      try {
        await page.goto(normalizeBioRxivUrl(url), {
          waitUntil: "domcontentloaded",
          timeout: Number(process.env.PAGE_TIMEOUT_MS || 25000)
        });
        await page.waitForSelector(safeSelector(), {
          state: "attached",
          timeout: Number(process.env.SELECTOR_TIMEOUT_MS || 15000)
        });
        const dynamicHtml = await page.content();
        const dom = new JSDOM(dynamicHtml, { url });
        const article = new Readability(dom.window.document).parse();
        if (!article) {
          results.push({
            order: index + 1,
            url,
            status: "fail",
            title: "",
            content: "",
            excerpt: "",
            length: 0
          });
        } else {
          const content = cleanText(article.textContent || "");
          results.push({
            order: index + 1,
            url,
            status: "success",
            title: article.title || "",
            content,
            excerpt: article.excerpt || "",
            length: content.length
          });
        }
      } catch (error) {
        results.push({
          order: index + 1,
          url,
          status: "error",
          title: "",
          content: "",
          excerpt: "",
          length: 0,
          error: error.message || String(error)
        });
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
  return results.sort((a, b) => a.order - b.order);
}
