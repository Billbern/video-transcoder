### 📄 DOCUMENT 3: UI/UX Specifications

**Design Philosophy**: "Developer-Clean". Dark-mode by default, high contrast, minimal clutter. Focus on state clarity.

#### 3.1 Core Views
1. **Dashboard (Home)**
   - **Hero Section**: Large, dashed-border drag-and-drop zone. Text: "Drop video here (Max 2GB, MP4/MOV/MKV)".
   - **Active Jobs List**: A table or card list below the upload zone showing:
     - Filename (truncated)
     - Target Preset (e.g., "720p")
     - Status Badge (Gray: Queued, Blue: Processing, Green: Completed, Red: Failed)
     - Action Button ("Download" or "Retry")

#### 3.2 Component States
- **UploadForm**: 
  - *Idle*: Dashed border, neutral color.
  - *Dragging*: Border turns primary color, background slightly highlighted.
  - *Uploading*: Progress bar (0-100%), cancel button visible.
- **VideoPlayer**: 
  - Only renders when status is `Completed`. Uses HTML5 `<video>` tag with a standard poster image (or generated thumbnail if Phase 2 is reached).

#### 3.3 Error Handling UX
- Never show raw stack traces to the user.
- Map backend error codes to human-readable messages:
  - `1001` → "Unsupported format. Please upload MP4, MOV, or MKV."
  - `1004` → "Storage quota exceeded. (Simulated)"