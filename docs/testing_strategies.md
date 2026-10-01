### 📄 DOCUMENT 8: Testing Strategies

**Philosophy**: Test behavior, not implementation. Prioritize high ROI tests.

1.  **Unit Testing (Python - `pytest`)**:
    *   *Target*: `FFmpegProcessor`, `JobRepository`, utility functions.
    *   *Method*: Mock external dependencies (S3, DB, subprocess). Verify that the correct FFmpeg arguments are generated for each preset.
2.  **Integration Testing (Python - `pytest` + `Testcontainers`)**:
    *   *Target*: API endpoints, Database interactions.
    *   *Method*: Spin up real, ephemeral Postgres and Redis containers during the test run. Verify that an API call to `/transcode` actually creates a record in the DB and pushes a message to Redis.
3.  **End-to-End Testing (React - `Playwright` or `Cypress`)**:
    *   *Target*: The critical user path (Upload -> Wait -> Download).
    *   *Method*: Automate the browser to upload a tiny 1MB test video, wait for the status to change to "Completed", and verify the download link appears.
