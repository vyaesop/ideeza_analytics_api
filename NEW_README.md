# Ideeza Analytics API

Professional Django analytics service example.

This repository contains a small Django app that demonstrates:

- Event-based analytics via a `BlogView` model.
- Daily pre aggregation into `DailyAnalyticsSummary` for fast queries.
- APIs for grouped analytics, top items, and performance over time.
- Tests, CI, linting, and deployment configuration (Docker).

Quick start (development):

1. Create a Python virtualenv and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run migrations and seed minimal data (local SQLite recommended):

```powershell
$env:DATABASE_URL='sqlite:///db.sqlite3'
Push-Location src
.\..venv\Scripts\python.exe manage.py migrate
.\..venv\Scripts\python.exe manage.py loaddata seed_data
Pop-Location
```

3. Run tests:

```powershell
Push-Location src
.\..venv\Scripts\python.exe manage.py test -v 2
Pop-Location
```

Design notes:
- Pre-aggregation stores daily summaries and per-day `blog_ids` so range distinct counts can be computed by unioning per-day values.
- For very large datasets, consider HyperLogLog sketches for approximate distinct counts.

License & contributing
- See `CONTRIBUTING.md` for contribution guidance.

Contact
- For project setup questions, use the repository issues on GitHub.
