### 📄 DOCUMENT 7: Implementation Plan

**Strategy**: Iterative delivery. We build the "Walking Skeleton" first, then harden it.

*   **Sprint 1: The Core Loop (Days 1-3)**
    *   Setup Docker Compose (Postgres, Redis, MinIO).
    *   Implement FastAPI endpoints for `/initiate` and `/transcode`.
    *   Build the `FFmpegProcessor` and the Celery Worker.
    *   Implement the `JobRepository` (Database handler).
*   **Sprint 2: Storage & Resilience (Days 4-6)**
    *   Integrate `boto3` for presigned URL generation (Bucket A) and output uploading (Bucket B).
    *   Implement the server-side cleanup cron for zombie uploads.
    *   Add robust error handling and FFmpeg stderr logging to the DB.
*   **Sprint 3: Frontend & Polish (Days 7-10)**
    *   Build React UI (Upload form, Job list, Video player).
    *   Implement polling/SSE for real-time status updates.
    *   Write the README, architecture diagrams (using Excalidraw/Mermaid), and finalize portfolio presentation.
