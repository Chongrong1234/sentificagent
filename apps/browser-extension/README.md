# Browser Extension MVP

This extension extracts literature metadata from the current page and sends it to the
local capture service at `http://127.0.0.1:8765`.

It also downloads the detected PDF through the browser session. This keeps access to
publisher cookies, VPN context, and SSO state inside the browser where they already work.

## Load

1. Open the browser extension page.
2. Enable developer mode.
3. Load unpacked extension from this folder.

## Current behavior

- extracts title, DOI, abstract, authors, venue, year, keywords, and candidate PDF URL
- posts metadata to the local capture service
- asks the service for a config-driven folder path
- downloads the PDF to `Downloads/<configured-prefix>/<classified-path>/`
- can process a queued batch of high-score download candidates created by the local app

## IEEE and ACM

This extension now includes first-stage rules for:

- IEEE Xplore detail pages like `https://ieeexplore.ieee.org/document/<id>`
- ACM DOI pages like `https://dl.acm.org/doi/<doi>`

For these sources the extension prefers canonical PDF routes:

- IEEE: `https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=<id>`
- ACM: `https://dl.acm.org/doi/pdf/<doi>`

## How to test

1. Start the local service with `python3 scripts/run_capture_server.py`.
2. Open a paper page such as:
   - `https://arxiv.org/abs/1706.03762`
   - `https://openreview.net/forum?id=VtmBAGCN7o`
3. Click the extension popup.
4. Check whether the page summary shows a title and `PDF detected`.
5. Click `Capture only` and confirm a new file appears under `data/library/inbox/`.
6. Click `Capture + download` and confirm the browser starts downloading the PDF.
7. For batch mode, first create a queue from the local app search page, then click `Run download queue`.

## Important limitation

This first version does not move the downloaded PDF into the project data volume.
That importer belongs in the next step of the local library service.
