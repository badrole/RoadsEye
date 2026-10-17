"""Singleton pipeline manager — owns the running Pipeline instance.

The FastAPI app imports this module and calls start/stop.
WebSocket handlers consume frames from the async queue.
"""
from __future__ import annotations

import asyncio
import time

from core.pipeline import Pipeline, PipelineConfig
from api.schemas import CountingLineStatus, MetricsMessage, PipelineStatus

# Max frames queued for WebSocket consumers; older frames are dropped when full
_VIDEO_QUEUE_MAXSIZE = 4
_METRICS_QUEUE_MAXSIZE = 64


class PipelineManager:
    """Manages the lifecycle of a single active pipeline.

    One pipeline runs at a time. Multiple WebSocket clients can subscribe
    to video and metrics queues simultaneously.
    """

    def __init__(self) -> None:
        self._pipeline: Pipeline | None = None
        self._task: asyncio.Task | None = None
        self._config: PipelineConfig | None = None

        self._start_time: float = 0.0
        self._frame_index: int = 0

        # Subscriber queues — one per connected WebSocket client
        self._video_queues: list[asyncio.Queue[bytes]] = []
        self._metrics_queues: list[asyncio.Queue[MetricsMessage]] = []

        self._lock = asyncio.Lock()

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> PipelineStatus:
        uptime = time.time() - self._start_time if self.running else 0.0
        fps    = self._frame_index / uptime if uptime > 0 else 0.0
        lines  = [
            CountingLineStatus(name=s.name, x1=s.p1[0], y1=s.p1[1], x2=s.p2[0], y2=s.p2[1])
            for s in (self._config.counting_lines if self._config else [])
        ]
        return PipelineStatus(
            running=self.running,
            source=self._config.source if self._config else None,
            frame_index=self._frame_index,
            uptime_seconds=round(uptime, 1),
            fps=round(fps, 1),
            demo_mode=self._config.demo_mode if self._config else False,
            counting_lines=lines,
        )

    async def start(self, config: PipelineConfig) -> None:
        async with self._lock:
            if self.running:
                await self._stop_pipeline()

            self._config = config
            self._pipeline = Pipeline(config)
            self._frame_index = 0
            self._start_time = time.time()
            self._task = asyncio.create_task(self._run_loop(), name="pipeline")

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_pipeline()

    # ── Queue subscription ────────────────────────────────────────────────────

    def subscribe_video(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_VIDEO_QUEUE_MAXSIZE)
        self._video_queues.append(q)
        return q

    def unsubscribe_video(self, q: asyncio.Queue[bytes]) -> None:
        try:
            self._video_queues.remove(q)
        except ValueError:
            pass

    def subscribe_metrics(self) -> asyncio.Queue[MetricsMessage]:
        q: asyncio.Queue[MetricsMessage] = asyncio.Queue(maxsize=_METRICS_QUEUE_MAXSIZE)
        self._metrics_queues.append(q)
        return q

    def unsubscribe_metrics(self, q: asyncio.Queue[MetricsMessage]) -> None:
        try:
            self._metrics_queues.remove(q)
        except ValueError:
            pass

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _stop_pipeline(self) -> None:
        if self._pipeline:
            self._pipeline.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._pipeline = None

    async def _run_loop(self) -> None:
        """Async loop: pull frames from pipeline, fan-out to subscribers."""
        from api.database import ring_push, upsert_hourly

        try:
            async for jpeg_bytes, raw_metrics in self._pipeline.run_async():
                self._frame_index += 1
                metrics = MetricsMessage.from_frame_metrics(raw_metrics)

                # Persist
                ring_push(metrics)

                # Upsert hourly aggregate every 25 frames (~1s) to avoid DB thrash
                if self._frame_index % 25 == 0:
                    asyncio.create_task(upsert_hourly(metrics))

                # Fan-out video frame
                for q in list(self._video_queues):
                    if q.full():
                        try:
                            q.get_nowait()   # drop oldest frame
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        q.put_nowait(jpeg_bytes)
                    except asyncio.QueueFull:
                        pass

                # Fan-out metrics
                for q in list(self._metrics_queues):
                    try:
                        q.put_nowait(metrics)
                    except asyncio.QueueFull:
                        pass   # slow consumer — drop frame

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[PipelineManager] Pipeline error: {exc}")
            raise


# Module-level singleton — imported by routes
manager = PipelineManager()
