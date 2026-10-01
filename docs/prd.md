
### 📄 DOCUMENT 1: Product Requirements Document (PRD)

**Product Name**: VideoTranscode Pro (Portfolio Edition)  
**Version**: 1.0  
**Author**: Moorben  
**Objective**: To design and implement a resilient, cost-optimized, asynchronous video transcoding platform that demonstrates mastery of distributed systems, cloud-native patterns, and pragmatic infrastructure choices.

#### 1.1 Problem Statement
Video transcoding is computationally expensive and blocking. Naive implementations freeze the UI, lose state on network drops, and incur massive cloud egress fees. This project solves that by decoupling ingestion, processing, and delivery using a message-queue-driven architecture optimized for low-cost infrastructure.

#### 1.2 Target Audience
- **Primary**: Technical recruiters, Staff/Principal Engineers reviewing the portfolio.
- **Secondary**: End-users (simulated) who need reliable, format-agnostic video conversion without local hardware requirements.

#### 1.3 Core Functional Requirements
1. **Ingestion**: Accept video uploads up to 2GB via presigned URLs (bypassing the backend to save bandwidth).
2. **Processing**: Asynchronously transcode videos into predefined presets (e.g., 720p MP4) using FFmpeg.
3. **Storage Routing**: Utilize two distinct object storage buckets:
   - **Bucket A (Ingest/Raw)**: Low-cost, infrequent access tier for original uploads.
   - **Bucket B (Egress/Processed)**: Optimized for public read/CDN delivery of final outputs.
4. **Observability**: Provide real-time (or near real-time via polling/SSE) status updates to the client.

#### 1.4 Non-Functional Requirements (The "Portfolio" Differentiators)
- **Cost Efficiency**: Zero egress fees from the processing stage; leverage free-tier compatible S3 APIs (e.g., Cloudflare R2, Backblaze B2).
- **Resilience**: Failed FFmpeg jobs must not crash the worker; they must be logged, marked as `failed`, and routed to a dead-letter concept.
- **Security**: All uploads validated by MIME type and magic bytes, not just file extensions.
- **Maintainability**: 80%+ test coverage on core business logic; fully containerized via Docker Compose.


