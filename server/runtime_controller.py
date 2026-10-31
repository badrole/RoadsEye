"""Singleton pipeline manager — owns the running Pipeline instance.

The FastAPI app imports this module and calls start/stop.
WebSocket handlers consume frames from the async queue.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import deque

from engine.stream_processor import Pipeline, PipelineConfig
from server.schemas import CountingLineStatus, MetricsMessage, PipelineStatus, ZonePoint

# Max frames queued for WebSocket consumers; older frames are dropped when full
_VIDEO_QUEUE_MAXSIZE = 2
_METRICS_QUEUE_MAXSIZE = 32
_METRICS_MIN_INTERVAL_S = 0.15


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

        self._lock = asyncio.Lock()
        # Default counting lines and Per-ID counting zone
        self._default_lines = []
        self._default_zone: list[tuple[int, int]] = []
        self._load_default_lines()
        self._load_default_zone()
        self._last_sheets_sync_time: float = 0.0
        self._preroll_pct: int | None = None   # 0-99 while the timeshift buffer fills
        self._stream_delay = max(0.0, float(os.environ.get("STREAM_DELAY_SECONDS", "0") or 0))
        self._eff_delay = 0.0
        self._delay_buffer: deque = deque()    # rolling (timestamp, jpeg) timeshift buffer
        self._delay_start: float | None = None
        self._counting_armed = True            # False while the timeshift buffer loads
        self._delay_task: asyncio.Task | None = None
        mode = os.environ.get("COUNT_MODE", "line").strip().lower()
        self._count_mode = mode if mode in ("line", "id") else "line"

    def _load_default_lines(self) -> None:
        import json
        import os
        from engine.traffic_metrics import CountingLineSpec
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

    def _load_default_zone(self) -> None:
        import json
        import os
        from engine.traffic_metrics import DEFAULT_COUNTING_ZONE
        raw = os.environ.get("COUNTING_ZONE", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                for pt in parsed:
                    if isinstance(pt, dict):
                        self._default_zone.append((int(pt["x"]), int(pt["y"])))
                    elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        self._default_zone.append((int(pt[0]), int(pt[1])))
            except Exception:
                pass
        if len(self._default_zone) < 3:
            self._default_zone = list(DEFAULT_COUNTING_ZONE)

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def count_mode(self) -> str:
        return self._count_mode

    def set_count_mode(self, mode: str) -> None:
        """Switch line/ID counting live (or set the mode for the next start)."""
        self._count_mode = mode
        if self._config is not None:
            self._config.count_mode = mode
        if self._pipeline is not None:
            self._pipeline.set_count_mode(mode)

    def status(self) -> PipelineStatus:
        is_preroll = self._preroll_pct is not None and self.running
        uptime = 0.0 if is_preroll else (time.time() - self._start_time if self.running else 0.0)
        fps    = 0.0 if is_preroll else (self._frame_index / uptime if uptime > 0 else 0.0)

        if self._pipeline is not None:
            active_lines = self._pipeline.line_specs
            active_zone = self._pipeline.zone_points
        elif self._config:
            active_lines = self._config.counting_lines or self._default_lines
            active_zone = self._config.counting_zone or self._default_zone
        else:
            active_lines = self._default_lines
            active_zone = self._default_zone

        lines  = [
            CountingLineStatus(name=s.name, x1=int(s.p1[0]), y1=int(s.p1[1]), x2=int(s.p2[0]), y2=int(s.p2[1]))
            for s in active_lines
        ]
        zone = [ZonePoint(x=int(p[0]), y=int(p[1])) for p in active_zone]
        return PipelineStatus(
            running=self.running,
            source=self._config.source if self._config else None,
            frame_index=self._frame_index,
            uptime_seconds=round(uptime, 1),
            fps=round(fps, 1),
            demo_mode=False,
            preroll_pct=self._preroll_pct if self.running else None,
            count_mode=self._count_mode,
            counting_lines=lines,
            counting_zone=zone,
        )

    async def start(self, config: PipelineConfig) -> None:
        async with self._lock:
            if self.running:
                await self._stop_pipeline()

            # Always open a persistent session for database logging
            self._session_id = uuid.uuid4().hex
            self._camera_id = str(config.source)
            import server.event_store as events_db
            events_db.open_session(self._session_id, self._camera_id, source_detail="RoadsEye pipeline")

            if not config.counting_lines and self._default_lines:
                config.counting_lines = list(self._default_lines)
            if not config.counting_zone and self._default_zone:
                config.counting_zone = list(self._default_zone)

            # Determine whether buffer delay is needed: only for remote live network streams
            src_str = str(config.source).lower()
            is_remote_live = src_str.startswith(("http://", "https://", "rtsp://")) or ".m3u8" in src_str
            self._eff_delay = self._stream_delay if is_remote_live else 0.0

            self._delay_buffer.clear()
            self._delay_start = time.time() if self._eff_delay > 0 else None
            self._preroll_pct = 0 if self._eff_delay > 0 else None
            self._counting_armed = self._eff_delay <= 0

            self._config = config
            self._count_mode = config.count_mode
            self._pipeline = Pipeline(config, counting_enabled=self._counting_armed)
            self._pipeline.set_counting_enabled(self._counting_armed)

            # Load cumulative counts for today to maintain continuous tally across reconnects/reloads
            today_counts = events_db.get_today_counts(camera_id=self._camera_id)
            self._pipeline.set_today_baseline(today_counts)

            self._frame_index = 0
            self._start_time = time.time()
            self._last_sheets_sync_time = time.time()
            self._task = asyncio.create_task(self._run_loop(), name="pipeline")
            if self._eff_delay > 0:
                self._delay_task = asyncio.create_task(self._release_delay_loop(), name="timeshift-release")
            else:
                self._delay_task = None

    async def _release_delay_loop(self) -> None:
        """Time-driven timeshift releaser: emits each frame once it is
        STREAM_DELAY_SECONDS old, independent of ingest hiccups. Also drives
        the loading progress and arms counting at 100%."""
        try:
            while True:
                await asyncio.sleep(0.04)
                if self._eff_delay > 0:
                    cutoff = time.time() - self._eff_delay
                    while self._delay_buffer and self._delay_buffer[0][0] <= cutoff:
                        _, ready = self._delay_buffer.popleft()
                        for q in list(self._video_queues):
                            if q.full():
                                try:
                                    q.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                            try:
                                q.put_nowait(ready)
                            except asyncio.QueueFull:
                                pass
                fill = (
                    min(1.0, (time.time() - self._delay_start) / self._eff_delay)
                    if self._eff_delay > 0 and self._delay_start else 1.0
                )
                self._preroll_pct = None if fill >= 1.0 else int(fill * 100)
                if not self._counting_armed and fill >= 1.0:
                    self._counting_armed = True
                    self._start_time = time.time()  # Uptime starts when playback starts
                    if self._pipeline:
                        self._pipeline.set_counting_enabled(True)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_pipeline()

    async def _stop_pipeline(self) -> None:
        if self._delay_task and not self._delay_task.done():
            self._delay_task.cancel()
            try:
                await self._delay_task
            except asyncio.CancelledError:
                pass
        self._delay_task = None
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
        self._delay_buffer.clear()
        # Mandatory autosave on disconnect/stop
        import server.event_store as events_db
        events_db.writer.flush()
        had_persistent_session = self._session_id is not None
        if self._session_id:
            events_db.close_session(self._session_id, status="stopped")
            self._session_id = None

        # Mandatory auto-sync to Google Sheets upon disconnect/stop
        if had_persistent_session:
            try:
                import server.gsheets_exporter as sheets_sync
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

    def set_zone(self, counting_zone: list[tuple[int, int]] | None) -> None:
        """Live update of Per-ID counting zone polygon."""
        pts = [(int(round(p[0])), int(round(p[1]))) for p in (counting_zone or [])]
        self._default_zone = pts
        if self._config is not None:
            self._config.counting_zone = pts
        if self._pipeline is not None:
            self._pipeline.set_zone(pts)

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
        from server.state_buffer import ring_push, upsert_hourly
        import server.event_store as events_db

        try:
            last_metrics_push = 0.0
            async for jpeg_bytes, raw_metrics in self._pipeline.run_async():
                if self._frame_index == 0 and self._eff_delay <= 0:
                    self._start_time = time.time()
                self._frame_index += 1
                now_ts = time.time()
                has_crossings = bool(raw_metrics.crossings)
                should_push_metrics = has_crossings or (now_ts - last_metrics_push >= _METRICS_MIN_INTERVAL_S)

                # ── Frame delivery: direct for eff_delay <= 0, buffered for timeshift
                if self._eff_delay <= 0:
                    for q in list(self._video_queues):
                        if q.full():
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            q.put_nowait(jpeg_bytes)
                        except asyncio.QueueFull:
                            pass
                else:
                    self._delay_buffer.append((now_ts, jpeg_bytes))

                if not should_push_metrics and self._frame_index % 1500 != 0:
                    continue

                metrics = MetricsMessage.from_frame_metrics(raw_metrics)

                # Persist confirmed counting crossings when armed (loading 100%)
                if self._counting_armed:
                    if metrics.crossings and self._session_id:
                        for c in metrics.crossings:
                            events_db.writer.enqueue(events_db.make_event(
                                camera_id=self._camera_id or "unknown",
                                session_id=self._session_id,
                                vehicle_type=c.class_name,
                                timestamp_epoch=c.timestamp,
                                track_id=c.track_id,
                                confidence=c.confidence,
                                direction=c.direction,
                                counting_line_id=c.line_name,
                            ))

                    # Persist to in-memory ring buffer (for live charts)
                    ring_push(metrics)

                    # Upsert hourly aggregate periodically (every 1500 frames ~1 min)
                    if self._frame_index % 1500 == 0:
                        asyncio.create_task(upsert_hourly(metrics))

                    # Periodic auto backup to Google Sheets (every 15 minutes = 900s)
                    if now_ts - self._last_sheets_sync_time >= 900:
                        self._last_sheets_sync_time = now_ts
                        try:
                            import server.gsheets_exporter as sheets_sync
                            if sheets_sync.is_configured():
                                asyncio.create_task(asyncio.to_thread(sheets_sync.sync_to_google_sheets))
                        except Exception as e:
                            print(f"[PipelineManager] Periodic sheets sync trigger error: {e}")

                # Fan-out metrics
                if should_push_metrics:
                    last_metrics_push = now_ts
                    for q in list(self._metrics_queues):
                        if q.full() and not has_crossings:
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            q.put_nowait(metrics)
                        except asyncio.QueueFull:
                            pass

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
                    import server.gsheets_exporter as sheets_sync
                    if sheets_sync.is_configured():
                        import threading as _threading
                        _threading.Thread(
                            target=sheets_sync.sync_to_google_sheets,
                            name="sheets-sync-onstop",
                            daemon=True,
                        ).start()
                except Exception:
                    pass


# Module-level singleton — imported by routes
manager = PipelineManager()
