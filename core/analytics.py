"""Traffic analytics engine.

Responsibilities:
- Vehicle counting when tracks cross named counting lines
- Speed estimation from pixel displacement × calibration factor
- Per-class breakdown
- Anomaly detection (count spikes, stopped vehicles)
- Confidence drift monitoring
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.tracker import Track


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class CountingLineSpec:
    """Specification for a named counting line."""
    name: str
    p1: tuple[int, int]
    p2: tuple[int, int]


@dataclass
class SpeedSample:
    track_id: int
    speed_kmh: float
    class_id: int
    timestamp: float


@dataclass
class FrameMetrics:
    """Analytics snapshot for one frame — sent over WebSocket."""
    timestamp: float
    frame_index: int
    # Counts
    total_count: int                        # vehicles counted crossing all lines (summed)
    count_per_class: dict[str, int]         # cumulative per class (from first line)
    count_per_line: dict[str, int]          # cumulative count per named line
    vehicles_in_frame: int                  # currently visible tracks
    # Speed
    avg_speed_kmh: float                    # rolling average over last N samples
    speed_samples: list[float]              # recent speed readings (last 10)
    # Alerts
    alerts: list[str]
    # Raw tracks for frontend overlay
    tracks: list[dict[str, Any]]


@dataclass
class Alert:
    message: str
    severity: str   # "info" | "warning" | "critical"
    timestamp: float = field(default_factory=time.time)


# ── Counting line ─────────────────────────────────────────────────────────────

class CountingLine:
    """A line segment that counts vehicles crossing it.

    The line is defined by two points (x1,y1)→(x2,y2) in pixel coords.
    A crossing is detected when a track's centre moves from one side to the other.
    """

    def __init__(self, p1: tuple[int, int], p2: tuple[int, int]) -> None:
        self.p1 = np.array(p1, dtype=float)
        self.p2 = np.array(p2, dtype=float)
        self._prev_sides: dict[int, float] = {}
        self.total_count: int = 0
        self.count_per_class: dict[str, int] = defaultdict(int)

    def update(self, tracks: list[Track]) -> list[Track]:
        """Check each track for a crossing. Returns newly-crossed tracks."""
        crossed = []
        current_ids = {t.track_id for t in tracks}

        for track in tracks:
            cx, cy = track.center
            side = self._side(cx, cy)

            prev = self._prev_sides.get(track.track_id)
            if prev is not None and prev != 0 and side != 0 and prev != side:
                self.total_count += 1
                self.count_per_class[track.class_name] += 1
                crossed.append(track)

            if side != 0:
                self._prev_sides[track.track_id] = side

        # Clean up lost tracks
        for tid in list(self._prev_sides):
            if tid not in current_ids:
                del self._prev_sides[tid]

        return crossed

    def _side(self, px: float, py: float) -> float:
        dx = self.p2[0] - self.p1[0]
        dy = self.p2[1] - self.p1[1]
        return float(np.sign(dx * (py - self.p1[1]) - dy * (px - self.p1[0])))


# ── Speed estimator ──────────────────────────────────────────────────────────

class SpeedEstimator:
    """Estimates speed from pixel displacement between frames."""

    def __init__(
        self,
        pixels_per_meter: float = 10.0,
        fps: float = 25.0,
        smoothing_window: int = 5,
    ) -> None:
        self.pixels_per_meter = pixels_per_meter
        self.fps = fps
        self.smoothing_window = smoothing_window
        self._history: dict[int, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=smoothing_window)
        )

    def update(self, tracks: list[Track]) -> list[SpeedSample]:
        samples = []
        current_ids = {t.track_id for t in tracks}

        for track in tracks:
            hist = self._history[track.track_id]
            hist.append(track.center)

            if len(hist) >= 2:
                positions = list(hist)
                total_px = sum(
                    np.linalg.norm(np.array(positions[i + 1]) - np.array(positions[i]))
                    for i in range(len(positions) - 1)
                )
                px_per_frame = total_px / (len(positions) - 1)
                speed_kmh = (px_per_frame / self.pixels_per_meter) * self.fps * 3.6
                samples.append(
                    SpeedSample(
                        track_id=track.track_id,
                        speed_kmh=round(speed_kmh, 1),
                        class_id=track.class_id,
                        timestamp=time.time(),
                    )
                )

        for tid in list(self._history):
            if tid not in current_ids:
                del self._history[tid]

        return samples


# ── Anomaly detector ──────────────────────────────────────────────────────────

class AnomalyDetector:
    """Threshold-based anomaly detection.

    - Count spike: current vehicle count > mean + 2σ of rolling window
    - Stopped vehicle: track barely moves for N consecutive frames
    """

    SPIKE_WINDOW      = 60
    SPIKE_Z_THRESHOLD = 2.0
    STOPPED_MIN_FRAMES    = 30
    STOPPED_PX_THRESHOLD  = 5.0

    def __init__(self) -> None:
        self._count_history: deque[int] = deque(maxlen=self.SPIKE_WINDOW)
        self._stationary_frames: dict[int, int] = defaultdict(int)
        self._last_centers: dict[int, tuple[float, float]] = {}

    def update(self, tracks: list[Track]) -> list[Alert]:
        alerts: list[Alert] = []
        current_count = len(tracks)
        current_ids = {t.track_id for t in tracks}

        # Count spike
        self._count_history.append(current_count)
        if len(self._count_history) == self.SPIKE_WINDOW:
            arr = np.array(self._count_history)
            mean, std = arr.mean(), arr.std()
            if std > 0 and current_count > mean + self.SPIKE_Z_THRESHOLD * std:
                alerts.append(Alert(
                    message=(
                        f"Traffic surge: {current_count} vehicles "
                        f"(mean={mean:.1f}, +{self.SPIKE_Z_THRESHOLD}σ)"
                    ),
                    severity="warning",
                ))

        # Stopped vehicles
        for track in tracks:
            cx, cy = track.center
            last = self._last_centers.get(track.track_id)
            if last is not None:
                dist = np.linalg.norm(np.array([cx, cy]) - np.array(last))
                if dist < self.STOPPED_PX_THRESHOLD:
                    self._stationary_frames[track.track_id] += 1
                else:
                    self._stationary_frames[track.track_id] = 0
            self._last_centers[track.track_id] = (cx, cy)

            if self._stationary_frames[track.track_id] == self.STOPPED_MIN_FRAMES:
                alerts.append(Alert(
                    message=f"Stopped vehicle: ID {track.track_id} ({track.class_name})",
                    severity="info",
                ))

        for tid in list(self._stationary_frames):
            if tid not in current_ids:
                del self._stationary_frames[tid]
                self._last_centers.pop(tid, None)

        return alerts


# ── Drift monitor ─────────────────────────────────────────────────────────────

class DriftMonitor:
    """Monitors detection confidence for model drift.

    Establishes a baseline mean confidence over the first BASELINE_FRAMES frames,
    then alerts when the rolling average drops more than THRESHOLD below baseline.
    Useful for catching model degradation in production.
    """

    BASELINE_FRAMES = 100    # frames to collect before locking in baseline
    WINDOW          = 30     # rolling window for current mean
    THRESHOLD       = 0.15   # relative drop fraction that triggers an alert
    ALERT_COOLDOWN  = 150    # minimum frames between repeated drift alerts

    def __init__(self) -> None:
        self._baseline_samples: list[float] = []
        self._baseline: float | None = None
        self._window: deque[float] = deque(maxlen=self.WINDOW)
        self._frames_since_alert: int = self.ALERT_COOLDOWN

    def update(self, tracks: list[Track]) -> list[Alert]:
        if not tracks:
            return []

        avg_conf = sum(t.confidence for t in tracks) / len(tracks)

        if self._baseline is None:
            self._baseline_samples.append(avg_conf)
            if len(self._baseline_samples) >= self.BASELINE_FRAMES:
                self._baseline = sum(self._baseline_samples) / len(self._baseline_samples)
                print(f"[DriftMonitor] Baseline confidence locked: {self._baseline:.3f}")
            return []

        self._window.append(avg_conf)
        self._frames_since_alert += 1

        if len(self._window) < self.WINDOW:
            return []

        rolling_mean = sum(self._window) / len(self._window)
        threshold    = self._baseline * (1.0 - self.THRESHOLD)

        if rolling_mean < threshold and self._frames_since_alert >= self.ALERT_COOLDOWN:
            drop_pct = (self._baseline - rolling_mean) / self._baseline * 100
            self._frames_since_alert = 0
            return [Alert(
                message=(
                    f"Confidence drift: rolling avg {rolling_mean:.2f} "
                    f"vs baseline {self._baseline:.2f} ({drop_pct:.0f}% drop)"
                ),
                severity="warning",
            )]

        return []


# ── Main analytics engine ────────────────────────────────────────────────────

class AnalyticsEngine:
    """Orchestrates counting, speed, anomaly detection, and drift monitoring.

    Usage::

        engine = AnalyticsEngine(
            counting_lines=[
                CountingLineSpec("north", (640, 0),   (640, 360)),
                CountingLineSpec("south", (640, 360), (640, 720)),
            ]
        )
        metrics = engine.update(tracks)
    """

    SPEED_HISTORY_LEN = 50

    def __init__(
        self,
        counting_lines: list[CountingLineSpec] | None = None,
        pixels_per_meter: float = 10.0,
        fps: float = 25.0,
    ) -> None:
        # Build named counting lines
        self._lines: list[tuple[str, CountingLine]] = []
        for spec in (counting_lines or []):
            self._lines.append((spec.name, CountingLine(spec.p1, spec.p2)))

        self.speed_estimator  = SpeedEstimator(pixels_per_meter=pixels_per_meter, fps=fps)
        self.anomaly_detector = AnomalyDetector()
        self.drift_monitor    = DriftMonitor()

        self._speed_history: deque[float] = deque(maxlen=self.SPEED_HISTORY_LEN)
        self._recent_alerts: deque[Alert] = deque(maxlen=20)
        self._frame_index: int = 0

    def update(self, tracks: list[Track]) -> FrameMetrics:
        """Process one frame's tracks and return an analytics snapshot."""

        # ── Multi-line counting ───────────────────────────────────────────────
        count_per_line: dict[str, int] = {}
        total_count  = 0
        count_per_class: dict[str, int] = {}

        for name, line in self._lines:
            line.update(tracks)
            count_per_line[name] = line.total_count
            total_count += line.total_count

        # Use the first line's per-class breakdown for backward compat
        if self._lines:
            count_per_class = dict(self._lines[0][1].count_per_class)

        # ── Speed ─────────────────────────────────────────────────────────────
        speed_samples = self.speed_estimator.update(tracks)
        for s in speed_samples:
            if s.speed_kmh < 200:
                self._speed_history.append(s.speed_kmh)

        avg_speed    = float(np.mean(list(self._speed_history))) if self._speed_history else 0.0
        recent_speeds = [s.speed_kmh for s in speed_samples[-10:]]

        # ── Anomaly + drift ───────────────────────────────────────────────────
        new_alerts: list[Alert] = []
        new_alerts.extend(self.anomaly_detector.update(tracks))
        new_alerts.extend(self.drift_monitor.update(tracks))
        self._recent_alerts.extend(new_alerts)

        alert_strings = [
            f"[{a.severity.upper()}] {a.message}"
            for a in list(self._recent_alerts)[-5:]
        ]

        # ── Serialise tracks ──────────────────────────────────────────────────
        speed_map = {s.track_id: s.speed_kmh for s in speed_samples}
        tracks_data = [
            {
                "id":         t.track_id,
                "bbox":       list(t.bbox),
                "class_id":   t.class_id,
                "class_name": t.class_name,
                "confidence": round(t.confidence, 3),
                "speed_kmh":  speed_map.get(t.track_id),
            }
            for t in tracks
        ]

        self._frame_index += 1

        return FrameMetrics(
            timestamp=time.time(),
            frame_index=self._frame_index,
            total_count=total_count,
            count_per_class=count_per_class,
            count_per_line=count_per_line,
            vehicles_in_frame=len(tracks),
            avg_speed_kmh=round(avg_speed, 1),
            speed_samples=recent_speeds,
            alerts=alert_strings,
            tracks=tracks_data,
        )

    @property
    def cumulative_counts(self) -> dict[str, int]:
        if self._lines:
            return dict(self._lines[0][1].count_per_class)
        return {}

    @property
    def line_specs(self) -> list[CountingLineSpec]:
        """Return the counting line specs for serialisation."""
        out = []
        for name, line in self._lines:
            out.append(CountingLineSpec(
                name=name,
                p1=(int(line.p1[0]), int(line.p1[1])),
                p2=(int(line.p2[0]), int(line.p2[1])),
            ))
        return out
