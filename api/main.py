"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Environment variables (see .env.example):
    MODEL_PATH            — path to ONNX weights file
    VIDEO_SOURCE          — default video source (file path, webcam index, RTSP URL)
    CONFIDENCE_THRESHOLD  — detection confidence threshold (default 0.25)
    IOU_THRESHOLD         — NMS IoU threshold (default 0.45)
    COUNTING_LINES        — JSON list of counting tripwires [{name,x1,y1,x2,y2}, ...]
    LOCK_LINES            — JSON list of label-lock lines [{name,x1,y1,x2,y2}, ...]
    CORS_ORIGINS          — comma-separated allowed CORS origins (default *)
"""
from __future__ import annotations

import ctypes
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db
from api.routes import analytics, health, stream


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if sys.platform == "win32":
        # Default Windows timer tick is 15.6 ms; asyncio waits round up to it,
        # which shows as uneven frame delivery. 1 ms while the API runs.
        ctypes.windll.winmm.timeBeginPeriod(1)
    await init_db()
    print("[API] Database initialised.")

    # Persistent event DB (Phase 1)
    import api.events_db as events_db
    try:
        resolved = events_db.init_events_db()
        print(f"[API] Events DB (persistent history): {resolved}")
    except Exception as exc:
        print(f"[API] ERROR: could not open events database at {events_db.DB_PATH.resolve()}: {exc}")
        print("[API]       Fix the database file/path, then restart. Refusing to silently create a fresh one.")
        raise
    events_db.writer.start()
    print("[API] Event writer started.")

    # Auto-start pipeline if VIDEO_SOURCE env var is set
    source = os.environ.get("VIDEO_SOURCE", "").strip()
    if source:
        import json
        from api.pipeline_manager import manager
        from core.pipeline import PipelineConfig
        from core.analytics import CountingLineSpec

        try:
            src: str | int = int(source)
        except ValueError:
            src = source

        counting_lines = []
        raw_lines = os.environ.get("COUNTING_LINES", "").strip()
        if raw_lines:
            try:
                parsed_lines = json.loads(raw_lines)
                for item in parsed_lines:
                    counting_lines.append(
                        CountingLineSpec(
                            name=item.get("name", "line"),
                            p1=(int(item["x1"]), int(item["y1"])),
                            p2=(int(item["x2"]), int(item["y2"])),
                        )
                    )
            except Exception as e:
                print(f"[API] Could not parse COUNTING_LINES from env: {e}")

        config = PipelineConfig(
            source=src,
            model_path=os.environ.get("MODEL_PATH") or None,
            confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.25")),
            iou_threshold=float(os.environ.get("IOU_THRESHOLD", "0.45")),
            counting_lines=counting_lines,
            count_mode=manager.count_mode,
        )
        await manager.start(config)
        print(f"[API] Pipeline auto-started with source={src!r}, count_lines={[l.name for l in counting_lines]}")

    yield

    # Shutdown
    from api.pipeline_manager import manager
    await manager.stop()
    print("[API] Pipeline stopped.")

    # Flush pending events to SQLite before exit
    import api.events_db as events_db
    events_db.writer.stop()
    print("[API] Event writer flushed and stopped.")
    if sys.platform == "win32":
        ctypes.windll.winmm.timeEndPeriod(1)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="RoadsEye API",
    description="Real-time vehicle detection, tracking, and traffic analytics.",
    version="1.1.0",
    lifespan=lifespan,
)

# CORS — allow React dev server by default; tighten in production
_origins_env = os.environ.get("CORS_ORIGINS", "*")
_origins = [o.strip() for o in _origins_env.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(stream.router)
app.include_router(analytics.router)


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "RoadsEye API", "docs": "/docs"}
