"""WebSocket endpoints for live video and metrics streaming.

/ws/video   — sends binary JPEG frames as fast as the pipeline produces them
/ws/metrics — sends JSON MetricsMessage objects each processed frame

Also exposes REST endpoints to start/stop/query the pipeline.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from server.runtime_controller import manager
from server.schemas import (
    CountModeRequest,
    CountModeResponse,
    LinesResponse,
    LinesUpdateRequest,
    PipelineStartRequest,
    PipelineStatus,
    ZonePoint,
    ZoneResponse,
    ZoneUpdateRequest,
)
from engine.stream_processor import PipelineConfig

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
        count_mode=req.count_mode or manager.count_mode,
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


# ── REST: live line editing ───────────────────────────────────────────────────

def _status_lines(content) -> list:
    from server.schemas import CountingLineStatus
    out = []
    for s in content:
        out.append(CountingLineStatus(name=s.name, x1=int(s.p1[0]), y1=int(s.p1[1]),
                                      x2=int(s.p2[0]), y2=int(s.p2[1])))
    return out


@router.get("/pipeline/lines", response_model=LinesResponse)
async def get_lines() -> LinesResponse:
    """Current counting line and Per-ID zone geometry (interactive editor hydration)."""
    st = manager.status()
    return LinesResponse(counting_lines=st.counting_lines, counting_zone=st.counting_zone)


@router.post("/pipeline/lines", response_model=LinesResponse)
async def update_lines(req: LinesUpdateRequest) -> LinesResponse:
    """Update counting lines (and optionally counting zone) live."""
    counting = [c.as_spec() for c in req.counting_lines]
    manager.set_lines(counting_lines=counting)
    if req.counting_zone is not None:
        manager.set_zone([(p.x, p.y) for p in req.counting_zone])
    st = manager.status()
    return LinesResponse(counting_lines=_status_lines(counting), counting_zone=st.counting_zone)


@router.get("/pipeline/zone", response_model=ZoneResponse)
async def get_zone() -> ZoneResponse:
    """Current Per-ID counting zone polygon."""
    return ZoneResponse(counting_zone=manager.status().counting_zone)


@router.post("/pipeline/zone", response_model=ZoneResponse)
async def update_zone(req: ZoneUpdateRequest) -> ZoneResponse:
    """Update Per-ID counting zone polygon live."""
    pts = [(p.x, p.y) for p in req.counting_zone]
    manager.set_zone(pts)
    return ZoneResponse(counting_zone=[ZonePoint(x=p[0], y=p[1]) for p in pts])


@router.get("/pipeline/count-mode", response_model=CountModeResponse)
async def get_count_mode() -> CountModeResponse:
    return CountModeResponse(mode=manager.count_mode)


@router.post("/pipeline/count-mode", response_model=CountModeResponse)
async def update_count_mode(req: CountModeRequest) -> CountModeResponse:
    """Switch between line-crossing and per-ID counting; the daily tally carries over."""
    manager.set_count_mode(req.mode)
    return CountModeResponse(mode=manager.count_mode)


# ── WebSocket: video frames ───────────────────────────────────────────────────

@router.websocket("/ws/video")
async def ws_video(ws: WebSocket) -> None:
    """Stream annotated JPEG frames as binary WebSocket messages.

    Drains intermediate queued frames before sending so the client always
    receives the freshest available frame with minimal latency.
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
                while not q.empty():
                    try:
                        jpeg_bytes = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
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
        import server.event_store as events_db
        events_db.writer.flush()


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
        import server.event_store as events_db
        events_db.writer.flush()
