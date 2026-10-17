"""Pipeline orchestrator: VideoSource → Detector → Tracker → Analytics.

Demo mode: set source="demo" — runs a synthetic scene with no ONNX model required.

Standalone test:
    python -m core.pipeline --source data/sample_videos/traffic.mp4
    python -m core.pipeline --source demo
"""
from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Generator

import cv2
import numpy as np

from core.analytics import AnalyticsEngine, CountingLineSpec, FrameMetrics
from core.tracker import Tracker
from core.video_source import VideoSource


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

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self._running = False

        self._source:    VideoSource | None = None
        self._detector                      = None   # core.detector.Detector
        self._tracker:   Tracker | None     = None
        self._analytics: AnalyticsEngine | None = None
        self._demo_gen                      = None   # core.demo_generator.DemoGenerator

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

        self._tracker   = Tracker(frame_rate=int(fps))
        self._analytics = AnalyticsEngine(
            counting_lines=cfg.counting_lines or [],
            pixels_per_meter=cfg.pixels_per_meter,
            fps=fps,
        )

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
                for frame, meta in self._source.stream(skip=self.config.skip_frames):
                    if not self._running:
                        break
                    result = await loop.run_in_executor(
                        None, self._process_live_frame, frame, meta.index
                    )
                    yield result
                    await asyncio.sleep(0)
        finally:
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

    def _process_demo_frame(self) -> tuple[bytes, FrameMetrics]:
        frame, detections = self._demo_gen.generate()
        tracks   = self._tracker.update(detections)
        metrics  = self._analytics.update(tracks)
        annotated = self._draw(frame, tracks, metrics) if self.config.draw_annotations else frame
        return self._encode_jpeg(annotated), metrics

    def _process_live_frame(
        self, frame: np.ndarray, _frame_index: int
    ) -> tuple[bytes, FrameMetrics]:
        detections = self._detector.detect(frame)
        tracks     = self._tracker.update(detections)
        metrics    = self._analytics.update(tracks)
        annotated  = self._draw(frame, tracks, metrics) if self.config.draw_annotations else frame
        return self._encode_jpeg(annotated), metrics

    # ── Annotation ────────────────────────────────────────────────────────────

    def _draw(
        self, frame: np.ndarray, tracks: list, metrics: FrameMetrics
    ) -> np.ndarray:
        out = frame.copy()

        # Draw each counting line with its label and live count
        for idx, spec in enumerate(self.config.counting_lines):
            color = self._LINE_COLORS[idx % len(self._LINE_COLORS)]
            p1 = (int(spec.p1[0]), int(spec.p1[1]))
            p2 = (int(spec.p2[0]), int(spec.p2[1]))
            cv2.line(out, p1, p2, color, 2)
            count = metrics.count_per_line.get(spec.name, 0)
            label = f"{spec.name}: {count}"
            cv2.putText(out, label, (p1[0] + 4, p1[1] + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        # Draw track boxes
        speed_map = {t["id"]: t.get("speed_kmh") for t in metrics.tracks}
        for track in tracks:
            x1, y1, x2, y2 = [int(v) for v in track.bbox]
            color = self._COLORS[track.class_id % len(self._COLORS)]
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

            spd = speed_map.get(track.track_id)
            spd_str = f" {spd:.0f}km/h" if spd is not None else ""
            label = f"#{track.track_id} {track.class_name}{spd_str}"
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
