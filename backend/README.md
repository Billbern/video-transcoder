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

### Error codes (mirrored on the frontend)

| Code | Meaning                                |
| ---- | -------------------------------------- |
| 1001 | Unsupported format (bad MIME/ext)      |
| 1002 | Corrupted source file                  |
| 1003 | FFmpeg failed                          |
| 1004 | Storage quota exceeded (simulated)     |
| 1005 | Storage backend unavailable            |
| 1999 | Unclassified internal error            |

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
