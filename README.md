# 🛣️ RoadsEye — CCTV Traffic Analytics Dashboard

Real-time vehicle detection, tracking, and traffic analytics dashboard — deteksi kendaraan, penghitungan volume, estimasi kecepatan, dan alert otomatis langsung dari feed CCTV.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.4-06B6D4?style=flat&logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-FFCC00?style=flat)](https://docs.ultralytics.com)
[![ONNX Runtime](https://img.shields.io/badge/ONNX_Runtime-GPU_1.22-005CED?style=flat)](https://onnxruntime.ai)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ Fitur

- 🔍 **Deteksi & pelacakan kendaraan real-time** — YOLOv8s ONNX + ByteTrack, dipercepat GPU (CUDA 12) via `onnxruntime-gpu`
- 🧠 **ID Lock** — label kelas kendaraan dikunci permanen setelah kendaraan melewati *lock line*, mencegah label "pindah kelas" saat oklusi sebelum garis hitung
- 📊 **Dashboard monitoring langsung** — video feed MJPEG-over-WebSocket, KPI strip, grafik jumlah kendaraan, distribusi kecepatan, komposisi kelas (pie chart)
- 🚨 **Alert otomatis** — deteksi anomali volume lalu lintas (rolling mean ± N x standar deviasi) tanpa model tambahan
- 🎯 **Editor garis interaktif** — tarik garis penghitung & lock line langsung di atas frame CCTV
- 🗺️ **Track overlay** — jejak lintasan kendaraan di atas frame
- 💾 **Persistensi** — SQLite + ring buffer in-memory, agregasi per jam via `/analytics`
- 🐳 **Docker-ready** — `docker compose up` menyalakan backend (GPU passthrough) + dashboard Nginx

---

## 🧰 Tech Stack

| Layer | Teknologi | Peran |
|-------|-----------|-------|
| CV / Inference | **YOLOv8s** (ONNX) + `onnxruntime-gpu` 1.22 | Deteksi kendaraan pada frame |
| Tracking | **supervision** (ByteTrack) | Pelacakan antar frame + counting |
| Video | **OpenCV** | Capture frame, resize, encode JPEG |
| Backend | **FastAPI** + Uvicorn | REST API + WebSocket (video & metrics) |
| Database | **SQLite** (`aiosqlite`) + ring buffer in-memory | Penyimpanan metrik histori |
| Anomaly | **SciPy** rolling stats | Baseline statistik alert volume |
| Frontend | **React 18** + **Vite 5** | Dashboard SPA |
| Charts | **Recharts** | Grafik jumlah, kecepatan, komposisi kelas |
| Styling | **Tailwind CSS 3** | Tema aspal–marka jalan (asphalt/lane tokens) |
| WebSocket client | native `WebSocket` API | Stream frame video biner + metrik JSON |
| Model training | **PyTorch** + **Ultralytics** | Fine-tune YOLOv8s (Colab) + export ONNX |
| Testing | **pytest** + httpx | Unit & API tests |
| Deployment | **Docker Compose** (NVIDIA runtime) | Orkestrasi backend + dashboard |

---

## 🏗️ Arsitektur

```
CCTV / MP4 / RTSP
      │  (OpenCV frame capture)
      ▼
┌─────────────────────────────────────────────┐
│ core/pipeline.py   ── orchestration loop    │
│   core/detector.py   YOLOv8s ONNX inference │
│   core/tracker.py    ByteTrack + ID lock    │
│   core/analytics.py  counting, speed, alert │
└─────────────────────────────────────────────┘
      │ ring buffer + SQLite
      ▼
FastAPI (api/)
   ├─ REST  : /health /pipeline/* /analytics/*
   └─ WS    : /ws/video (JPEG biner)  /ws/metrics (JSON)
      │
      ▼
React Dashboard (dashboard/)
   ├─ VideoFeed / StatsCards / VehicleCountChart
   ├─ SpeedDistribution / ClassBreakdown
   └─ TrafficFlowMap / PipelineControl / AlertsPanel
```

---

## 🚦 Cara Menjalankan (Windows — Satu Klik)

1. **Klik dua kali `start_app.bat`** — script otomatis:
   - Membuat virtual environment (`venv/`) dan menginstall dependency Python (hanya saat pertama kali)
   - Menyalakan backend FastAPI di `http://localhost:8000`
   - Menyalakan frontend Vite di `http://localhost:5173`
   - Membuka browser ke dashboard

2. Hentikan semuanya dengan `Ctrl+C` di jendela terminal, atau klik dua kali **`stop_app.bat`**.

> ⚠️ Perlu **Python 3.10+** dan **Node.js 18+**. GPU NVIDIA + CUDA 12 opsional — backend tetap jalan di CPU (lebih lambat).

### Manual (tanpa .bat)

```bash
# Terminal 1 — Backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — Frontend
cd dashboard
npm install
npm run dev
```

Buka `http://localhost:5173`, isi sumber video di panel kontrol, tekan **Start**.

### Docker

```bash
docker compose up --build
# backend  : http://localhost:8000/docs
# dashboard: http://localhost:80
```

---

## ⚙️ Konfigurasi

Semua konfigurasi lewat file `.env` (salin dari `.env.example`):

| Variabel | Default | Keterangan |
|----------|---------|------------|
| `MODEL_PATH` | `models/weights/yolov8s_traffic.onnx` | Bobot model deteksi (ONNX) |
| `VIDEO_SOURCE` | `data/sample_videos/traffic.mp4` | Path file / `0` webcam / `rtsp://` |
| `CONFIDENCE_THRESHOLD` | `0.4` | Batas minimum confidence deteksi |
| `IOU_THRESHOLD` | `0.5` | NMS IoU threshold |
| `TARGET_CLASSES` | `2,3,5,7` | COCO: 2=mobil, 3=motor, 5=bus, 7=truk |
| `COUNTING_LINES` | — | Garis penghitung JSON `[{name,x1,y1,x2,y2}]` |
| `LOCK_LINES` | — | Garis pengunci label (kunci kelas sebelum garis hitung) |
| `METERS_PER_PIXEL` | `0.05` | Kalibrasi estimasi kecepatan |
| `ANOMALY_*` | — | Window & threshold deteksi anomali volume |
| `METRICS_INTERVAL_MS` | `500` | Interval broadcast metrik WebSocket |
| `JPEG_QUALITY` | `80` | Kualitas frame JPEG stream video |

---

## 🧪 Testing

```bash
venv\Scripts\activate
pytest
```

Mencakup unit test engine (`tests/test_core.py`), test API (`tests/test_api.py`), dan test mekanisme **ID lock** (`test_id_lock.py`).

---

## 🗂️ Struktur Proyek

```
RoadsEye/
├── api/                      # Backend FastAPI
│   ├── main.py               # Entry point, lifespan, CORS
│   ├── pipeline_manager.py   # Siklus hidup pipeline (start/stop)
│   ├── database.py           # SQLite (aiosqlite) — metrik
│   ├── schemas.py            # Skema Pydantic
│   └── routes/
│       ├── health.py         # GET /health
│       ├── stream.py         # Kontrol pipeline + WS video/metrics
│       └── analytics.py      # Agregasi recent / hourly / summary
├── core/                     # Engine CV
│   ├── pipeline.py           # Orkestrasi frame loop
│   ├── detector.py           # YOLOv8s ONNX inference
│   ├── tracker.py            # ByteTrack + ID lock
│   ├── analytics.py          # Counting, speed, anomaly
│   ├── video_source.py       # Source file/webcam/RTSP
│   └── demo_generator.py     # Generator video demo
├── dashboard/                # Frontend React
│   ├── src/
│   │   ├── App.jsx           # Layout rail + monitoring deck
│   │   ├── components/       # VideoFeed, StatsCards, charts...
│   │   └── hooks/useWebSocket.js
│   └── Dockerfile            # Build + Nginx serve
├── models/weights/           # yolov8s_traffic.onnx
├── data/                     # traffic.db + sample video
├── notebooks/                # EDA & training (Colab)
├── tests/                    # pytest: core, api, id_lock
├── start_app.bat             # Launcher satu-klik Windows
├── stop_app.bat              # Penghenti proses
├── requirements.txt
├── .env.example
└── docker-compose.yml
```

---

## 📡 API & WebSocket

API playground interaktif: **`http://localhost:8000/docs`** (Swagger UI).

| Endpoint | Method | Deskripsi |
|----------|--------|-----------|
| `/health` | GET | Health check API |
| `/pipeline/start` | POST | Mulai pipeline deteksi |
| `/pipeline/stop` | POST | Hentikan pipeline |
| `/pipeline/status` | GET | Status pipeline + FPS live |
| `/pipeline/lines` | GET/POST | Ambil/set garis counting & lock |
| `/analytics/recent` | GET | Metrik terkini (ring buffer) |
| `/analytics/hourly` | GET | Agregasi volume per jam |
| `/analytics/summary` | GET | Ringkasan periodik (total, rata-rata, puncak) |
| `ws /ws/video` | WebSocket | Stream frame JPEG biner (live CCTV) |
| `ws /ws/metrics` | WebSocket | Broadcast metrik JSON tiap 500 ms |

---

## 📊 Charts di Dashboard

| Panel | Chart | Sumber data |
|-------|-------|-------------|
| Komposisi Kelas | **Donut chart** (mobil/motor/bus/truk) | `metrics.count_per_class` |
| Vehicles In Frame | Line/area chart time-series | `metrics.history` |
| Speed Distribution | Line/area chart | `metrics.history` |
| Track Overlay | Canvas overlay jejak kendaraan | `metrics.tracks` |

---

## 🎯 Model

`yolov8s_traffic.onnx` — fine-tune YOLOv8s pada kelas kendaraan dari dataset lalu-lintas (Colab, lihat `notebooks/02_training.ipynb`). Pipeline inference pintar:

1. Frame di-resize ke input size model
2. ONNX Runtime (CUDA EP → fallback CPU)
3. NMS di sisi Python; hanya kelas `{2,3,5,7}`
4. ByteTrack assign ID + **lock line** membekukan label kelas antar frame
5. Garis counting menghitung volum per kelas per arah

---

## 🤝 Kontribusi

1. Fork & clone
2. `pip install -r requirements.txt && (cd dashboard && npm install)`
3. Buat branch fitur → commit → PR 

Praktik: `pytest` wajib hijau sebelum merge.

---


