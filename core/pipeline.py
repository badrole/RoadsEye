"""Pipeline orchestrator: VideoSource → Detector → Tracker → Analytics.

Demo mode: set source="demo" — runs a synthetic scene with no ONNX model required.

Standalone test:
    python -m core.pipeline --source data/sample_videos/traffic.mp4
    python -m core.pipeline --source demo
"""
from __future__ import annotations

import argparse
import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import AsyncGenerator, Generator

import cv2
import numpy as np

from core.analytics import AnalyticsEngine, CountingLineSpec, FrameMetrics
from core.tracker import Tracker, Track
from core.video_source import VideoSource


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    source: str | int = 0
    model_path: str | None = None
    confidence_threshold: float = 0.25
    iou_threshold: float = 0.45
    input_size: int = 640
    skip_frames: int = 0
    counting_lines: list[CountingLineSpec] = field(default_factory=list)
    pixels_per_meter: float = 10.0
    loop: bool = False
    draw_annotations: bool = True
    jpeg_quality: int = 72
    count_mode: str = "line"   # "line" = tripwire crossings, "id" = every tracker ID once

    @property
    def demo_mode(self) -> bool:
        return str(self.source).lower() == "demo"


class _ReusedTrack:
    """Lightweight track proxy for reusing cached detections on fresh frames."""
    def __init__(self, d: dict) -> None:
        self.track_id = int(d.get("id", 0))
        self.bbox = tuple(d.get("bbox", (0, 0, 0, 0)))
        self.class_id = int(d.get("class_id", 0))
        self.class_name = str(d.get("class_name", "vehicle"))


def _as_track(d: dict) -> _ReusedTrack:
    return _ReusedTrack(d)


# ── Pipeline ──────────────────────────────────────────────────────────────────

class Pipeline:
    """End-to-end detection+tracking+analytics pipeline.

    Usage (standalone)::

        pipeline = Pipeline(PipelineConfig(source="traffic.mp4"))
        for frame_bytes, metrics in pipeline.run_sync():
            ...

    Usage (async, for FastAPI)::

        async for frame_bytes, metrics in pipeline.run_async():
            await ws.send_bytes(frame_bytes)
    """

    # Class colours BGR
    _COLORS: list[tuple[int, int, int]] = [
        (255, 100, 100),   # car        — blue
        (100, 200, 255),   # bus        — orange
        (100, 255, 100),   # motorcycle — green
        (100, 180, 255),   # truck      — yellow
    ]

    # Counting-line label colours (one per line, cycled)
    _LINE_COLORS: list[tuple[int, int, int]] = [
        (0, 255, 255),
        (255, 200, 0),
        (180, 0, 255),
        (0, 255, 100),
    ]

    def __init__(self, config: PipelineConfig | None = None, counting_enabled: bool = True) -> None:
        self.config = config or PipelineConfig()
        self._running = False
        self._counting_enabled = counting_enabled

        self._source:    VideoSource | None = None
        self._detector                      = None   # core.detector.Detector
        self._tracker:   Tracker | None     = None
        self._analytics: AnalyticsEngine | None = None
        self._demo_gen                      = None   # core.demo_generator.DemoGenerator
        self._initial_counts: dict[str, int] = {}
        self._line_flash: dict[str, int] = {}       # line_name -> remaining flash frames
        self._crossed_flash: dict[int, int] = {}    # track_id -> remaining flash frames
        self._analytics_lock = threading.Lock()     # worker thread vs live API edits

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def setup(self) -> None:
        cfg = self.config

        if cfg.demo_mode:
            from core.demo_generator import DemoGenerator
            self._demo_gen = DemoGenerator(fps=25.0)
            fps = 25.0
        else:
            from core.detector import Detector
            self._source = VideoSource(cfg.source, loop=cfg.loop)
            self._source.open()
            self._detector = Detector(
                model_path=cfg.model_path,
                confidence_threshold=cfg.confidence_threshold,
                iou_threshold=cfg.iou_threshold,
                input_size=cfg.input_size,
            )
            fps = self._source.fps or 25.0

        self._tracker = Tracker(
            frame_rate=int(fps),
            track_activation_threshold=0.25,
            lost_track_buffer=30,
            minimum_matching_threshold=0.8,
        )
        self._analytics = AnalyticsEngine(
            counting_lines=cfg.counting_lines or [],
            pixels_per_meter=cfg.pixels_per_meter,
            fps=fps,
        )
        self._analytics.counting_enabled = self._counting_enabled
        self._analytics.set_count_mode(cfg.count_mode)
        if self._initial_counts:
            self._analytics.set_today_baseline(self._initial_counts)

        if cfg.demo_mode:
            print("[Pipeline] Source  : DEMO (synthetic)")
        else:
            print(f"[Pipeline] Source  : {self._source}")
            print(f"[Pipeline] Detector: {self._detector}")
        print(f"[Pipeline] Lines   : {[s.name for s in cfg.counting_lines]}")
        print(f"[Pipeline] Count   : {cfg.count_mode} mode")
        print(f"[Pipeline] Skip    : every {cfg.skip_frames + 1} frames")

    def teardown(self) -> None:
        if self._source:
            self._source.close()
        self._running = False

    def stop(self) -> None:
        self._running = False

    @property
    def analytics(self) -> AnalyticsEngine | None:
        return self._analytics

    def set_today_baseline(self, baseline: dict[str, int]) -> None:
        self._initial_counts = dict(baseline or {})
        if self._analytics:
            self._analytics.set_today_baseline(self._initial_counts)

    def set_counting_enabled(self, enabled: bool) -> None:
        """Gate tallying (side state stays warm) — used during timeshift load."""
        if self._analytics is not None:
            self._analytics.counting_enabled = bool(enabled)

    def set_count_mode(self, mode: str) -> None:
        """Switch line/ID counting on a running pipeline without losing the tally."""
        with self._analytics_lock:
            if self._analytics is not None:
                self._analytics.set_count_mode(mode)
            self.config.count_mode = mode

    # ── Sync runner ───────────────────────────────────────────────────────────

    def run_sync(self) -> Generator[tuple[bytes, FrameMetrics], None, None]:
        self.setup()
        self._running = True
        try:
            if self.config.demo_mode:
                yield from self._run_demo_sync()
            else:
                for frame, meta in self._source.stream(skip=self.config.skip_frames):
                    if not self._running:
                        break
                    yield self._process_live_frame(frame, meta.index)
        finally:
            self.teardown()

    def _run_demo_sync(self) -> Generator[tuple[bytes, FrameMetrics], None, None]:
        frame_interval = 1.0 / 25.0
        while self._running:
            t0 = time.perf_counter()
            yield self._process_demo_frame()
            elapsed = time.perf_counter() - t0
            wait = frame_interval - elapsed
            if wait > 0:
                time.sleep(wait)

    # ── Async runner ──────────────────────────────────────────────────────────

    JITTER_BUFFER_S = 0.35

    async def run_async(self) -> AsyncGenerator[tuple[bytes, FrameMetrics], None]:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.setup)
        self._running = True
        try:
            if self.config.demo_mode:
                async for result in self._run_demo_async(loop):
                    yield result
            else:
                async for result in self._run_live_async(loop):
                    yield result
        finally:
            self._running = False

    async def _run_live_async(
        self, loop: asyncio.AbstractEventLoop
    ) -> AsyncGenerator[tuple[bytes, FrameMetrics], None]:
        """4-stage concurrent pipeline: reader → inference → encoder → pacer.

        Separating GPU inference from CPU drawing + JPEG encoding allows the GPU
        and CPU to work in parallel on consecutive frames instead of waiting on
        each other sequentially.
        """
        fps = min(max(self._source.fps or 25.0, 10.0), 60.0)
        interval = 1.0 / fps
        live = self._source.is_live
        is_hls = getattr(self._source, "is_hls", False)
        drop_old = live and not is_hls
        stride = max(self.config.skip_frames, 0)
        prebuffer = max(2, int(fps * self.JITTER_BUFFER_S)) if is_hls else 2
        raw_cap = int(fps * 2) if is_hls else (6 if live else int(fps * 2))
        enc_cap = 8
        out_cap = int(fps * 2)
        burst_threshold = max(4, int(fps * 0.6))

        raw: deque = deque()
        raw_cv = threading.Condition()
        enc: deque = deque()
        enc_cv = threading.Condition()
        out: deque = deque()
        out_cv = threading.Condition()
        closing = threading.Event()
        src_done = threading.Event()
        inf_done = threading.Event()
        enc_done = threading.Event()
        aq: asyncio.Queue = asyncio.Queue()

        def alive() -> bool:
            return self._running and not closing.is_set()

        def reader() -> None:
            fails = reopens = 0
            try:
                while alive():
                    frame, meta = self._source.read()
                    if frame is None:
                        if not alive() or not self._source.is_live:
                            break
                        fails += 1
                        if fails >= 25:
                            if reopens >= 5:
                                break
                            reopens += 1
                            print(f"[Pipeline] Live source stalled — re-opening ({reopens}/5)")
                            try:
                                self._source.close()
                                self._source.open()
                            except Exception as exc:
                                print(f"[Pipeline] Re-open failed: {exc}")
                            fails = 0
                            continue
                        time.sleep(0.02)
                        continue
                    fails = reopens = 0
                    with raw_cv:
                        if drop_old:
                            if len(raw) >= raw_cap:
                                raw.popleft()   # shed oldest stale frame
                        else:
                            while len(raw) >= raw_cap and alive():
                                raw_cv.wait(0.02)
                        raw.append((frame, meta.index))
                        raw_cv.notify_all()
            finally:
                src_done.set()
                with raw_cv:
                    raw_cv.notify_all()

        def inference_worker() -> None:
            n = 0
            last_met: FrameMetrics | None = None
            last_tracks: list = []
            try:
                while alive():
                    with raw_cv:
                        while not raw and not src_done.is_set() and alive():
                            raw_cv.wait(0.02)
                        if not raw:
                            if src_done.is_set():
                                break
                            continue
                        raw_backlog = len(raw)
                        frame, idx = raw.popleft()
                        raw_cv.notify_all()
                    try:
                        reuse = (
                            last_met is not None
                            and (
                                (stride and (n % (stride + 1) != 0))
                                or (drop_old and raw_backlog > burst_threshold and (n % 2 == 1))
                            )
                        )
                        if reuse:
                            tracks = last_tracks
                            met = replace(last_met, crossings=[])
                        else:
                            tracks, met = self._infer_frame(frame, idx)
                            last_met = met
                            last_tracks = tracks
                    except Exception as exc:
                        print(f"[Pipeline] inference worker: {exc}")
                        continue
                    finally:
                        n += 1
                    with enc_cv:
                        if drop_old:
                            if len(enc) >= enc_cap:
                                enc.popleft()
                        else:
                            while len(enc) >= enc_cap and alive():
                                enc_cv.wait(0.02)
                        enc.append((frame, tracks, met))
                        enc_cv.notify_all()
            finally:
                inf_done.set()
                with enc_cv:
                    enc_cv.notify_all()

        def encode_worker() -> None:
            try:
                while alive():
                    with enc_cv:
                        while not enc and not inf_done.is_set() and alive():
                            enc_cv.wait(0.02)
                        if not enc:
                            if inf_done.is_set():
                                break
                            continue
                        frame, tracks, met = enc.popleft()
                        enc_cv.notify_all()
                    try:
                        annotated = (
                            self._draw(frame, tracks, met, in_place=True)
                            if self.config.draw_annotations
                            else frame
                        )
                        jpeg = self._encode_jpeg(annotated)
                    except Exception as exc:
                        print(f"[Pipeline] encode worker: {exc}")
                        continue
                    with out_cv:
                        if drop_old:
                            if len(out) >= out_cap:
                                out.popleft()
                        else:
                            while len(out) >= out_cap and alive():
                                out_cv.wait(0.02)
                        out.append((jpeg, met))
                        out_cv.notify_all()
            finally:
                enc_done.set()
                with out_cv:
                    out_cv.notify_all()

        def emit(item) -> None:
            try:
                loop.call_soon_threadsafe(aq.put_nowait, item)
            except RuntimeError:
                pass   # event loop already closed (app shutdown)

        def pacer() -> None:
            buffering = True
            next_due = 0.0
            try:
                while alive():
                    with out_cv:
                        if buffering and len(out) < prebuffer and not enc_done.is_set():
                            out_cv.wait(0.01)
                            continue
                        if buffering:
                            buffering = False
                            next_due = time.perf_counter()
                        if not out:
                            if enc_done.is_set():
                                break
                            out_cv.wait(0.005)
                            continue
                        depth = len(out)
                        item = out.popleft()
                        out_cv.notify_all()

                    # Smoothly adapt cadence without re-entering a hard buffer freeze
                    step = interval
                    if drop_old:
                        if depth > prebuffer * 3:
                            step *= 0.65
                        elif depth > prebuffer * 2:
                            step *= 0.85
                        elif depth <= 1:
                            step *= 1.06

                    now = time.perf_counter()
                    delay = next_due - now
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -0.1:
                        next_due = now
                    next_due += step
                    emit(item)
            finally:
                emit(None)

        threads = [
            threading.Thread(target=reader, name="video-reader", daemon=True),
            threading.Thread(target=inference_worker, name="inference-worker", daemon=True),
            threading.Thread(target=encode_worker, name="encode-worker", daemon=True),
            threading.Thread(target=pacer, name="frame-pacer", daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            while True:
                item = await aq.get()
                if item is None:
                    break
                yield item
        finally:
            closing.set()
            for cv in (raw_cv, enc_cv, out_cv):
                with cv:
                    cv.notify_all()
            for t in threads:
                t.join(timeout=2.0)
            self.teardown()

    async def _run_demo_async(
        self, loop: asyncio.AbstractEventLoop
    ) -> AsyncGenerator[tuple[bytes, FrameMetrics], None]:
        frame_interval = 1.0 / 25.0
        while self._running:
            t0 = loop.time()
            result = await loop.run_in_executor(None, self._process_demo_frame)
            yield result
            elapsed = loop.time() - t0
            wait = frame_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)

    # ── Frame processing ──────────────────────────────────────────────────────

    def _draw_reuse(self, frame: np.ndarray, metrics: FrameMetrics) -> np.ndarray:
        """Annotate a frame using metrics."""
        return self._draw(frame, [_as_track(t) for t in metrics.tracks], metrics, in_place=True)

    def _apply_crossing_flash(self, metrics: FrameMetrics) -> None:
        if metrics.crossings:
            for c in metrics.crossings:
                lname = c.get("line_name") if isinstance(c, dict) else getattr(c, "line_name", None)
                tid = c.get("track_id") if isinstance(c, dict) else getattr(c, "track_id", None)
                if lname:
                    self._line_flash[lname] = 8
                if tid is not None:
                    self._crossed_flash[tid] = 10

    def _infer_frame(
        self, frame: np.ndarray, _frame_index: int
    ) -> tuple[list, FrameMetrics]:
        detections = self._detector.detect(frame)
        tracks     = self._tracker.update(detections)
        with self._analytics_lock:
            metrics = self._analytics.update(tracks)
        self._apply_crossing_flash(metrics)
        return tracks, metrics

    def _process_demo_frame(self) -> tuple[bytes, FrameMetrics]:
        frame, detections = self._demo_gen.generate()
        tracks   = self._tracker.update(detections)
        metrics  = self._analytics.update(tracks)
        self._apply_crossing_flash(metrics)
        annotated = self._draw(frame, tracks, metrics, in_place=True) if self.config.draw_annotations else frame
        return self._encode_jpeg(annotated), metrics

    def _process_live_frame(
        self, frame: np.ndarray, _frame_index: int
    ) -> tuple[bytes, FrameMetrics]:
        tracks, metrics = self._infer_frame(frame, _frame_index)
        annotated = self._draw(frame, tracks, metrics, in_place=True) if self.config.draw_annotations else frame
        return self._encode_jpeg(annotated), metrics

    # ── Live line editing ─────────────────────────────────────────────────────

    @property
    def line_specs(self) -> list[CountingLineSpec]:
        """Return current counting line specs."""
        if self._analytics is not None:
            return self._analytics.line_specs
        return list(self.config.counting_lines)

    def set_lines(self, counting_lines: list[CountingLineSpec] | None = None) -> None:
        """Update counting lines on a running pipeline (interactive editor)."""
        if counting_lines is not None:
            with self._analytics_lock:
                self.config.counting_lines = list(counting_lines)
                if self._analytics is not None:
                    self._analytics.set_line_specs(counting_lines)

    # ── Annotation ────────────────────────────────────────────────────────────

    def _draw(
        self,
        frame: np.ndarray,
        tracks: list,
        metrics: FrameMetrics,
        *,
        in_place: bool = False,
    ) -> np.ndarray:
        out = frame if in_place else frame.copy()
        id_mode = self.config.count_mode == "id"

        # Draw each counting line with its label and live count (flashing on crossing)
        for idx, spec in enumerate([] if id_mode else self.config.counting_lines):
            is_flashing = self._line_flash.get(spec.name, 0) > 0
            if is_flashing:
                self._line_flash[spec.name] -= 1
                color = (0, 255, 0)      # neon green flash on crossing
                thickness = 4
                tag = " [HIT!]"
            else:
                color = self._LINE_COLORS[idx % len(self._LINE_COLORS)]
                thickness = 2
                tag = ""

            p1 = (int(spec.p1[0]), int(spec.p1[1]))
            p2 = (int(spec.p2[0]), int(spec.p2[1]))
            cv2.line(out, p1, p2, color, thickness)
            count = metrics.count_per_line.get(spec.name, 0)
            label = f"{spec.name}: {count}{tag}"
            cv2.putText(out, label, (p1[0] + 4, p1[1] + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        # Draw track boxes and tracking centroids
        speed_map = {t["id"]: t.get("speed_kmh") for t in metrics.tracks}
        in_frame_motors = 0
        for track in tracks:
            if getattr(track, "class_name", "") == "motorcycle":
                in_frame_motors += 1
            x1, y1, x2, y2 = [int(v) for v in track.bbox]
            is_counted = self._crossed_flash.get(track.track_id, 0) > 0
            if is_counted:
                self._crossed_flash[track.track_id] -= 1
                color = (0, 255, 0)      # neon green highlight on counted vehicle
                box_thick = 3
                hit_badge = " [COUNTED]"
            else:
                color = self._COLORS[track.class_id % len(self._COLORS)]
                box_thick = 2
                hit_badge = ""

            cv2.rectangle(out, (x1, y1), (x2, y2), color, box_thick)

            # Draw vehicle tracking centroid dot
            cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
            cv2.circle(out, (cx, cy), 3, (0, 255, 255), -1)
            cv2.circle(out, (cx, cy), 4, (0, 0, 0), 1)

            spd = speed_map.get(track.track_id)
            spd_str = f" {spd:.0f}km/h" if spd is not None else ""
            label = f"#{track.track_id} {track.class_name}{spd_str}{hit_badge}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(out, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(out, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # HUD overlay
        motor_total = metrics.count_per_class.get("motorcycle", 0)
        hud = [
            f"Count ({'per ID' if id_mode else 'per line'}): {metrics.total_count} (Motor: {motor_total})",
            f"In frame: {metrics.vehicles_in_frame} (Motor: {in_frame_motors})",
            f"Avg speed: {metrics.avg_speed_kmh:.1f} km/h",
        ]
        for i, line in enumerate(hud):
            cv2.putText(out, line, (10, 30 + i * 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(out, line, (10, 30 + i * 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 1)

        return out

    def _encode_jpeg(self, frame: np.ndarray) -> bytes:
        ok, buf = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality]
        )
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return buf.tobytes()


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TrafficVision pipeline test")
    parser.add_argument("--source", default="0",
                        help="Video file, webcam index, RTSP URL, or 'demo'")
    parser.add_argument("--model", default=None)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--no-display", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    source: str | int = args.source
    if source != "demo":
        try:
            source = int(source)
        except ValueError:
            pass

    config = PipelineConfig(
        source=source,
        model_path=args.model,
        confidence_threshold=args.conf,
        skip_frames=args.skip,
        loop=args.loop,
    )

    pipeline  = Pipeline(config)
    frame_count = 0
    t_start     = time.perf_counter()

    try:
        for jpeg_bytes, metrics in pipeline.run_sync():
            frame_count += 1

            if not args.no_display:
                arr   = np.frombuffer(jpeg_bytes, np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                cv2.imshow("TrafficVision", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    pipeline.stop()
                    break

            if frame_count % 30 == 0:
                elapsed = time.perf_counter() - t_start
                fps_act = frame_count / elapsed
                print(
                    f"[{frame_count:5d}] FPS={fps_act:.1f} | "
                    f"count={metrics.total_count} | "
                    f"in_frame={metrics.vehicles_in_frame} | "
                    f"speed={metrics.avg_speed_kmh:.1f}km/h"
                )

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cv2.destroyAllWindows()
        elapsed = time.perf_counter() - t_start
        print(f"\nProcessed {frame_count} frames in {elapsed:.1f}s "
              f"({frame_count / max(elapsed, 1):.1f} FPS)")


if __name__ == "__main__":
    main()
