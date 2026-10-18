"""Singleton pipeline manager — owns the running Pipeline instance.

The FastAPI app imports this module and calls start/stop.
WebSocket handlers consume frames from the async queue.
"""
from __future__ import annotations

import asyncio
import time
import uuid

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

        # Persistence (Phase 1): one processing session per pipeline start
        self._session_id: str | None = None
        self._camera_id: str | None = None

        # Subscriber queues — one per connected WebSocket client
        self._video_queues: list[asyncio.Queue[bytes]] = []
        self._metrics_queues: list[asyncio.Queue[MetricsMessage]] = []

        self._lock = asyncio.Lock()        # Default counting lines (fallback when stopped / hydration before start)
        self._default_lines = []
        self._load_default_lines()
        self._last_sheets_sync_time: float = 0.0

    def _load_default_lines(self) -> None:
        import json
        import os
        from core.analytics import CountingLineSpec
        raw = os.environ.get("COUNTING_LINES", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                for item in parsed:
                    self._default_lines.append(
                        CountingLineSpec(
                            name=item.get("name", "line1"),
                            p1=(int(item["x1"]), int(item["y1"])),
                            p2=(int(item["x2"]), int(item["y2"])),
                        )
                    )
            except Exception:
                pass
        if not self._default_lines:
            self._default_lines = [CountingLineSpec("line1", (0, 380), (1280, 380))]

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> PipelineStatus:
        uptime = time.time() - self._start_time if self.running else 0.0
        fps    = self._frame_index / uptime if uptime > 0 else 0.0

        if self._pipeline is not None:
            active_lines = self._pipeline.line_specs
        elif self._config and self._config.counting_lines:
            active_lines = self._config.counting_lines
        else:
            active_lines = self._default_lines

        lines  = [
            CountingLineStatus(name=s.name, x1=int(s.p1[0]), y1=int(s.p1[1]), x2=int(s.p2[0]), y2=int(s.p2[1]))
            for s in active_lines
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

            # Persistent session is only opened for real cameras/files, never for demo
            if not config.demo_mode:
                self._session_id = uuid.uuid4().hex
                self._camera_id = str(config.source)
                import api.events_db as events_db
                events_db.open_session(self._session_id, self._camera_id, source_detail="RoadsEye pipeline")
            else:
                self._session_id = None
                self._camera_id = "demo"

            if not config.counting_lines and self._default_lines:
                config.counting_lines = list(self._default_lines)

            self._config = config
            self._pipeline = Pipeline(config)

            # Load cumulative counts for today only for real cameras
            if not config.demo_mode:
                today_counts = events_db.get_today_counts(camera_id=self._camera_id)
                self._pipeline.set_today_baseline(today_counts)

            self._frame_index = 0
            self._start_time = time.time()
            self._last_sheets_sync_time = time.time()
            self._task = asyncio.create_task(self._run_loop(), name="pipeline")

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_pipeline()

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
        # Mandatory autosave on disconnect/stop
        import api.events_db as events_db
        events_db.writer.flush()
        had_persistent_session = self._session_id is not None
        if self._session_id:
            events_db.close_session(self._session_id, status="stopped")
            self._session_id = None

        # Mandatory auto-sync to Google Sheets upon disconnect/stop
        if had_persistent_session:
            try:
                import api.sheets_sync as sheets_sync
                if sheets_sync.is_configured():
                    asyncio.create_task(asyncio.to_thread(sheets_sync.sync_to_google_sheets))
            except Exception as e:
                print(f"[PipelineManager] Sheets sync on stop error: {e}")

    def set_lines(self, counting_lines) -> None:
        """Live update of counting lines, or update defaults when stopped."""
        specs = list(counting_lines) if counting_lines is not None else []
        self._default_lines = specs
        if self._config is not None:
            self._config.counting_lines = specs
        if self._pipeline is not None:
            self._pipeline.set_lines(
                counting_lines=specs,
            )

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

    async def _run_loop(self) -> None:
        """Async loop: pull frames from pipeline, fan-out to subscribers."""
        from api.database import ring_push, upsert_hourly
        import api.events_db as events_db

        try:
            async for jpeg_bytes, raw_metrics in self._pipeline.run_async():
                self._frame_index += 1
                metrics = MetricsMessage.from_frame_metrics(raw_metrics)

                # Persist confirmed counting crossings ONLY for real video (never demo)
                if metrics.crossings and self._session_id and not (self._config and self._config.demo_mode):
                    for c in metrics.crossings:
                        events_db.writer.enqueue(events_db.make_event(
                            camera_id=self._camera_id or "unknown",
                            session_id=self._session_id,
                            vehicle_type=c.class_name,
                            timestamp_epoch=c.timestamp,
                        ))

                # Persist to in-memory ring buffer (for live charts)
                ring_push(metrics)

                # Upsert hourly aggregate periodically (every 1500 frames ~1 min) for non-demo
                if not (self._config and self._config.demo_mode) and self._frame_index % 1500 == 0:
                    asyncio.create_task(upsert_hourly(metrics))

                # Periodic sync to Google Sheets (every 15 minutes = 900s) for non-demo
                if not (self._config and self._config.demo_mode) and (time.time() - self._last_sheets_sync_time >= 900):
                    self._last_sheets_sync_time = time.time()
                    try:
                        import api.sheets_sync as sheets_sync
                        if sheets_sync.is_configured():
                            asyncio.create_task(asyncio.to_thread(sheets_sync.sync_to_google_sheets))
                    except Exception as e:
                        print(f"[PipelineManager] Periodic sheets sync trigger error: {e}")

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
        finally:
            # Mandatory autosave on stream disconnect / termination if persistent session was active
            if self._session_id:
                events_db.writer.flush()
                try:
                    import api.sheets_sync as sheets_sync
                    if sheets_sync.is_configured():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            pool.submit(sheets_sync.sync_to_google_sheets).result(timeout=15.0)
                except Exception:
                    pass


# Module-level singleton — imported by routes
manager = PipelineManager()
