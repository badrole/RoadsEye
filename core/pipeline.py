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
from dataclasses import dataclass, field
from typing import AsyncGenerator, Generator

import cv2
import numpy as np

from core.analytics import AnalyticsEngine, CountingLineSpec, FrameMetrics
from core.tracker import Tracker, Track
from core.video_source import VideoSource


def _as_track(row: dict) -> Track:
    """Lightweight Track view over a serialized metrics track dict."""
    t = object.__new__(Track)
    t.track_id = int(row["id"])
    t.bbox = tuple(row["bbox"])
    t.confidence = float(row.get("confidence", 1.0))
    t.class_id = int(row.get("class_id", 0))
    t.class_name = str(row.get("class_name", "unknown"))
    return t


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    source: str | int = 0
    model_path: str | None = None
    confidence_threshold: float = 0.35
    iou_threshold: float = 0.45
    input_size: int = 640
    skip_frames: int = 0
    counting_lines: list[CountingLineSpec] = field(default_factory=list)
    pixels_per_meter: float = 10.0
    loop: bool = False
    draw_annotations: bool = True
    jpeg_quality: int = 80

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
        if self._initial_counts:
            self._analytics.set_today_baseline(self._initial_counts)

        if cfg.demo_mode:
            print("[Pipeline] Source  : DEMO (synthetic)")
        else:
            print(f"[Pipeline] Source  : {self._source}")
            print(f"[Pipeline] Detector: {self._detector}")
        print(f"[Pipeline] Lines   : {[s.name for s in cfg.counting_lines]}")
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

    async def run_async(self) -> AsyncGenerator[tuple[bytes, FrameMetrics], None]:
        self.setup()
        self._running = True
        loop = asyncio.get_event_loop()
        try:
            if self.config.demo_mode:
                async for result in self._run_demo_async(loop):
                    yield result
            else:
                # Threaded reader + paced output: video flows at the source's
                # native fps while the detector runs on a stride, so the stream
                # stays smooth instead of skipping between processed frames.
                fps = self._source.fps or 25.0
                if fps < 10.0:
                    fps = 10.0
                frame_interval = 1.0 / fps                # Detect every paced frame — GPU inference (~20 ms) comfortably
                # fits the source interval, and skipping frames makes small
                # fast objects (motorcycles) flicker out of the tracker.
                stride = max(self.config.skip_frames, 0)

                latest: dict = {"frame": None, "idx": 0, "seq": 0}
                lock = threading.Lock()

                closing = threading.Event()

                def reader() -> None:
                    fails = 0
                    reopens = 0
                    t_prev = time.monotonic()
                    while self._running and not closing.is_set():
                        frame, meta = self._source.read()
                        if frame is None:
                            if closing.is_set():
                                break
                            if not self._source.is_live:
                                self._running = False
                                break
                            fails += 1
                            if fails >= 25:
                                if reopens < 5:
                                    reopens += 1
                                    print(f"[Pipeline] Live source stalled — re-opening ({reopens}/5)")
                                    try:
                                        self._source.close()
                                        self._source.open()
                                        t_prev = time.monotonic()
                                    except Exception as exc:
                                        print(f"[Pipeline] Re-open failed: {exc}")
                                    fails = 0
                                    continue
                                self._running = False
                                break
                            time.sleep(0.04)
                            continue
                        fails = 0
                        reopens = 0
                        with lock:
                            latest["frame"] = frame
                            latest["idx"] = meta.index
                            latest["seq"] += 1

                        # Smooth nominal pace to prevent runaway reading
                        now = time.monotonic()
                        elapsed = now - t_prev
                        wait = frame_interval - elapsed
                        if wait > 0:
                            time.sleep(min(wait, frame_interval))
                        t_prev = time.monotonic()

                rt = threading.Thread(target=reader, name="video-reader", daemon=True)
                rt.start()

                c_lock = threading.Lock()
                cache: dict = {"jpeg": None, "metrics": None, "seq": 0, "idx": -1}

                def worker() -> None:
                    """Dedicated inference thread: grabs newest frame, runs
                    detect+track+draw+encode. Each frame is consumed exactly
                    once via the seq counter — prevents double-counting."""
                    last_consumed_seq = 0
                    while self._running and not closing.is_set():
                        with lock:
                            frame = latest["frame"]
                            idx = latest["idx"]
                            rseq = latest["seq"]
                        if frame is None or rseq == last_consumed_seq:
                            time.sleep(0.003)
                            continue
                        last_consumed_seq = rseq
                        try:
                            jpeg, met = self._process_live_frame(frame, idx)
                        except Exception as exc:
                            print(f"[Pipeline] inference worker: {exc}")
                            time.sleep(0.02)
                            continue
                        with c_lock:
                            cache["jpeg"] = jpeg
                            cache["metrics"] = met
                            cache["seq"] += 1
                            cache["idx"] = idx

                wt = threading.Thread(target=worker, name="inference-worker", daemon=True)
                wt.start()

                last_drawn_seq = -1
                try:
                    while self._running:
                        t0 = time.perf_counter()
                        with c_lock:
                            jpeg_bytes, cached_metrics, produced = (
                                cache["jpeg"], cache["metrics"], cache["seq"]
                            )
                        if jpeg_bytes is None or cached_metrics is None or produced == last_drawn_seq:
                            await asyncio.sleep(0.003)
                            continue

                        last_drawn_seq = produced
                        yield jpeg_bytes, cached_metrics

                        wait = frame_interval - (time.perf_counter() - t0)
                        if wait > 0:
                            await asyncio.sleep(wait)
                finally:
                    closing.set()
                    rt.join(timeout=2.0)
                    wt.join(timeout=2.0)
                    self.teardown()
        finally:
            self._running = False

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
        return self._draw(frame, [_as_track(t) for t in metrics.tracks], metrics)

    def _process_demo_frame(self) -> tuple[bytes, FrameMetrics]:
        frame, detections = self._demo_gen.generate()
        tracks   = self._tracker.update(detections)
        metrics  = self._analytics.update(tracks)
        if metrics.crossings:
            for c in metrics.crossings:
                lname = c.get("line_name") if isinstance(c, dict) else getattr(c, "line_name", None)
                tid = c.get("track_id") if isinstance(c, dict) else getattr(c, "track_id", None)
                if lname:
                    self._line_flash[lname] = 8
                if tid is not None:
                    self._crossed_flash[tid] = 10
        annotated = self._draw(frame, tracks, metrics) if self.config.draw_annotations else frame
        return self._encode_jpeg(annotated), metrics

    def _process_live_frame(
        self, frame: np.ndarray, _frame_index: int
    ) -> tuple[bytes, FrameMetrics]:
        detections = self._detector.detect(frame)
        tracks     = self._tracker.update(detections)
        metrics    = self._analytics.update(tracks)
        if metrics.crossings:
            for c in metrics.crossings:
                lname = c.get("line_name") if isinstance(c, dict) else getattr(c, "line_name", None)
                tid = c.get("track_id") if isinstance(c, dict) else getattr(c, "track_id", None)
                if lname:
                    self._line_flash[lname] = 8
                if tid is not None:
                    self._crossed_flash[tid] = 10
        annotated  = self._draw(frame, tracks, metrics) if self.config.draw_annotations else frame
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
            self.config.counting_lines = list(counting_lines)
            if self._analytics is not None:
                self._analytics.set_line_specs(counting_lines)

    # ── Annotation ────────────────────────────────────────────────────────────

    def _draw(
        self, frame: np.ndarray, tracks: list, metrics: FrameMetrics
    ) -> np.ndarray:
        out = frame.copy()

        # Draw each counting line with its label and live count (flashing on crossing)
        for idx, spec in enumerate(self.config.counting_lines):
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
        for track in tracks:
            x1, y1, x2, y2 = [int(v) for v in track.bbox]
            is_counted = self._crossed_flash.get(track.track_id, 0) > 0
            if is_counted:
                self._crossed_flash[track.track_id] -= 1
                color = (0, 255, 0)      # neon green highlight on crossed vehicle
                box_thick = 3
                hit_badge = " [COUNTED]"
            else:
                color = self._COLORS[track.class_id % len(self._COLORS)]
                box_thick = 2
                hit_badge = ""

            cv2.rectangle(out, (x1, y1), (x2, y2), color, box_thick)

            # Draw vehicle tracking centroid dot (gives instant visual proof when center touches line)
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
        hud = [
            f"Count: {metrics.total_count}",
            f"In frame: {metrics.vehicles_in_frame}",
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
    parser.add_argument("--conf", type=float, default=0.35)
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
