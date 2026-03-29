# AptTrack

Static GitHub Pages tracker for Avalon Princeton Circle 1 bed / 1 bath listings.

## What it does

- Scrapes the Avalon Princeton Circle page
- Keeps only `1 bed / 1 bath` units
- Stores generated JSON in `data/latest.json` and `data/history.json`
- Shows current listings, previous prices, and historic min/max prices on GitHub Pages

## Update schedule

GitHub Actions checks every hour, but only writes new data when the local time in `America/New_York` is:

- `9:00 AM`
- `5:00 PM`

This keeps the intended schedule aligned to Eastern Time through daylight saving changes.

## Files

- `index.html`: static site for GitHub Pages
- `scripts/update_data.py`: scraper and history generator
- `.github/workflows/update-data.yml`: scheduled data refresh
- `.github/workflows/deploy-pages.yml`: Pages deployment

## Local run

```bash
python scripts/update_data.py
python -m http.server 8000
```

Then open `http://127.0.0.1:8000`.
