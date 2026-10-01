### 📄 DOCUMENT 2: User Stories (with Acceptance Criteria)


**US-1: Resumable Video Upload**
- *As an* end-user, *I want to* upload large video files via a drag-and-drop interface, *so that* I can convert my local media without worrying about network interruptions.
- *Acceptance Criteria*:
  - UI shows a progress bar during upload.
  - Backend generates a presigned URL; the frontend uploads directly to Bucket A.
  - Files >100MB are handled gracefully (chunking simulated or implemented).
  - Invalid file types (e.g., `.exe` renamed to `.mp4`) are rejected with a clear error.

**US-2: Asynchronous Transcoding Request**
- *As a* user, *I want to* select a target resolution (e.g., 720p) and trigger processing, *so that* the system handles the heavy lifting without blocking my browser.
- *Acceptance Criteria*:
  - Clicking "Transcode" immediately returns a `job_id`.
  - The job is queued in Redis/RabbitMQ.
  - The UI transitions to a "Processing" state.

**US-3: Job Status Tracking**
- *As a* user, *I want to* see the current status of my video (Queued, Processing, Completed, Failed), *so that* I know when my file is ready.
- *Acceptance Criteria*:
  - Frontend polls `/api/jobs/{id}` every 3 seconds (or uses SSE).
  - Status changes are reflected in the UI within 5 seconds of backend state change.
  - Failed jobs display a user-friendly error message (e.g., "Corrupted source file").

**US-4: Secure Output Delivery**
- *As a* user, *I want to* download or stream my processed video, *so that* I can use the converted file.
- *Acceptance Criteria*:
  - Processed files are stored in Bucket B.
  - Download links are generated as time-limited presigned URLs (valid for 1 hour) to prevent hotlinking and control egress.
