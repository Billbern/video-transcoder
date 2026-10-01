# VideoTranscode Pro

> Portfolio-grade asynchronous video transcoding platform built to the spec in `docs/`.

## Status

| Sprint | Scope                                      | State         |
| ------ | ------------------------------------------ | ------------- |
| 1      | Core loop: API + worker + FFmpeg + tests   | ✅ Done       |
| 2      | Storage hardening, presigned URLs, cleanup | ✅ Done       |
| 3      | Frontend (React + TS), polish, E2E         | Not started   |

## Sprint 2 highlights

* **Magic-byte preflight** — the worker does a ranged GET against Bucket A before downloading the full file; bad-magic uploads never enter FFmpeg.
* **Retry classification** — `StorageError` is transient (Celery auto-retry with backoff, max 3); FFmpeg failures are permanent.
* **Structured stderr logs** — full FFmpeg stderr is written to `/tmp/transcoder/ffmpeg-logs/{job_id}.log`; the `Job.ffmpeg_stderr` column stores a preview.
* **Celery Beat cleanup cron** — `cleanup_runner.run_once()` runs every 15 minutes, deleting `UPLOADING` jobs older than 1 hour and reporting `zombies_found` / `objects_deleted` / `objects_failed`.
* **Testcontainers scaffolding** — `tests/integration/test_testcontainers_pipeline.py` ships the full API → DB → Celery broker path test, gated on Docker availability so it auto-skips when Docker is missing.

## Repository layout

```
.
├── backend/                  FastAPI + Celery worker (Sprints 1-2)
├── frontend/                 React app (Sprint 3 placeholder)
├── docker-compose.dev.yml    Local dev stack (Postgres + Redis + MinIO + API + worker + beat)
├── docker-compose.yml        Production stack (GHCR images, joined to NPM network)
├── .env.example              Production env template
├── pyproject.toml            Ruff/Black/Mypy/Pytest config (repo-wide)
├── .pre-commit-config.yaml   Local hooks
└── docs/
    ├── nginx-proxy-manager.md  How to attach NPM to the `videotranscoder` network
    └── ...                     Source-of-truth specs (PRD, stories, tests, DoD)
```

## Quick start

```bash
docker compose -f docker-compose.dev.yml up --build
```

Then open:
- API docs: <http://localhost:8000/docs>
- MinIO:   <http://localhost:9001> (`minioadmin`/`minioadmin`)

## Running the tests

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. pytest --cov=app
```

## Documentation

See [`docs/`](./docs) for:
- `prd.md` — Product Requirements
- `user_stories.md` — Acceptance criteria per feature
- `implementation.md` — Sprint plan
- `testing_strategies.md` — Test taxonomy
- `ui_ux_spec.md` — UI design language
- `coding_standards.md` — Formatter / linter rules
- `definition_of_done.md` — Definition of Done
- `deployment.md` — Hetzner / NPM / GHCR plan
- `nginx-proxy-manager.md` — Step-by-step NPM proxy-host setup

See [`backend/README.md`](./backend/README.md) for the API contract, error codes, and environment reference.
