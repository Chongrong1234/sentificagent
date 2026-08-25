# Scientific Agent Elfeed Workflow

This directory implements the reference workflow with native Emacs components:

1. `elfeed` discovers entries from RSS/Atom feeds.
2. `elfeed-score` assigns personalized priority scores.
3. Entries with score above `scientific-agent-score-threshold` or tag `to-summarize`
   are exported to the Node `article-summarizer`.
4. `article-summarizer` uses Playwright Firefox and Mozilla Readability to extract
   readable article text.
5. OpenAI-compatible chat completion writes summaries.
6. Summaries are written back to `elfeed-entry` metadata as `:summary`.
7. Org schedule files are exported, and `scientific-agent-org-capture-entry` can
   capture the current elfeed entry.

## Install

Install Emacs packages:

```elisp
(use-package elfeed :ensure t)
(use-package elfeed-score :ensure t)
```

Install the Node summarizer:

```bash
cd /home/lichongrong/桌面/scientific_agent/tools/article-summarizer
npm install
npx playwright install firefox
```

Configure API credentials in `~/.env`:

```text
APIKEY=your-key
BASEURL=https://api.moonshot.cn/v1
MODEL=kimi-k2.5
MAX_ARTICLE_LENGTH=18000
PROMPT=你是科研文献助手。请输出 JSON object，字段包括 summary, why_it_matters, methods, datasets, limitations, next_actions, schedule_suggestion。
```

Add `tools/elfeed/init-example.el` content to your Emacs config.

## Run

In Emacs:

```elisp
M-x scientific-agent-configure-feeds
M-x elfeed-update
```

After update, the hook calls:

```elisp
scientific-agent-summarize-candidates
```

Manual trigger:

```elisp
M-x scientific-agent-summarize-candidates
```

Manual force-summary:

1. In `elfeed-search`, tag an entry with `to-summarize`.
2. Run `M-x scientific-agent-summarize-candidates`.

Outputs:

```text
data/library/elfeed/*-urls.txt
data/library/elfeed/*-summaries.json
data/library/elfeed/*-schedule.org
```

Metadata written back to entries:

```elisp
(elfeed-meta entry :summary)
(elfeed-meta entry :article-title)
(elfeed-meta entry :article-excerpt)
(elfeed-meta entry :article-length)
```
