# Traffic Video Analytics Project

Real-time vehicle detection, multi-object tracking, and traffic analytics — end-to-end from raw video to a live React dashboard.

**Stack:** YOLOv8s · ByteTrack · ONNX Runtime GPU · FastAPI · WebSockets · React · Recharts · Tailwind CSS · SQLite · Docker

---

## Features

- **Vehicle detection** — YOLOv8s fine-tuned on traffic surveillance footage (UA-DETRAC dataset), exported to ONNX for fast GPU inference
- **Multi-object tracking** — ByteTrack via the `supervision` library; each vehicle gets a stable track ID across frames
- **Counting lines** — configurable virtual tripwires; vehicles are counted the moment their center crosses a line
- **Speed estimation** — pixel displacement × camera calibration constant → km/h per track
- **Class breakdown** — real-time split across car, bus, motorcycle, and truck
- **Anomaly detection** — rolling 60-frame window; surge alert fires when count exceeds 2σ from the mean; stopped-vehicle alert for stationary tracks
- **Drift monitoring** — rolling confidence baseline; alerts when model performance degrades during a live run
- **Live dashboard** — video feed, KPI cards, vehicle count chart, speed distribution, class breakdown, alerts panel, track overlay canvas
- **Demo mode** — fully synthetic traffic scene (no ONNX model, no camera, no GPU required)
- **REST + WebSocket API** — annotated JPEG frames on `/ws/video`, JSON metrics on `/ws/metrics`, historical data via `/analytics/*`
- **Dockerised** — single `docker compose up` brings up the full stack (GPU optional)

---

## Demo

Demo mode generates a synthetic 2-lane traffic scene entirely in software using OpenCV and NumPy. Vehicles spawn with realistic class distribution, travel at 35–78 km/h, and produce real `Detection` objects identical to ONNX model output — no weights file or camera needed.

> Start the project and click **Demo** in the Pipeline Control bar.

---

## Architecture

```
Video Source (file / webcam / RTSP / demo)
    │
    ▼
OpenCV Frame Capture          core/video_source.py
    │
    ▼
YOLOv8s — ONNX Runtime GPU   core/detector.py
    │
    ▼
ByteTrack (supervision)       core/tracker.py
    │
    ▼
Analytics Engine              core/analytics.py
  ├── multi-line vehicle counting
  ├── speed estimation (px/frame × calibration → km/h)
  ├── class breakdown
  ├── anomaly detection (surge + stopped vehicle)
  └── drift monitoring (confidence rolling baseline)
    │
    ▼
FastAPI Backend               api/
  ├── WebSocket /ws/video     — binary JPEG frames
  ├── WebSocket /ws/metrics   — JSON MetricsMessage per frame
  └── REST      /analytics/*  — historical aggregates
    │
    ▼
React Dashboard               dashboard/
  ├── VideoFeed canvas
  ├── KPI cards (StatsCards)
  ├── VehicleCountChart
  ├── SpeedDistribution
  ├── ClassBreakdown
  ├── AlertsPanel
  └── TrackOverlay canvas
    │
    ▼
SQLite (hourly aggregates) + in-memory ring buffer (last 5 min)
```

---

## Quick Start — Demo Mode (no model required)

### Docker (recommended)

```bash
git clone https://github.com/salvirezwan/Traffic-Video-Analytics-Project.git
cd Traffic-Video-Analytics-Project
docker compose up --build
```

Open **http://localhost** in your browser, then click **Demo** in the Pipeline Control bar.

> The `deploy.resources` GPU block in `docker-compose.yml` is silently ignored if `nvidia-container-toolkit` is not installed — CPU inference and demo mode work fine without it.

### Local development

**Prerequisites:** Python 3.10+, Node 20+

```bash
# 1. Backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # edit as needed
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 2. Frontend (separate terminal)
cd dashboard
npm install
npm run dev
```

Open **http://localhost:3000**, click **Demo**.

---

## Full Setup — Live Video with GPU Inference

### 1. Requirements

- NVIDIA GPU with CUDA 12.x support
- CUDA Toolkit 12.6 + cuDNN 9.x installed
- `onnxruntime-gpu` (included in `requirements.txt`)

### 2. Obtain weights

Train on Colab (see [Training](#training)) or download a pre-exported file and place it at:

```
models/weights/yolov8s_traffic.onnx
```

### 3. Configure

```bash
cp .env.example .env
```

Key variables:

| Variable | Default | Description |
|---|---|---|
| `MODEL_PATH` | `models/weights/yolov8s_traffic.onnx` | Path to ONNX weights |
| `VIDEO_SOURCE` | `data/sample_videos/traffic.mp4` | File path, `0` for webcam, `rtsp://…` |
| `CONFIDENCE_THRESHOLD` | `0.4` | Detection confidence (0–1) |
| `IOU_THRESHOLD` | `0.5` | NMS IoU threshold |
| `METERS_PER_PIXEL` | `0.05` | Speed calibration constant |
| `JPEG_QUALITY` | `80` | Video stream compression (1–100) |

### 4. Run

```bash
# Docker (with GPU)
docker compose up --build

# Local
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
# Frontend: cd dashboard && npm run dev
```

### 5. Use the dashboard

1. Set **Source** (file path, `0` for webcam, or RTSP URL) in the Pipeline Control bar.
2. Adjust **Confidence** threshold as needed (lower = more detections, more false positives).
3. Optionally expand **Lines** to define counting tripwires — enter `(x1, y1) → (x2, y2)` in source frame pixels.
4. Click **Start**.

---

## Counting Lines

Lines are configured per-run via the dashboard UI or directly via the API. Each line has a name and two endpoints in the **source frame's pixel coordinate system**.

A vehicle is counted when its center point crosses from one side of the line to the other.

```json
POST /pipeline/start
{
  "source": "data/sample_videos/traffic.mp4",
  "confidence_threshold": 0.35,
  "counting_lines": [
    { "name": "north", "x1": 0, "y1": 200, "x2": 1280, "y2": 200 },
    { "name": "south", "x1": 0, "y1": 500, "x2": 1280, "y2": 500 }
  ]
}
```

Live counts per line are broadcast on `/ws/metrics` → `count_per_line` and rendered on the Track Overlay canvas.

---

## API Reference

Interactive docs available at **http://localhost:8000/docs**

| Method | Path | Description |
|---|---|---|
| `POST` | `/pipeline/start` | Start (or restart) the pipeline |
| `POST` | `/pipeline/stop` | Stop the running pipeline |
| `GET` | `/pipeline/status` | Current pipeline state |
| `WS` | `/ws/video` | Binary JPEG frame stream |
| `WS` | `/ws/metrics` | JSON `MetricsMessage` per frame |
| `GET` | `/analytics/recent` | Last N minutes from ring buffer |
| `GET` | `/analytics/hourly` | Hourly aggregates from SQLite |
| `GET` | `/health` | Health check |

---

## Training

All training happens on Google Colab (free T4 GPU). The local machine is inference-only.

```
notebooks/
├── 01_data_prep.ipynb      # Download UA-DETRAC, convert to YOLO format
└── 02_training.ipynb       # Train YOLOv8s, evaluate, export to ONNX
```

**Workflow:**

1. Open `notebooks/02_training.ipynb` in Colab.
2. Run all cells — trains YOLOv8s on UA-DETRAC, saves `.pt` + `.onnx` to Google Drive.
3. Download `yolov8s_traffic.onnx` to `models/weights/`.
4. Restart the API — it loads the model automatically on startup.

**Hardware used:**
- Training: Google Colab T4 (15 GB VRAM), `batch_size=16`
- Inference: NVIDIA RTX 3050 Ti (4 GB VRAM) — YOLOv8s ONNX fits comfortably

---

## Project Structure

```
traffic-video-analytics-project/
├── core/                   # CV + analytics engine
│   ├── detector.py         # ONNX/YOLOv8 inference wrapper
│   ├── tracker.py          # ByteTrack via supervision
│   ├── video_source.py     # OpenCV frame capture abstraction
│   ├── demo_generator.py   # Synthetic traffic scene (no model needed)
│   ├── pipeline.py         # Orchestrator: detect → track → analyse
│   └── analytics.py        # Counting, speed, anomalies, drift monitor
├── api/                    # FastAPI backend
│   ├── main.py
│   ├── pipeline_manager.py # Singleton: owns Pipeline, fans out to WS clients
│   ├── database.py         # SQLite + in-memory ring buffer
│   ├── schemas.py          # Pydantic v2 models
│   ├── routes/
│   │   ├── stream.py       # WebSocket video + metrics, pipeline control
│   │   ├── analytics.py    # Historical data REST endpoints
│   │   └── health.py
│   └── Dockerfile
├── dashboard/              # React + Vite frontend
│   ├── src/
│   │   ├── components/     # VideoFeed, StatsCards, charts, AlertsPanel, …
│   │   └── hooks/
│   │       └── useWebSocket.js
│   ├── nginx.conf          # Reverse proxy for Docker deployment
│   └── Dockerfile
├── models/
│   ├── weights/            # .onnx / .pt files (gitignored)
│   └── export/             # ONNX export scripts
├── notebooks/              # Colab training notebooks
├── tests/                  # pytest test suite
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Detection model | YOLOv8s (Ultralytics) fine-tuned on UA-DETRAC → ONNX |
| Inference runtime | ONNX Runtime with CUDAExecutionProvider |
| Tracking | ByteTrack via `supervision` |
| Video I/O | OpenCV |
| Backend | FastAPI + uvicorn + WebSockets |
| Data models | Pydantic v2 |
| Persistence | SQLite via `aiosqlite` + in-memory ring buffer |
| Frontend | React 18 + Vite + Tailwind CSS + Recharts |
| Container | Docker + Docker Compose |
| Training | Google Colab T4 GPU |

---

## Development

```bash
# Backend tests
pytest tests/ -v

# Frontend lint
cd dashboard && npm run lint
```

