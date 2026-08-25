import { config } from "dotenv";
import fs from "fs";
import { parseWithPlaywright } from "./parse-url.js";
import { getCompletion } from "./completions.js";

config({
  path: process.env.ENV_FILE || `${process.env.HOME}/.env`,
  override: false
});

const [inputFile, outputFile] = process.argv.slice(2);

if (!inputFile || !outputFile) {
  console.error("Usage: npm run summarize -- <input-urls.txt> <output.json>");
  process.exit(2);
}

const urls = fs
  .readFileSync(inputFile, "utf-8")
  .split(/\r?\n/)
  .map((item) => item.trim())
  .filter(Boolean);

const articles = await parseWithPlaywright(urls);
if (process.env.FETCH_ONLY === "1") {
  fs.writeFileSync(outputFile, JSON.stringify(articles, null, 2));
  console.log(JSON.stringify({ status: "done", mode: "fetch-only", count: articles.length, outputFile }));
  process.exit(0);
}
const responses = await getCompletion({ articles });
fs.writeFileSync(outputFile, JSON.stringify(responses, null, 2));
console.log(JSON.stringify({ status: "done", count: responses.length, outputFile }));
