import OpenAI from "openai";

function trimArticles(articles) {
  const maxLength = Number(process.env.MAX_ARTICLE_LENGTH || 18000);
  return articles.map((article) => ({
    ...article,
    content: article.content ? article.content.trim().slice(0, maxLength) : ""
  }));
}

async function createCompletion(articleText) {
  if (!articleText?.trim()) {
    return "";
  }
  const apiKey = process.env.APIKEY || process.env.OPENAI_API_KEY || process.env.KIMI_API_KEY;
  if (!apiKey) {
    return "";
  }
  const client = new OpenAI({
    apiKey,
    baseURL: process.env.BASEURL || process.env.OPENAI_BASE_URL || "https://api.moonshot.cn/v1"
  });
  const completion = await client.chat.completions.create({
    model: process.env.MODEL || "kimi-k2.5",
    messages: [
      {
        role: "system",
        content:
          process.env.PROMPT ||
          "你是科研文献助手。请用中文总结正文，输出 JSON object，字段包括 summary, why_it_matters, methods, datasets, limitations, next_actions, schedule_suggestion。"
      },
      {
        role: "user",
        content: articleText
      }
    ]
  });
  return completion?.choices?.[0]?.message?.content?.trim() || "";
}

export async function getCompletion({ articles }) {
  const trimmed = trimArticles(articles);
  const responses = await Promise.all(
    trimmed.map(async (article, index) => ({
      index,
      response: await createCompletion(article.content)
    }))
  );
  const ordered = responses.sort((a, b) => a.index - b.index);
  return trimmed.map((article, index) => ({
    ...article,
    summary: ordered[index].response
  }));
}
