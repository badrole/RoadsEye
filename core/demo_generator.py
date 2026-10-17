"""Synthetic traffic scene generator for demo mode.

Produces realistic-looking video frames and Detection objects without requiring
an ONNX model or a real camera. Use by setting source="demo" in PipelineConfig.

Scene layout (1280×720):
  - Sky region above road
  - 2-lane road across the middle of the frame
  - Ground below road
  - Lane 0 (upper half): vehicles travel left → right
  - Lane 1 (lower half): vehicles travel right → left
  - Vehicles are class-sized colored rectangles with windshield highlights
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import cv2
import numpy as np

from core.detector import Detection


# ── Vehicle specs per class ───────────────────────────────────────────────────
# w/h in pixels (for a 1280×720 frame); weight = relative spawn frequency

_SPECS: dict[int, dict] = {
    0: dict(name="car",        w=(60, 100), h=(34, 52), weight=6),
    1: dict(name="bus",        w=(110, 155), h=(58, 80), weight=1),
    2: dict(name="motorcycle", w=(24,  42),  h=(20, 34), weight=2),
    3: dict(name="truck",      w=(95, 145),  h=(52, 76), weight=2),
}

# BGR colours for each class
_COLORS: dict[int, tuple[int, int, int]] = {
    0: (200, 130,  60),   # car      — blue-ish
    1: ( 60, 170, 220),   # bus      — orange-ish
    2: ( 60, 210,  90),   # motorcycle — green
    3: ( 60, 140, 215),   # truck    — yellow-ish
}


# ── Internal vehicle state ────────────────────────────────────────────────────

@dataclass
class _Vehicle:
    x: float               # left edge (pixels, can be negative when entering)
    y: float               # top edge (pixels)
    w: int                 # width  (pixels)
    h: int                 # height (pixels)
    class_id: int
    speed_px: float        # pixels per frame, signed (+x or -x direction)
    confidence: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2


# ── Generator ─────────────────────────────────────────────────────────────────

class DemoGenerator:
    """Generates synthetic (frame, detections) pairs at a configurable FPS.

    Usage::

        gen = DemoGenerator(fps=25.0)
        frame, detections = gen.generate()   # call once per pipeline tick

    The caller is responsible for timing — ``generate()`` returns immediately.
    """

    # Road geometry as fraction of frame height
    ROAD_TOP_FRAC    = 0.35
    ROAD_BOTTOM_FRAC = 0.85

    TARGET_VEHICLES = 8   # try to keep this many in-frame at all times
    MAX_VEHICLES    = 14

    # Pixels-per-metre assumed by the speed estimator (must match PipelineConfig)
    PIXELS_PER_METRE = 10.0

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: float = 25.0,
        seed: int = 42,
    ) -> None:
        self.width  = width
        self.height = height
        self.fps    = fps

        self._rng          = random.Random(seed)
        self._vehicles:    list[_Vehicle] = []
        self._frame_count: int = 0

        # Precompute road geometry
        self._road_top    = int(self.ROAD_TOP_FRAC    * height)
        self._road_bottom = int(self.ROAD_BOTTOM_FRAC * height)
        self._road_h      = self._road_bottom - self._road_top

        # Lane centre Y positions
        self._lane_cy = [
            self._road_top + self._road_h // 4,
            self._road_top + 3 * self._road_h // 4,
        ]

        # Pre-draw the static background to avoid redrawing each frame
        self._bg = self._make_background()

        # Spread initial vehicles across the frame
        for _ in range(self.TARGET_VEHICLES):
            self._spawn(random_x=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self) -> tuple[np.ndarray, list[Detection]]:
        """Advance the simulation one tick. Returns (bgr_frame, detections)."""
        self._frame_count += 1

        # Possibly spawn a new vehicle
        if (
            len(self._vehicles) < self.TARGET_VEHICLES
            and self._rng.random() < 0.20
        ):
            self._spawn()

        # Move and cull out-of-frame vehicles
        self._vehicles = [
            v for v in self._vehicles
            if -v.w <= (v.x + v.speed_px) <= self.width
        ]
        for v in self._vehicles:
            v.x += v.speed_px

        frame = self._render()
        detections = self._make_detections()
        return frame, detections

    # ── Internal ──────────────────────────────────────────────────────────────

    def _spawn(self, random_x: bool = False) -> None:
        if len(self._vehicles) >= self.MAX_VEHICLES:
            return

        lane     = self._rng.randint(0, 1)
        direction = 1 if lane == 0 else -1

        classes = list(_SPECS.keys())
        weights = [_SPECS[c]["weight"] for c in classes]
        class_id = self._rng.choices(classes, weights=weights)[0]
        spec = _SPECS[class_id]

        w = self._rng.randint(*spec["w"])
        h = self._rng.randint(*spec["h"])
        y = float(self._lane_cy[lane] - h // 2)

        # Speed: 35–80 km/h → pixels/frame
        # speed_px = (km/h × 1000/3600 m/s) × pixels_per_metre / fps
        speed_kmh   = self._rng.uniform(35, 78)
        speed_px    = (speed_kmh * 1000 / 3600) * self.PIXELS_PER_METRE / self.fps
        speed_px   *= direction

        if random_x:
            x = float(self._rng.randint(0, self.width - w))
        else:
            x = float(-w) if direction > 0 else float(self.width)

        conf = self._rng.uniform(0.52, 0.97)
        self._vehicles.append(
            _Vehicle(x=x, y=y, w=w, h=h, class_id=class_id,
                     speed_px=speed_px, confidence=conf)
        )

    def _make_detections(self) -> list[Detection]:
        detections = []
        for v in self._vehicles:
            # Only emit when vehicle is >50% visible
            visible_w = min(v.x2, self.width) - max(v.x, 0)
            if v.w > 0 and visible_w / v.w > 0.50:
                detections.append(Detection(
                    bbox=(
                        float(max(0, v.x)),
                        float(v.y),
                        float(min(self.width, v.x2)),
                        float(v.y2),
                    ),
                    confidence=v.confidence,
                    class_id=v.class_id,
                ))
        return detections

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _make_background(self) -> np.ndarray:
        bg = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Sky
        sky = np.array([38, 28, 18], dtype=np.uint8)
        bg[: self._road_top] = sky

        # Road surface
        road = np.array([52, 52, 52], dtype=np.uint8)
        bg[self._road_top : self._road_bottom] = road

        # Ground
        ground = np.array([28, 42, 22], dtype=np.uint8)
        bg[self._road_bottom :] = ground

        # Road edge lines
        cv2.line(bg, (0, self._road_top),
                 (self.width, self._road_top), (180, 180, 180), 3)
        cv2.line(bg, (0, self._road_bottom),
                 (self.width, self._road_bottom), (180, 180, 180), 3)

        return bg

    def _render(self) -> np.ndarray:
        frame = self._bg.copy()

        # Scrolling dashed lane divider
        lane_y   = (self._road_top + self._road_bottom) // 2
        dash, gap = 40, 20
        offset   = (self._frame_count * 2) % (dash + gap)
        x = -offset
        while x < self.width:
            x1 = max(0, int(x))
            x2 = min(self.width, int(x + dash))
            if x2 > x1:
                cv2.line(frame, (x1, lane_y), (x2, lane_y), (210, 210, 100), 2)
            x += dash + gap

        # Draw vehicles (sorted by y for a rough depth effect)
        for v in sorted(self._vehicles, key=lambda v: v.y):
            x1, y1 = int(max(0, v.x)), int(v.y)
            x2, y2 = int(min(self.width, v.x + v.w)), int(v.y + v.h)
            if x2 <= x1 or y2 <= y1:
                continue

            color = _COLORS[v.class_id]

            # Body
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, -1)

            # Windshield (lighter rectangle in the top 40% of body)
            ws_x1 = x1 + max(1, int((x2 - x1) * 0.15))
            ws_x2 = x1 + max(2, int((x2 - x1) * 0.85))
            ws_y1 = y1 + max(1, int((y2 - y1) * 0.10))
            ws_y2 = y1 + max(2, int((y2 - y1) * 0.50))
            cv2.rectangle(frame, (ws_x1, ws_y1), (ws_x2, ws_y2),
                          (130, 185, 210), -1)

            # Thin shadow ellipse below vehicle
            cx = int((x1 + x2) / 2)
            cv2.ellipse(frame, (cx, y2 + 3),
                        (max(4, (x2 - x1) // 2), 4),
                        0, 0, 360, (18, 18, 18), -1)

        # "DEMO MODE" badge
        cv2.rectangle(frame, (8, 8), (175, 34), (30, 30, 30), -1)
        cv2.putText(frame, "DEMO MODE", (13, 27),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 210, 255), 2)

        # Subtle timestamp
        ts_str = f"t={self._frame_count / self.fps:.1f}s"
        cv2.putText(frame, ts_str,
                    (self.width - 95, self.height - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (90, 90, 90), 1)

        return frame
