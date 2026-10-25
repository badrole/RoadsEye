# RoadsEye

Real-time vehicle detection, tracking, and traffic analytics for CCTV streams and video files. RoadsEye detects four vehicle classes (`car`, `bus`, `motorcycle`, `truck`), tracks each vehicle with ByteTrack, counts traffic by tripwire line or tracker ID, estimates speed, and stores every event in local SQLite with optional Google Sheets sync.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.4-06B6D4?style=flat&logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-FFCC00?style=flat)](https://docs.ultralytics.com)
[![ONNX Runtime](https://img.shields.io/badge/ONNX_Runtime-1.22-005CED?style=flat)](https://onnxruntime.ai)

---

## How It Works

Each video source runs through a 4-stage concurrent pipeline (`reader -> inference -> encoder -> pacer`):

1. `core/video_source.py` reads frames from local MP4 files, USB webcams, RTSP streams, or HLS (`.m3u8`) feeds. For remote HLS streams, a background prefetcher downloads `.ts` segments using 12 parallel HTTP byte-range connections so per-connection throttling on CCTV servers does not stall playback.
2. `core/detector.py` letterboxes frames to 640x640, runs YOLOv8s ONNX inference on CUDA (with CPU fallback), and filters boxes with NMS. It supports both custom 4-class traffic models and standard 80-class COCO models.
3. `core/tracker.py` maintains vehicle IDs across frames using ByteTrack (`supervision`).
4. `core/analytics.py` counts vehicles, calculates speed from pixel displacement, checks for traffic surges, stopped vehicles, and confidence drift, and outputs a `FrameMetrics` snapshot.
5. `api/pipeline_manager.py` streams annotated JPEG frames over `/ws/video`, broadcasts JSON metrics over `/ws/metrics`, writes confirmed vehicle events to `data/roadseye.db`, and syncs daily summaries to Google Sheets.

## Key Capabilities

- Switch between two counting modes (`line` and `id`) while the pipeline runs:
  - `line` mode counts vehicles when their bounding-box center crosses a named tripwire line.
  - `id` mode counts every tracked vehicle in frame once by tracker ID, with 5-frame majority-vote class refinement.
  - Switching modes carries over the daily WIB tally without double-counting vehicles already recorded.
- Add, drag, or delete counting lines directly over the live video canvas. Line updates apply immediately without resetting vehicle counts.
- Estimate vehicle speed in km/h from pixel displacement (`pixels_per_meter`) and trigger alerts for traffic surges (frame count above the 60-frame mean + 2 standard deviations), stopped vehicles (under 5 px movement for 30 frames), and confidence drift (30-frame rolling mean dropping 15% below the initial 100-frame baseline).
- Store confirmed vehicle crossings in `data/roadseye.db` (SQLite WAL). A background writer commits buffered events every second (or every 5 events) and flushes on pipeline stop or shutdown.
- Resume daily counts across restarts on the same calendar day (Asia/Jakarta, UTC+7) by reloading today's totals from `data/roadseye.db`, resetting at 00:00 WIB.
- Open `?view=database` from the dashboard to filter historical events by date and class, export Excel-ready UTF-8 CSV files, create online SQLite backups (`VACUUM INTO`), or delete specific dates from both SQLite and Google Sheets.
- Run a synthetic two-lane traffic stream by setting the source to `demo` when testing without a camera, GPU, or ONNX model file.

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Detection | YOLOv8s ONNX, `onnxruntime-gpu` 1.22 | GPU/CPU object detection |
| Tracking | `supervision` (ByteTrack) | Multi-object tracking across frames |
| Video I/O | OpenCV, `httpx` | Stream capture, parallel HLS segment prefetch, JPEG encoding |
| Backend | FastAPI, Uvicorn | REST endpoints and WebSocket streaming |
| Database | SQLite (`sqlite3` WAL + `aiosqlite`) | Event history (`roadseye.db`) and hourly aggregates (`traffic.db`) |
| Cloud Sync | Google Sheets API v4 | Automated 15-minute and on-stop reporting |
| Frontend | React 18, Vite 5, Tailwind CSS 3, Recharts | Live monitoring dashboard and database viewer |
| Testing | pytest, pytest-asyncio, httpx | Unit, database, and API test suites |

---

## Quick Start

### Windows (One-Click)

1. Run `start_app.bat`. On first launch it creates `venv/`, installs Python and Node dependencies, starts the FastAPI backend on port `8000` and Vite on port `3000`, and opens `http://localhost:3000`.
2. Run `stop_app.bat` to stop both servers.

Requirements: Python 3.10+ and Node.js 18+. For GPU acceleration on Windows, install NVIDIA driver 566+ (`onnxruntime-gpu==1.22.0` uses the bundled `nvidia-*-cu12` wheels).

### Manual Setup

```bash
# 1. Backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 2. Frontend (in a second terminal)
cd dashboard
npm install
npm run dev
```

Open `http://localhost:3000`, enter a video source (`0` for webcam, a local `.mp4` path, an `rtsp://` or `.m3u8` URL, or `demo`), choose `Per Line` or `Per ID`, and click **START PIPELINE**.

---

## Configuration

Copy `.env.example` to `.env` in the project root. `api/main.py` loads `.env` on startup.

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDEO_SOURCE` | unset | Source to auto-start on boot (`0`, file path, RTSP/HLS URL, or `demo`). Leave empty to start manually from the UI. |
| `MODEL_PATH` | `models/weights/yolov8s_traffic.onnx` | Path to the YOLOv8 ONNX model. |
| `CONFIDENCE_THRESHOLD` | `0.25` | Minimum detection confidence (`0.0` to `1.0`). |
| `IOU_THRESHOLD` | `0.45` | NMS IoU threshold (`0.0` to `1.0`). |
| `COUNT_MODE` | `line` | Default counting mode (`line` or `id`). |
| `COUNTING_LINES` | `[{"name":"line1","x1":0,"y1":380,"x2":1280,"y2":380}]` | Default counting lines as JSON. |
| `STREAM_DELAY_SECONDS` | `0` | Optional timeshift buffer (in seconds) for remote streams (`rtsp://`, `http://`, `https://`, `.m3u8`). |
| `CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins. |
| `ROADSEYE_DB_PATH` | `data/roadseye.db` | SQLite path for persistent vehicle events and sessions. |
| `DATABASE_PATH` | `data/traffic.db` | SQLite path for hourly aggregates. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | unset | Google Spreadsheet ID for cloud sync. Leave blank to disable sync. |
| `GOOGLE_SHEETS_CREDENTIALS_FILE` | `credentials.json` | Google service account JSON key path. |

### `POST /pipeline/start` Payload

| Field | Default | Description |
|-------|---------|-------------|
| `source` | `0` | Webcam index, file path, RTSP/HLS URL, or `demo`. |
| `confidence_threshold` | `0.25` | Detection confidence threshold (`0.0` to `1.0`). |
| `iou_threshold` | `0.45` | NMS IoU threshold (`0.0` to `1.0`). |
| `skip_frames` | `0` | Number of intermediate frames to reuse detections on (`0` = run inference on every frame). |
| `counting_lines` | `[]` | Array of `{name, x1, y1, x2, y2}`; falls back to active/default lines when empty. |
| `count_mode` | current mode | `"line"` or `"id"`. |
| `pixels_per_meter` | `10.0` | Calibration factor for speed estimation (`> 0`). |
| `loop` | `false` | Restart local video files at EOF. |
| `draw_annotations` | `true` | Draw bounding boxes, centroids, counting lines, and HUD on output frames. |
| `jpeg_quality` | `72` | Output JPEG quality (`1` to `100`). |

---

## API Endpoints

Full OpenAPI documentation is available at `http://localhost:8000/docs`.

### Pipeline & Streaming

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Server health and version check. |
| `/pipeline/start` | POST | Start or restart the pipeline. |
| `/pipeline/stop` | POST | Stop the pipeline, flush events to SQLite, and trigger Google Sheets sync. |
| `/pipeline/status` | GET | Current status, source, frame index, FPS, uptime, count mode, and lines. |
| `/pipeline/lines` | GET / POST | Read or update counting line coordinates on the fly. |
| `/pipeline/count-mode` | GET / POST | Read or switch counting mode (`line` or `id`) without losing the daily count. |
| `/ws/video` | WebSocket | Streams binary JPEG frames to the browser canvas. |
| `/ws/metrics` | WebSocket | Streams JSON `MetricsMessage` updates (counts, speed, alerts, tracks, crossings). |

### Analytics & Persistence

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analytics/events` | GET | Paginated vehicle events, filterable by `camera_id`, `vehicle_type`, `session_id`, `date_from`, and `date_to` (WIB). |
| `/analytics/events/per-minute` | GET | Zero-filled per-minute vehicle counts (WIB) for the timeline bar chart. |
| `/analytics/events/export.csv` | GET | Download filtered events as a UTF-8 BOM CSV file with WIB timestamps. |
| `/analytics/events/by-date` | DELETE | Delete all events for a WIB date (`YYYY-MM-DD`) from SQLite and Google Sheets. |
| `/analytics/history/summary` | GET | Total counts, class breakdown, hourly buckets, daily buckets, and peak hour (WIB). |
| `/analytics/history/backup` | POST | Create a safe online backup of `roadseye.db` using `VACUUM INTO`. |
| `/analytics/sessions` | GET | List recent processing sessions with start/stop timestamps. |
| `/analytics/sheets/status` | GET | Check Google Sheets configuration and spreadsheet URL. |
| `/analytics/sheets/sync` | POST | Trigger an immediate Google Sheets sync. |
| `/analytics/recent` | GET | Recent frames from the in-memory ring buffer (`10` to `3600` seconds). |

---

## Project Layout

```text
RoadsEye/
├── api/
│   ├── main.py               FastAPI app, lifespan, auto-start, CORS
│   ├── pipeline_manager.py   Singleton pipeline runner, queues, DB & Sheets hooks
│   ├── events_db.py          SQLite WAL event store, background writer, WIB queries
│   ├── database.py           In-memory ring buffer and hourly aggregate store
│   ├── sheets_sync.py        Google Sheets API v4 sync
│   ├── schemas.py            Pydantic v2 request and response schemas
│   └── routes/               health.py, stream.py, analytics.py
├── core/
│   ├── pipeline.py           4-stage async pipeline, drawing, and CLI runner
│   ├── video_source.py       VideoCapture wrapper and parallel HLS prefetcher
│   ├── detector.py           YOLOv8 ONNX Runtime inference and NMS
│   ├── tracker.py            ByteTrack wrapper
│   ├── analytics.py          Line counter, ID counter, speed, anomaly, and drift
│   └── demo_generator.py     Synthetic traffic generator for demo mode
├── dashboard/
│   ├── src/App.jsx           Main monitoring layout and database viewer router
│   ├── src/components/       VideoFeed, LineEditor, StatsCards, charts, DatabaseViewer
│   ├── src/hooks/            useWebSocket.js (video ImageBitmap + throttled metrics)
│   └── vite.config.js        Vite dev server and /api proxy
├── models/weights/           ONNX model directory (yolov8s_traffic.onnx)
├── notebooks/                Data exploration and YOLOv8 training notebooks
├── tests/                    test_core.py, test_api.py, test_events.py, test_sheets.py
├── start_app.bat             Windows one-click launcher
├── stop_app.bat              Windows service stopper
└── requirements.txt          Python dependencies
```

---

## Testing

```bash
venv\Scripts\activate
pytest
```

All tests redirect SQLite paths to temporary files and mock the ONNX detector when testing core logic, so running `pytest` never modifies `data/roadseye.db`.

