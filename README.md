# RoadsEye

Real-time vehicle detection, tracking and traffic analytics for CCTV feeds. RoadsEye classifies vehicles, tracks them across frames, counts them across configurable counting lines, estimates speed, and persists every confirmed crossing to a durable local database with optional Google Sheets reporting.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.4-06B6D4?style=flat&logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-FFCC00?style=flat)](https://docs.ultralytics.com)
[![ONNX Runtime](https://img.shields.io/badge/ONNX_Runtime-1.22-005CED?style=flat)](https://onnxruntime.ai)

---

## Overview

Every processed frame moves through a fixed chain:

```
video source -> detector (YOLOv8s ONNX) -> tracker (ByteTrack) -> analytics -> API -> dashboard
```

- **Detector** letterboxes the frame, runs ONNX inference (CUDA provider when available, CPU otherwise) and applies NMS in Python. Both the 4-class traffic export and an 80-class COCO export are supported: COCO detections are mapped onto the four traffic classes and rejected when another class outscores the vehicle interpretation.
- **Tracker** assigns stable IDs across frames using ByteTrack via `supervision`.
- **Analytics** counts line crossings, estimates speed from pixel displacement, raises anomaly alerts, monitors detection-confidence drift and emits one metrics snapshot per frame.
- **API** fans frames out to WebSocket subscribers and persists confirmed crossings through a buffered background writer.
- **Dashboard** renders the live JPEG stream, KPI strip, charts, the interactive counting-line editor and the persistent history panels.

## Features

- Real-time detection and tracking of cars, buses, motorcycles and trucks.
- Multiple named counting lines, editable by dragging endpoints directly on the live video. Edits apply without restarting the pipeline and preserve accumulated counts.
- Speed estimation from pixel displacement and a pixels-per-metre calibration factor supplied per start request.
- Anomaly alerts: volume surge (current frame count above the 60-frame mean plus 2 standard deviations), stopped vehicle (fewer than 5 px of movement across 30 consecutive frames) and detection-confidence drift (rolling 30-frame mean falling 15 percent below the baseline locked over the first 100 frames).
- Demo mode: a synthetic two-lane scene with no ONNX model, no camera and no GPU.
- Durable event history in SQLite (WAL), buffered writes, idempotent inserts and safe online backup. History survives browser refreshes, pipeline stops and backend restarts.
- Per-day purge that removes a day from the database and from the Google Sheet.
- CSV export of filtered events, WIB timestamps and Excel-ready UTF-8 output.
- Optional Google Sheets synchronisation: raw events plus a formatted daily summary with donut charts.
- Buffered playback for remote live streams via `STREAM_DELAY_SECONDS`, with a pre-roll progress indicator and counting arms once playback reaches the live edge.
- Multiple WebSocket clients may subscribe to video and metrics simultaneously.

---

## Tech Stack

| Layer | Technology | Role |
|-------|------------|------|
| Inference | YOLOv8s exported to ONNX, `onnxruntime-gpu` 1.22 | Vehicle detection per frame |
| Tracking | `supervision` (ByteTrack) | Stable IDs across frames |
| Video | OpenCV | Capture, resize, annotation, JPEG encoding |
| Analytics | NumPy | Counting, speed, anomaly and drift thresholds |
| Backend | FastAPI, Uvicorn | REST API and WebSockets |
| Storage | SQLite: `aiosqlite` for aggregates, stdlib `sqlite3` (WAL) for events | Metric store and durable event history |
| Frontend | React 18, Vite 5 | Dashboard single-page app |
| Charts | Recharts | Volume, composition and history charts |
| Styling | Tailwind CSS 3 | Asphalt and lane-marking design tokens |
| Sheets | Google Sheets API v4 | Cloud reporting |
| Training | PyTorch, Ultralytics | Fine-tuning and ONNX export |
| Testing | pytest, pytest-asyncio, httpx | Unit and API coverage |
| Deployment | Docker Compose | Backend plus Nginx dashboard |

## Architecture

```
CCTV / MP4 / RTSP / demo
        |
        v
  core/pipeline.py      orchestration and frame pacing
   core/detector.py     YOLOv8s ONNX inference
   core/tracker.py      ByteTrack IDs
   core/analytics.py    counting, speed, alerts
        |
        v
  api/                  FastAPI: REST + WebSocket fan-out, event writer, Sheets sync
        |
        v
  dashboard/            React console + separate database viewer window
```

## Getting Started

### Windows, one click

1. Double-click `start_app.bat`. It creates `venv/`, installs Python and Node dependencies on first run, starts the backend on port 8000 and the Vite dev server on port 3000, then opens the dashboard.
2. Stop everything with `stop_app.bat` or Ctrl+C in each terminal.

Requires Python 3.10+ and Node.js 18+. An NVIDIA GPU with CUDA 12 is optional; without it inference falls back to CPU.

### Manual

```bash
# Backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Dashboard
cd dashboard
npm install
npm run dev            # http://localhost:3000
```

Open `http://localhost:3000`, enter a video source in the control panel (a webcam index, a file path, an RTSP URL, or `demo`) and press Start. The Vite dev server proxies `/api` to the backend, including WebSocket upgrades.

### Docker

```bash
docker compose up --build
# API and docs : http://localhost:8000/docs
# Dashboard    : http://localhost:80
```

The compose file declares an NVIDIA device reservation and mounts `models/weights`, `data/sample_videos`, `db_data` for aggregate storage and `roadseye_events` for event history. The bundled image installs CPU `onnxruntime`; install `onnxruntime-gpu` in the image to actually use the reserved GPU.

---

## Configuration

Settings come from the environment. `api/main.py` loads `.env` from the project root at import time, so copy `.env.example` to `.env` and edit it there.

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDEO_SOURCE` | unset | Auto-start source on boot: file path, webcam index, RTSP URL or `demo`. The pipeline does not start automatically when unset. |
| `MODEL_PATH` | `models/weights/yolov8s_traffic.onnx` | ONNX weights. |
| `CONFIDENCE_THRESHOLD` | `0.25` on env auto-start, `0.35` when started over the API | Detection confidence floor. |
| `IOU_THRESHOLD` | `0.45` | NMS IoU threshold. |
| `COUNTING_LINES` | one horizontal line at y=380 on a 1280x720 frame | Default counting lines, JSON array of `{name,x1,y1,x2,y2}`. |
| `STREAM_DELAY_SECONDS` | `0` | Playback delay for remote live sources (`http`, `https`, `rtsp`, `.m3u8`) only. Local files, webcams and `demo` stream immediately. |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. |
| `DATABASE_PATH` | `data/traffic.db` | SQLite file for hourly aggregates. |
| `ROADSEYE_DB_PATH` | `data/roadseye.db` | SQLite file for persistent event history. Point this at a backed-up location in production. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | unset | Target spreadsheet. Sync is skipped when unset or when the credentials file is missing. |
| `GOOGLE_SHEETS_CREDENTIALS_FILE` | `credentials.json` | Service-account key, resolved relative to the project root when not absolute. |

Per-run settings are supplied in the `POST /pipeline/start` body instead of the environment:

| Field | Default | Description |
|-------|---------|-------------|
| `source` | `0` | File path, webcam index, RTSP URL or `demo`. |
| `confidence_threshold` | `0.35` | Detection confidence floor, 0 to 1. |
| `iou_threshold` | `0.45` | NMS IoU threshold, 0 to 1. |
| `counting_lines` | `[]` | Counting lines for this run; falls back to `COUNTING_LINES`. |
| `pixels_per_meter` | `10.0` | Speed calibration. Must be greater than 0. |
| `loop` | `false` | Loop local files instead of stopping at the end. |
| `draw_annotations` | `true` | Burn boxes, line labels and the HUD into the streamed frames. |
| `jpeg_quality` | `80` | JPEG quality of the video stream, 1 to 100. |
| `skip_frames` | `0` | Used by the standalone CLI runner only; the live WebSocket loop always processes the newest frame. |

---

## API Reference

Interactive playground: `http://localhost:8000/docs`.

### Pipeline

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness check. |
| `/pipeline/start` | POST | Start or restart the pipeline with a run configuration. |
| `/pipeline/stop` | POST | Stop the pipeline, flush buffered events and close the session. |
| `/pipeline/status` | GET | Running state, source, frame index, uptime, FPS, pre-roll percentage and active lines. |
| `/pipeline/lines` | GET | Current counting-line geometry, for editor hydration. |
| `/pipeline/lines` | POST | Replace counting lines live; accumulated counts are preserved. |

### Streaming

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ws/video` | WebSocket | Binary JPEG frames as they are produced. Text `{"type":"ping"}` messages keep the socket alive during stalls. |
| `/ws/metrics` | WebSocket | One JSON metrics message per processed frame: counts, speed, alerts, tracks and this frame's crossings. |

### Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/analytics/recent` | GET | Per-frame metrics from the last `window` seconds of the ring buffer, 10 to 3600. |
| `/analytics/events` | GET | Paginated event history, filterable by camera, vehicle class, session and WIB date range. |
| `/analytics/events/per-minute` | GET | Vehicles per minute in WIB, zero-filled, for the timeline chart. |
| `/analytics/events/export.csv` | GET | Filtered events as a CSV download with WIB timestamps. |
| `/analytics/events/by-date` | DELETE | Purge one WIB calendar day from the database and from the Google Sheet. |
| `/analytics/history/summary` | GET | Cumulative totals, per-hour and per-day buckets computed from persisted events. |
| `/analytics/history/backup` | POST | Online backup of the events database via `VACUUM INTO`. |
| `/analytics/sessions` | GET | Processing sessions with start, end and status. |
| `/analytics/sheets/status` | GET | Google Sheets configuration and target URL. |
| `/analytics/sheets/sync` | POST | Trigger an immediate synchronisation. |
| `/analytics/hourly` | GET | Legacy hourly aggregates from the `aiosqlite` store, bucketed in UTC. |
| `/analytics/summary` | GET | Legacy period summary derived from the same store. |
| `/analytics/history` | DELETE | Clear the in-memory ring buffer only; the database is untouched. |

Event history is the source of truth for totals. The two legacy endpoints above read a separate aggregate table and are retained for compatibility.

---

## Storage and Data Model

Two SQLite files are used, and only the second holds history worth backing up.

- `data/traffic.db` (`aiosqlite`): `hourly_aggregates`, the legacy aggregate path. Live charts read a separate in-process ring buffer holding the last 10,000 metrics frames, roughly five minutes at 25 FPS.
- `data/roadseye.db` (stdlib `sqlite3`, WAL): `processing_sessions`, `vehicle_events` and `system_logs`. This is the durable history.

One `vehicle_events` row is written per confirmed crossing, carrying camera ID, session ID, track ID, vehicle class, UTC timestamp and epoch, crossing direction, confidence and counting-line ID. Row IDs are UUIDs inserted with `INSERT OR IGNORE`, so a retried batch cannot duplicate a crossing. Writes are buffered in memory, committed every 15 minutes and flushed on stop, on disconnect and at shutdown. If the file is missing or unreadable the API refuses to start rather than silently creating an empty database.

Calendar buckets, chart labels, CSV timestamps and the dashboard use Asia/Jakarta (UTC+7); stored epochs remain UTC.

## Dashboard

| Panel | Source |
|-------|--------|
| Live CCTV | `/ws/video` frames with the counting-line editor overlaid on the canvas |
| KPI strip | `/pipeline/status` and the latest metrics, including uptime and pre-roll progress |
| Class composition donut | `metrics.count_per_class` |
| Vehicles per minute | `/analytics/events/per-minute` |
| Persistent history | `/analytics/history/summary` filtered to today in WIB |
| Vehicle timestamp feed | `metrics.crossings` |
| Database viewer | `/analytics/events`, history summary, CSV export and backup, opened in its own window via `?view=database` |

---

## Project Structure

```
RoadsEye/
├── api/
│   ├── main.py               FastAPI app, lifespan, CORS
│   ├── pipeline_manager.py   Pipeline lifecycle, WebSocket fan-out, subscription queues
│   ├── database.py           aiosqlite hourly aggregates and ring buffer
│   ├── events_db.py          Durable event store, session log and buffered writer
│   ├── sheets_sync.py        Google Sheets synchronisation
│   ├── schemas.py            Pydantic wire models
│   └── routes/               health.py, stream.py, analytics.py
├── core/
│   ├── pipeline.py           Frame loop, pacing and annotation
│   ├── detector.py           YOLOv8s ONNX inference and NMS
│   ├── tracker.py            ByteTrack IDs
│   ├── analytics.py          Counting, speed, anomaly and drift monitors
│   ├── video_source.py       File, webcam and RTSP capture with PTS pacing
│   └── demo_generator.py     Synthetic two-lane scene
├── dashboard/
│   ├── src/App.jsx           Console layout and database window switch
│   ├── src/components/       VideoFeed, StatsCards, charts, LineEditor, HistoryPanel, DatabaseViewer
│   ├── src/hooks/useWebSocket.js
│   ├── vite.config.js        Dev proxy for /api and WebSocket upgrades
│   └── nginx.conf            Production proxy and SPA fallback
├── models/weights/           yolov8s_traffic.onnx
├── data/                     Aggregate DB, event DB and sample videos
├── notebooks/                Exploration and training
├── tests/                    test_api.py, test_core.py, test_events.py, test_sheets.py
├── start_app.bat             One-click launcher
├── stop_app.bat
├── docker-compose.yml
└── requirements.txt
```

## Testing

```bash
venv\Scripts\activate
pytest
```

Tests rewrite both database paths to temporary files, so a run never touches real history. No ONNX model, GPU or camera is required: the detector is mocked in engine tests.

## Model

`models/weights/yolov8s_traffic.onnx` is a YOLOv8s fine-tuned on traffic imagery and exported to ONNX. Training and export live in `notebooks/02_training.ipynb`. Inference letterboxes input to 640x640, runs the CUDA execution provider when available, falls back to CPU, then applies NMS in Python.

Classes are `car`, `bus`, `motorcycle` and `truck` on internal ids 0 to 3, matching COCO indices 2, 5, 3 and 7 respectively. A stock 80-class COCO export also works; those detections are mapped to the traffic classes and discarded when another class outscores the vehicle interpretation.

## Contributing

1. Fork the repository and create a feature branch.
2. Install dependencies: `pip install -r requirements.txt` and `npm install` inside `dashboard/`.
3. Keep `pytest` green before opening a pull request.
