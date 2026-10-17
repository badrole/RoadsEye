"""FastAPI application entry point.

Run with:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Environment variables (see .env.example):
    MODEL_PATH            — path to ONNX weights file
    VIDEO_SOURCE          — default video source (file path, webcam index, RTSP URL)
    CONFIDENCE_THRESHOLD  — detection confidence threshold (default 0.35)
    IOU_THRESHOLD         — NMS IoU threshold (default 0.45)
    CORS_ORIGINS          — comma-separated allowed CORS origins (default *)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db
from api.routes import analytics, health, stream


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    print("[API] Database initialised.")

    # Auto-start pipeline if VIDEO_SOURCE env var is set
    source = os.environ.get("VIDEO_SOURCE", "").strip()
    if source:
        from api.pipeline_manager import manager
        from core.pipeline import PipelineConfig

        try:
            src: str | int = int(source)
        except ValueError:
            src = source

        config = PipelineConfig(
            source=src,
            model_path=os.environ.get("MODEL_PATH") or None,
            confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.35")),
            iou_threshold=float(os.environ.get("IOU_THRESHOLD", "0.45")),
        )
        await manager.start(config)
        print(f"[API] Pipeline auto-started with source={src!r}")

    yield

    # Shutdown
    from api.pipeline_manager import manager
    await manager.stop()
    print("[API] Pipeline stopped.")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="TrafficVision API",
    description="Real-time vehicle detection, tracking, and traffic analytics.",
    version="1.0.0",
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
    return {"message": "TrafficVision API", "docs": "/docs"}
