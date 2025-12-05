# Ideeza Analytics API

[![CI](https://github.com/vyaesop/ideeza_analytics_api/actions/workflows/ci.yml/badge.svg)](https://github.com/vyaesop/ideeza_analytics_api/actions/workflows/ci.yml)

Professional Django analytics service.

Overview
--------
This repository demonstrates a small but realistic analytics pipeline built with Django and PostgreSQL. It shows:

- event ingestion via a `BlogView` model
- daily pre aggregation into `DailyAnalyticsSummary` for fast queries
- REST endpoints for grouped analytics, top items, and performance over time
- management commands for seeding and pre calculation
- tests, CI, and Docker based deployment examples

Goals
-----
- Provide a production minded pre aggregation design to avoid expensive real time scans
- Keep API surfaces simple and cache/aggregation friendly
- Ship tests and CI so the work can be reviewed and validated quickly

Quick start (development)
-------------------------
These commands assume PowerShell on Windows and the repository root as the working directory.

1) Create a virtualenv and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) Run migrations and an initial seed (local SQLite recommended for quick dev):

```powershell
$env:DATABASE_URL='sqlite:///db.sqlite3'
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py loaddata seed_data || true
.\.venv\Scripts\python.exe manage.py precalculate_stats
```

3) Run the test suite (fast, uses in memory SQLite via `ideeza.test_settings`):

```powershell
.\.venv\Scripts\python.exe manage.py test -v 2 --settings=ideeza.test_settings
```

Important: `precalculate_stats` populates the `DailyAnalyticsSummary` pre aggregates (and per day `blog_ids`) used by the fast endpoints. Run it after seeding data.

Note: In local/dev setups without Redis or Postgres advisory locks, `precalculate_stats` will log a warning and proceed (safe for small datasets). In production you should ensure a cache or Postgres is available so the command can acquire a lock to prevent concurrent runs.

API (high level)
-----------------
- `POST /api/analytics/blog-views/country/` — grouped analytics by country (fast pre aggregated path)
- `POST /api/analytics/top/blog/` — top blogs
- `POST /api/analytics/performance/` — performance/time series

All endpoints accept JSON payloads with filtering parameters (date range, year, country_codes, exclude_country_codes, author_username). Responses use a simple structure: `x` (group), `y` (unique blogs), `z` (total views).

Design & implementation notes
-----------------------------
- Pre-aggregation: `DailyAnalyticsSummary` stores daily `total_views`, `unique_blogs`, and `blog_ids` (list of blog ids seen that day). For range queries the service unions `blog_ids` across days to compute exact distinct counts.
- Scalability: `blog_ids` is exact but grows with cardinality — for very large datasets switch to HyperLogLog sketches for approximate distinct counts.
- Tradeoffs: pre aggregates reduce query time at the cost of storage and the need to run incremental pre calculation jobs.

Developer & maintenance
-----------------------
- Tests: `analytics/tests.py` contains unit/regression tests. Use the `ideeza.test_settings` to avoid contacting external services.
- CI: a GitHub Actions workflow runs the linter and tests. The badge above links to the workflow file.
- Formatting: the repo uses `ruff` and `black` — run `python -m ruff format .` and `python -m black .` in the repo virtualenv.

Deployment
----------
- A `Dockerfile` and `docker-compose.yml` are included as deployment examples. For production, set environment variables (e.g. `DATABASE_URL`, `REDIS_URL`) via your platform or secrets manager — do not commit secrets.

Notes about the repository rewrite
---------------------------------
This repository has been sanitized and history rewritten for demonstration. Backup branches are available on the remote (for recovery) if needed.

Contributing
------------
Please open issues or PRs for improvements. See `CONTRIBUTING.md` for guidance.

License
-------
This repository is provided as an example for review and learning purposes.

Contact
-------
Use GitHub issues on the repository for questions about the sample project.



---

## Architecture

- **Models:** `BlogView` (fact table) + `DailyAnalyticsSummary` (pre calculated)
- **Optimization:** Composite indexes, `select_related()`, Redis caching (15 min)
- **Security:** JWT authentication, typed serializers

---

## Commands

| Action | Docker Command | Makefile (optional) |
|--------|----------------|---------------------|
| Start | `docker-compose up -d --build` | `make up` |
| Migrate | `docker-compose exec web python manage.py migrate` | `make migrate` |
| Seed data | `docker-compose exec web python manage.py seed_data` | `make seed` |
| Pre-calculate | `docker-compose exec web python manage.py precalculate_stats` | `make precalc` |
| Run tests | `docker-compose exec web python manage.py test` | `make test` |

### Developer helpers

Lint and type check locally:

```bash
pip install -r dev-requirements.txt
ruff check .
mypy src --ignore-missing-imports
```
| View logs | `docker-compose logs -f` | `make logs` |
| Stop | `docker-compose down` | `make down` |

---

## Feature flags and runtime toggles

- `IDEEZA_PERFORMANCE_X_METRIC` (env)
	- `viewed` (default) — show distinct blogs viewed per period
	- `created` — show blogs created per period
- `IDEEZA_USE_HLL` (env)
	- `true`/`1` to enable writing HyperLogLog (HLL) structures to Redis during precalc.
	- Read-path uses HLL when available; falls back to exact counts for correctness.
- `STATSD_HOST` / `STATSD_PORT` (env)
	- Optional StatsD endpoint for precalc timing and counters.
- `IDEEZA_API_OPEN` (env)
	- `true` (default) keeps API endpoints open (`AllowAny`) for local/dev convenience.
	- Set to `false` in production to require authentication (`IsAuthenticated`).

## CI Integration

A GitHub Actions workflow `./github/workflows/integration.yml` is included to run migrations and a dry-run of `precalculate_stats` against Postgres+Redis.


## Tech Stack

- Python 3.11 / Django 4.2 / DRF
- PostgreSQL 15 / Redis 7
- Docker / Swagger (drf-yasg)
