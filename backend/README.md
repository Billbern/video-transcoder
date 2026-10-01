# VideoTranscode Pro — Backend (FastAPI)

This is the FastAPI + Celery worker service for the VideoTranscode Pro platform.
For the full system design see `../docs/`.

## Layout

```
backend/
├── app/
│   ├── api/jobs.py              # FastAPI routes: /api/initiate, /api/transcode, /api/jobs
│   ├── config.py                # Pydantic settings (env-driven)
│   ├── db/                      # SQLAlchemy async engine + declarative base
│   ├── models/job.py            # Job ORM model + status enum + UUID column
│   ├── schemas.py               # Pydantic request/response models
│   ├── services/
│   │   ├── ffmpeg_processor.py  # Builds argv + runs FFmpeg subprocess
│   │   ├── formats.py           # Allowed MIME types, magic-byte signatures, presets
│   │   ├── job_repository.py    # All DB read/write against `jobs`
│   │   └── storage.py           # boto3 wrapper for two S3 buckets
│   ├── workers/
│   │   ├── celery_app.py        # Celery instance (Redis broker)
│   │   ├── tasks.py             # `transcode_job` task body
│   │   └── cleanup_runner.py    # Periodic zombie-upload GC
│   └── main.py                  # FastAPI app factory + lifespan
├── tests/
│   ├── unit/                    # Pure logic, mocked externals
│   └── integration/             # End-to-end via ASGI in-process
├── Dockerfile                   # Production image (FFmpeg + Python 3.12)
├── requirements.txt
└── requirements-dev.txt
```

## Worker resilience

The Celery worker (`transcode_job`) implements the resilience contract from
`docs/prd.md`:

* **Magic-byte validation** — a ranged `GET` against Bucket A reads the first
  32 bytes *before* downloading the source. Files that don't match a known
  container (MP4/MOV/Matroska) are marked `FAILED` with code `1001`
  (`UNSUPPORTED_FORMAT`) and never enter the FFmpeg pipeline.
* **Transient vs. permanent errors** — `StorageError` is wrapped in a
  `TransientError` and retried via `self.retry(countdown=...)` up to
  `max_retries=3`. Only after the retries are exhausted is the job marked
  `FAILED` with code `1005` (`STORAGE_UNAVAILABLE`). FFmpeg failures are
  permanent and never trigger retries (per the PRD: we don't want to burn CPU
  on a corrupted source).
* **Structured stderr capture** — the full FFmpeg stderr is written to
  `/tmp/transcoder/ffmpeg-logs/{job_id}.log`; the `Job.ffmpeg_stderr` column
  stores a preview (header line + first 2000 bytes) so operators can grep the
  full log on disk.
* **Idempotent cleanup cron** — `cleanup_runner.run_once()` is registered as
  a Celery Beat task (`cleanup-zombie-uploads`, every 15 minutes) that
  deletes `UPLOADING` jobs older than 1 hour. Reports `zombies_found`,
  `objects_deleted`, and `objects_failed` as metrics-friendly counters.

## Error codes

Mirrored on the frontend (`docs/ui_ux_spec.md` §3.3).

| Code | Meaning                          | Marked `FAILED`? | Retryable? |
| ---- | -------------------------------- | ---------------- | ---------- |
| 1001 | Unsupported format (bad magic)   | Yes              | No         |
| 1003 | FFmpeg failed                    | Yes              | No         |
| 1004 | Storage quota exceeded (sim.)    | Yes              | No         |
| 1005 | Storage backend unavailable      | After retries    | Yes        |
| 1999 | Unclassified internal error      | Yes              | No         |

## Quick start (with Docker)

```bash
# From the repo root:
docker compose -f docker-compose.dev.yml up --build
```

This brings up Postgres, Redis, MinIO (+ bucket init), the API at
`http://localhost:8000`, and a Celery worker.

* API docs: <http://localhost:8000/docs>
* MinIO console: <http://localhost:9001> (user/pass: `minioadmin`/`minioadmin`)

## Local development (without Docker)

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

# Run the API (SQLite + in-memory mode for quick hacking):
USE_SQLITE=1 \
  PYTHONPATH=. \
  uvicorn app.main:app --reload

# Run the worker (eager mode in tests; live Redis in dev):
celery -A app.workers.celery_app worker --loglevel=INFO
```

## Tests

```bash
cd backend
. .venv/bin/activate
PYTHONPATH=. pytest                     # unit + integration, ~25 s
PYTHONPATH=. pytest --cov=app           # with coverage report
```

## Lint & type-check

```bash
ruff check app tests          # lint (black + flake8 + isort + bugbear + …)
ruff format app tests         # formatter (black-compatible)
black app tests               # alternative formatter
mypy --config-file ../pyproject.toml app   # strict mode (with third-party overrides)
```

## API contract

| Method | Path                | Purpose                                           |
| ------ | ------------------- | ------------------------------------------------- |
| POST   | `/api/initiate`     | Validate, persist UPLOADING Job, return presigned PUT URL |
| POST   | `/api/transcode`    | Verify upload, transition to QUEUED, enqueue Celery task |
| GET    | `/api/jobs`         | List recent jobs (newest first)                   |
| GET    | `/api/jobs/{id}`    | Get one job; include presigned GET URL if COMPLETED |
| GET    | `/health`           | Health probe                                      |
| GET    | `/docs`             | Swagger UI (FastAPI auto-docs)                    |

## Environment variables

| Name                       | Default                                                                  | Notes                              |
| -------------------------- | ------------------------------------------------------------------------ | ---------------------------------- |
| `DATABASE_URL`             | `postgresql+asyncpg://vt:vt@postgres:5432/videotranscoder`              | Async SQLAlchemy DSN               |
| `SYNC_DATABASE_URL`        | `postgresql+psycopg2://vt:vt@postgres:5432/videotranscoder`              | Sync DSN for the Celery worker     |
| `REDIS_URL`                | `redis://redis:6379/0`                                                   | Celery broker + result backend     |
| `S3_ENDPOINT`              | `http://localhost:9000`                                                  | MinIO locally; `None` in prod for AWS |
| `S3_BUCKET_INGEST`         | `raw-videos`                                                             | Bucket A — raw uploads             |
| `S3_BUCKET_OUTPUT`         | `processed-videos`                                                       | Bucket B — processed outputs       |
| `S3_PRESIGN_EXPIRY_SECONDS`| `3600`                                                                   | 1-hour presigned GET URLs          |
| `MAX_UPLOAD_BYTES`         | `2147483648`                                                             | 2 GiB hard cap                     |
| `USE_SQLITE`               | `0`                                                                      | Set to `1` for local SQLite        |
| `LOG_LEVEL`                | `INFO`                                                                   | Standard Python log level          |
| `WORKDIR`                  | `/tmp/transcoder`                                                        | Local working dir on the worker    |
