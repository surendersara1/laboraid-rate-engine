# Product Walkthrough SPA

Single-file tabbed renderer of the three product walkthrough docs:

- [`../Runbooks/PRODUCT_END_TO_END_FLOW.md`](../Runbooks/PRODUCT_END_TO_END_FLOW.md)
- [`../Runbooks/PRODUCT_SERVICE_INVENTORY.md`](../Runbooks/PRODUCT_SERVICE_INVENTORY.md)
- [`../Runbooks/PRODUCT_ERROR_AND_LOGGING_REFERENCE.md`](../Runbooks/PRODUCT_ERROR_AND_LOGGING_REFERENCE.md)

Live at:

> **https://d3ggwschjt81wu.cloudfront.net/product-walkthrough.html**

(Same CloudFront distribution as the React UI; served from
`s3://laboraid-dev-l1-bucket-spa/product-walkthrough.html`.)

## Structure

```
docs/spa/
  template.html        # Single-file shell with embedded CSS + JS + CDN deps
  build.py             # Inlines the three Runbook MDs (base64) into the template
  deploy.py            # Builds, uploads to S3, invalidates CloudFront
  README.md            # this file
  dist/index.html      # Build output (gitignored)
```

## Build

```bash
python docs/spa/build.py
```

Writes `docs/spa/dist/index.html` (~95 KB, fully self-contained — open in a
browser, no other assets needed). The three source MDs are base64-inlined
into the HTML so the SPA renders identically locally and on S3, and there's
no CORS path to debug.

## Deploy

```bash
# build + upload + CloudFront invalidation
python docs/spa/deploy.py

# build + show what would happen, no upload
python docs/spa/deploy.py --dry-run

# upload but skip CF invalidation (cache expires in 5 min)
python docs/spa/deploy.py --no-invalidate
```

After deploy the new build is live within seconds (invalidation finishes in
under a minute).

## What the SPA does

- Renders all three MDs with [`marked`](https://marked.js.org/) (GFM tables,
  syntax-highlighted code blocks via [`highlight.js`](https://highlightjs.org/)).
- Renders ```` ```mermaid ```` blocks with [`mermaid`](https://mermaid.js.org/)
  — this is the diagram that was blank in your previous MD viewer.
- Tabs across the top switch between the three docs; deep links work via
  `?tab=flow|services|errors`.
- Left sidebar TOC auto-builds from `<h2>`/`<h3>` headings of the active tab
  and follows the scroll position.
- Theme toggle (top right) flips dark / light.
- Print button (top right) pre-renders every tab and opens the print dialog —
  use this to export a single PDF of the full deck.
- Inter-doc links inside the markdown switch tabs instead of navigating away.

## Update flow

1. Edit any of the three `../Runbooks/PRODUCT_*.md` files.
2. `python docs/spa/deploy.py`.
3. Refresh the live URL — invalidation completes in <1 min.

## CDN dependencies (pinned)

| Lib | Version | Purpose |
|---|---|---|
| marked | 13.0.3 | Markdown → HTML |
| mermaid | 11.4.1 | Diagrams (`flowchart`, `sequenceDiagram`, etc.) |
| highlight.js | 11.10.0 | Code-block syntax highlighting |

If the customer requires no-CDN delivery (air-gapped review), copy these into
`docs/spa/vendor/` and patch the `<script src>`/`<link href>` to local paths
before deploying.
