# sl-tiktok

Why 7-Eleven staff on TikTok say they want to quit — from search results to an
executive dashboard.

## Setup

```bash
.venv/bin/pip install -e .
```

Editable install, so `sltiktok` imports from `src/` without `PYTHONPATH`.

## Pipeline

Five stages. Each runs on its own and picks up the last one's output from `out/`.

| Stage | Command | Produces |
|---|---|---|
| Discover | `python -m sltiktok.discover` | accounts + `video_url` (Playwright, Google Sheet) |
| Enrich | `python -m sltiktok.enrich` | views/likes/shares, `uploaded_at`, raw comments (Apify) |
| Analyze | `python -m sltiktok.analyze` | transcript, on-screen text, themes, intent (Gemini) |
| OCR | `python -m sltiktok.ocr` | cover-frame text — fallback only, where Analyze failed |
| Dashboard | `python -m sltiktok.dashboard` | `web/data.js` |

Prefix each with `.venv/bin/`. Most stages cost money — run `--estimate` first.

## Dashboard

```bash
xdg-open web/index.html
```

Opens straight from `file://`; no server, no build step.

## Tests

```bash
for t in tests/test_*.py; do .venv/bin/python "$t"; done   # 59 checks
```

No pytest on this machine — each file runs standalone via `__main__`.

## Layout

```
src/sltiktok/   pipeline stages
tests/          one suite per stage
tools/          spotcheck (model vs human labels), push_sheet
web/            the deliverable dashboard
docs/           STEP3/4/5 runbooks, design specs
out/            generated data (gitignored)
```

Docs: [STEP3](docs/STEP3.md) comments · [STEP4](docs/STEP4.md) analysis ·
[STEP5](docs/STEP5.md) dashboard.
