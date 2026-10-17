"""WebSocket endpoints for live video and metrics streaming.

/ws/video   — sends binary JPEG frames as fast as the pipeline produces them
/ws/metrics — sends JSON MetricsMessage objects each processed frame

Also exposes REST endpoints to start/stop/query the pipeline.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from api.pipeline_manager import manager
from api.schemas import PipelineStartRequest, PipelineStatus
from core.pipeline import PipelineConfig

router = APIRouter(tags=["stream"])


# ── Pipeline control ──────────────────────────────────────────────────────────

@router.post("/pipeline/start", response_model=PipelineStatus, status_code=status.HTTP_202_ACCEPTED)
async def start_pipeline(req: PipelineStartRequest) -> PipelineStatus:
    """Start (or restart) the processing pipeline."""
    counting_lines = [c.as_spec() for c in req.counting_lines]

    config = PipelineConfig(
        source=req.source,
        confidence_threshold=req.confidence_threshold,
        iou_threshold=req.iou_threshold,
        skip_frames=req.skip_frames,
        counting_lines=counting_lines,
        pixels_per_meter=req.pixels_per_meter,
        loop=req.loop,
        draw_annotations=req.draw_annotations,
        jpeg_quality=req.jpeg_quality,
    )

    await manager.start(config)
    return manager.status()


@router.post("/pipeline/stop", response_model=PipelineStatus)
async def stop_pipeline() -> PipelineStatus:
    """Stop the running pipeline."""
    await manager.stop()
    return manager.status()


@router.get("/pipeline/status", response_model=PipelineStatus)
async def pipeline_status() -> PipelineStatus:
    return manager.status()


# ── WebSocket: video frames ───────────────────────────────────────────────────

@router.websocket("/ws/video")
async def ws_video(ws: WebSocket) -> None:
    """Stream annotated JPEG frames as binary WebSocket messages.

    The client should display each binary blob as an image (e.g. via
    URL.createObjectURL or setting img.src to a blob URL).

    The pipeline must already be running — start it via POST /pipeline/start first.
    If the pipeline stops, this WebSocket closes automatically.
    """
    await ws.accept()

    if not manager.running:
        await ws.send_text(json.dumps({"error": "Pipeline not running. POST /pipeline/start first."}))
        await ws.close(code=1011)
        return

    q = manager.subscribe_video()
    try:
        while True:
            if not manager.running:
                break
            try:
                jpeg_bytes = await asyncio.wait_for(q.get(), timeout=5.0)
                await ws.send_bytes(jpeg_bytes)
            except asyncio.TimeoutError:
                # Send a ping to keep connection alive and check if pipeline died
                try:
                    await ws.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe_video(q)


# ── WebSocket: metrics stream ─────────────────────────────────────────────────

@router.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket) -> None:
    """Stream JSON analytics metrics each processed frame.

    Message format: MetricsMessage (see api/schemas.py)

    The pipeline must already be running — start it via POST /pipeline/start first.
    """
    await ws.accept()

    if not manager.running:
        await ws.send_text(json.dumps({"error": "Pipeline not running. POST /pipeline/start first."}))
        await ws.close(code=1011)
        return

    q = manager.subscribe_metrics()
    try:
        while True:
            if not manager.running:
                break
            try:
                metrics = await asyncio.wait_for(q.get(), timeout=5.0)
                await ws.send_text(metrics.model_dump_json())
            except asyncio.TimeoutError:
                try:
                    await ws.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe_metrics(q)
