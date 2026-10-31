"""Traffic analytics engine.

Responsibilities:
- Vehicle counting when tracks cross named counting lines
- Direct per-ID vehicle counting for all tracked vehicles in frame
- Speed estimation from pixel displacement × calibration factor
- Per-class breakdown
- Anomaly detection (count spikes, stopped vehicles)
- Confidence drift monitoring
"""
from __future__ import annotations

import datetime as _dt
import math
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from engine.yolo_infer import CLASS_NAMES
from engine.vehicle_tracker import Track

COUNT_MODES = ("line", "id")
ID_MODE_LINE_NAME = "ID"
DEFAULT_COUNTING_ZONE: list[tuple[int, int]] = [
    (160, 140),
    (1120, 140),
    (1120, 620),
    (160, 620),
]


def _point_in_polygon(px: float, py: float, poly: list[tuple[int, int]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def _bbox_in_zone(
    bbox: tuple[float, float, float, float],
    zone: list[tuple[int, int]],
) -> bool:
    """Return True if the vehicle is inside the counting zone.

    For large vehicles (trucks, buses, container trucks), requiring all 4 corners
    and midpoints to be 100% inside simultaneously fails because long vehicles span
    across boundaries as they travel. Checking centroid, ground-contact points,
    or significant corner presence guarantees large vehicles are accurately registered in Per-ID counting mode.
    """
    if len(zone) < 3:
        return True
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    # Vehicle centroid is inside zone
    if _point_in_polygon(cx, cy, zone):
        return True
    # Bottom contact (wheels on road) is inside zone
    if _point_in_polygon(cx, y2, zone):
        return True
    # At least two corners inside zone
    corners = ((x1, y1), (x2, y1), (x1, y2), (x2, y2))
    inside_corners = sum(1 for px, py in corners if _point_in_polygon(px, py, zone))
    return inside_corners >= 2


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
    total_count: int                        # vehicles counted (per line or per ID)
    count_per_class: dict[str, int]         # cumulative per class
    count_per_line: dict[str, int]          # cumulative count per named line
    vehicles_in_frame: int                  # currently visible tracks
    # Speed
    avg_speed_kmh: float                    # rolling average over last N samples
    speed_samples: list[float]              # recent speed readings (last 10)
    # Alerts
    alerts: list[str]
    # Raw tracks for frontend overlay
    tracks: list[dict[str, Any]]
    # Confirmed counting crossings this frame (persisted as vehicle_events)
    crossings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Alert:
    message: str
    severity: str   # "info" | "warning" | "critical"
    timestamp: float = field(default_factory=time.time)


# ── Counting line ─────────────────────────────────────────────────────────────

class CountingLine:
    """A line segment that counts vehicles crossing it.

    The line is defined by two points (x1,y1)→(x2,y2) in pixel coords.
    A crossing is detected when a track's centre moves from one side to the
    other.  After a crossing fires, the track enters a cooldown window
    (CROSSING_COOLDOWN_FRAMES frames) during which it cannot re-trigger on
    this line — this kills the double-count bug caused by tracker jitter or
    repeated inference on the same frame.
    """

    CROSSING_COOLDOWN_FRAMES = 30
    LOST_SIDE_TTL_FRAMES = 45
    RELINK_MAX_MISSING_FRAMES = 25
    RELINK_LINE_DIST_PX = 130.0
    RELINK_MAX_JUMP_PX = 140.0

    def __init__(self, p1: tuple[int, int], p2: tuple[int, int]) -> None:
        self.p1 = np.array(p1, dtype=float)
        self.p2 = np.array(p2, dtype=float)
        self._prev_sides: dict[int, float] = {}
        self._missing_frames: dict[int, int] = {}
        self._last_state: dict[int, tuple[float, float, float, float, float, int]] = {}
        self._cooldowns: dict[int, int] = {}    # track_id → remaining cooldown frames
        self._crossed_ids: dict[int, float] = {} # track_id → timestamp when crossed (anti-double-count)
        self.total_count: int = 0
        self.count_per_class: dict[str, int] = defaultdict(int)
        self.last_crossings: list[tuple[Track, float, float]] = []

    def set_baseline_counts(self, baseline: dict[str, int]) -> None:
        """Initialize line count from existing daily baseline."""
        self.count_per_class = defaultdict(int, baseline)
        self.total_count = sum(self.count_per_class.values())

    def update(self, tracks: list[Track], count: bool = True) -> list[Track]:
        """Check each track for a crossing. Returns newly-crossed tracks.

        Preserves side state across brief occlusions (up to LOST_SIDE_TTL_FRAMES)
        and re-links trajectory across the line if an occluded motorcycle/vehicle
        re-emerges on the opposite side with a new tracker ID."""
        crossed = []
        current_ids = {t.track_id for t in tracks}
        self.last_crossings = []
        now_ts = time.time()

        # Tick cooldowns down
        for tid in list(self._cooldowns):
            self._cooldowns[tid] -= 1
            if self._cooldowns[tid] <= 0:
                del self._cooldowns[tid]

        # Evict old crossed IDs after 60 seconds to avoid unbounded memory
        for tid in list(self._crossed_ids):
            if now_ts - self._crossed_ids[tid] > 60.0 and tid not in current_ids:
                del self._crossed_ids[tid]

        for track in tracks:
            tid = track.track_id
            cx, cy = track.center
            side = self._side(cx, cy)
            self._missing_frames[tid] = 0

            prev = self._prev_sides.get(tid)
            if prev is None and side != 0 and tid not in self._crossed_ids:
                inherited_side, old_tid = self._try_relink_across_line(track, cx, cy, side, current_ids)
                if inherited_side is not None and old_tid is not None:
                    prev = inherited_side
                    self._prev_sides.pop(old_tid, None)
                    self._missing_frames.pop(old_tid, None)
                    self._last_state.pop(old_tid, None)
                    self._crossed_ids[old_tid] = now_ts

            if prev is not None and prev != 0 and side != 0 and prev != side:
                if count and tid not in self._crossed_ids and tid not in self._cooldowns:
                    self.total_count += 1
                    self.count_per_class[track.class_name] += 1
                    crossed.append(track)
                    self.last_crossings.append((track, prev, side))
                    self._cooldowns[tid] = self.CROSSING_COOLDOWN_FRAMES
                    self._crossed_ids[tid] = now_ts
            if side != 0:
                self._prev_sides[tid] = side

            old_st = self._last_state.get(tid)
            if old_st is not None:
                ocx, ocy, ovx, ovy, _, _ = old_st
                vx = 0.5 * ovx + 0.5 * (cx - ocx)
                vy = 0.5 * ovy + 0.5 * (cy - ocy)
            else:
                vx, vy = 0.0, 0.0
            self._last_state[tid] = (cx, cy, vx, vy, side if side != 0 else (prev or 0.0), track.class_id)

        # Age out lost tracks only after LOST_SIDE_TTL_FRAMES so brief occlusion at the line never drops counts
        for tid in list(self._prev_sides):
            if tid not in current_ids:
                miss = self._missing_frames.get(tid, 0) + 1
                self._missing_frames[tid] = miss
                if miss > self.LOST_SIDE_TTL_FRAMES:
                    del self._prev_sides[tid]
                    self._missing_frames.pop(tid, None)
                    self._last_state.pop(tid, None)

        return crossed

    def _try_relink_across_line(
        self,
        track: Track,
        cx: float,
        cy: float,
        side: float,
        current_ids: set[int],
    ) -> tuple[float | None, int | None]:
        if self._dist_to_segment(cx, cy) > self.RELINK_LINE_DIST_PX:
            return None, None

        best_old_tid: int | None = None
        best_old_side: float | None = None
        best_dist = self.RELINK_MAX_JUMP_PX

        for old_tid, oside in self._prev_sides.items():
            if old_tid in current_ids or old_tid in self._crossed_ids or old_tid in self._cooldowns:
                continue
            if oside == 0 or oside == side:
                continue
            miss = self._missing_frames.get(old_tid, 0)
            if miss < 1 or miss > self.RELINK_MAX_MISSING_FRAMES:
                continue
            st = self._last_state.get(old_tid)
            if st is None:
                continue
            ocx, ocy, ovx, ovy, _, ocls = st
            if track.class_id == 2 or ocls == 2:
                if track.class_id != ocls:
                    continue
            if self._dist_to_segment(ocx, ocy) > self.RELINK_LINE_DIST_PX:
                continue

            pred_x = ocx + ovx * miss
            pred_y = ocy + ovy * miss
            dist = min(math.hypot(cx - pred_x, cy - pred_y), math.hypot(cx - ocx, cy - ocy))
            if dist < best_dist:
                best_dist = dist
                best_old_tid = old_tid
                best_old_side = oside

        return best_old_side, best_old_tid

    def _dist_to_segment(self, px: float, py: float) -> float:
        ab = self.p2 - self.p1
        ap = np.array([px, py], dtype=float) - self.p1
        ab_len_sq = float(np.dot(ab, ab))
        if ab_len_sq <= 1e-6:
            return float(np.linalg.norm(ap))
        t = float(np.clip(np.dot(ap, ab) / ab_len_sq, 0.0, 1.0))
        proj = self.p1 + t * ab
        return float(np.linalg.norm(np.array([px, py], dtype=float) - proj))

    def mark_counted(self, track_ids) -> None:
        """Treat these tracks as already counted (used on count-mode switch)."""
        now_ts = time.time()
        for tid in track_ids:
            self._crossed_ids[tid] = now_ts

    def _side(self, px: float, py: float) -> float:
        dx = self.p2[0] - self.p1[0]
        dy = self.p2[1] - self.p1[1]
        return float(np.sign(dx * (py - self.p1[1]) - dy * (px - self.p1[0])))


# ── ID counter ───────────────────────────────────────────────────────────────

@dataclass
class _IdTrackRecord:
    tid: int
    bbox: tuple[float, float, float, float]
    last_track: Track
    hits: int = 0
    zone_hits: int = 0
    max_conf: float = 0.0
    votes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    last_seen: float = 0.0


class IdCounter:
    """Counts every vehicle in frame once by tracker ID.

    When a counting zone (`zone`, >= 3 polygon vertices) is configured, a vehicle is
    counted ONLY after its entire bounding box is 100% inside the obstacle-free zone,
    and its vehicle type is determined from detections while fully inside the zone."""

    MIN_HITS = 3
    FORGET_AFTER_S = 60.0

    def __init__(self, zone: list[tuple[int, int]] | None = None) -> None:
        self.zone: list[tuple[int, int]] = [
            (int(round(p[0])), int(round(p[1]))) for p in (zone or [])
        ]
        self._records: dict[int, _IdTrackRecord] = {}
        self._counted: set[int] = set()
        self._counted_class: dict[int, str] = {}
        self._last_seen: dict[int, float] = {}
        self.total_count: int = 0
        self.count_per_class: dict[str, int] = defaultdict(int)
        self.last_counted: list[tuple[Track, str]] = []

    def set_zone(self, zone: list[tuple[int, int]] | None) -> None:
        self.zone = [(int(round(p[0])), int(round(p[1]))) for p in (zone or [])]

    def set_baseline_counts(self, baseline: dict[str, int]) -> None:
        self.count_per_class = defaultdict(int, baseline)
        self.total_count = sum(self.count_per_class.values())

    def mark_counted(self, track_ids, flush_uncounted: bool = False) -> None:
        now_ts = time.time()
        for tid in track_ids:
            self._counted.add(tid)
            self._last_seen[tid] = now_ts

    def update(self, tracks: list[Track], count: bool = True) -> None:
        now_ts = time.time()
        self.last_counted = []
        current_ids = {t.track_id for t in tracks}
        has_zone = len(self.zone) >= 3

        for track in tracks:
            tid = track.track_id
            in_zone_full = _bbox_in_zone(track.bbox, self.zone) if has_zone else True
            rec = self._records.get(tid)

            if rec is None:
                rec = _IdTrackRecord(
                    tid=tid,
                    bbox=track.bbox,
                    last_track=track,
                    last_seen=now_ts,
                )
                self._records[tid] = rec
            else:
                rec.bbox = track.bbox
                rec.last_track = track
                rec.last_seen = now_ts

            rec.hits += 1

            # When a counting zone is active and the vehicle enters it 100% for the first time,
            # clear out-of-zone partial votes so only obstacle-free detections decide the class.
            if has_zone and in_zone_full:
                if rec.zone_hits == 0:
                    rec.votes.clear()
                    rec.max_conf = 0.0
                rec.zone_hits += 1

            # Lock class once an already-counted vehicle starts leaving the counting zone
            if has_zone and tid in self._counted and not in_zone_full:
                locked_cls = self._counted_class.get(tid)
                if locked_cls and locked_cls in CLASS_NAMES:
                    track.set_class_id(CLASS_NAMES.index(locked_cls))
                self._last_seen[tid] = now_ts
                continue

            conf = max(track.confidence, 0.1)
            if conf >= 0.78 and 0.0 < rec.max_conf < 0.55:
                rec.votes.clear()

            if rec.max_conf >= 0.75 and conf < 0.50:
                vote_weight = conf * 0.1
            else:
                vote_weight = conf
                if conf > rec.max_conf:
                    rec.max_conf = conf

            rec.votes[track.class_name] += vote_weight
            best_cls = max(rec.votes.items(), key=lambda kv: kv[1])[0]

            if best_cls in CLASS_NAMES:
                track.set_class_id(CLASS_NAMES.index(best_cls))

            self._last_seen[tid] = now_ts

            if tid in self._counted:
                prev_cls = self._counted_class.get(tid)
                if prev_cls and best_cls != prev_cls:
                    if self.count_per_class[prev_cls] > 0:
                        self.count_per_class[prev_cls] -= 1
                        if self.count_per_class[prev_cls] == 0:
                            del self.count_per_class[prev_cls]
                    self.count_per_class[best_cls] += 1
                    self._counted_class[tid] = best_cls
                continue

            ready = (in_zone_full and rec.zone_hits >= 2) if has_zone else (rec.hits >= self.MIN_HITS)
            if count and ready:
                self._commit_count(tid, track, best_cls)

        for tid, ts in list(self._last_seen.items()):
            if now_ts - ts > self.FORGET_AFTER_S and tid not in current_ids:
                del self._last_seen[tid]
                self._counted.discard(tid)
                self._counted_class.pop(tid, None)
                self._records.pop(tid, None)

    def _commit_count(self, tid: int, track: Track, cls: str) -> None:
        self.total_count += 1
        self.count_per_class[cls] += 1
        self._counted.add(tid)
        self._counted_class[tid] = cls
        self.last_counted.append((track, cls))


# ── Speed estimator ──────────────────────────────────────────────────────────

class SpeedEstimator:
    """Estimates speed from net pixel displacement across a smoothing window."""

    JITTER_NET_PX_DEADBAND = 3.5
    JITTER_FRAME_PX_DEADBAND = 0.55

    def __init__(
        self,
        pixels_per_meter: float = 10.0,
        fps: float = 25.0,
        smoothing_window: int = 8,
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
                net_px = float(np.linalg.norm(np.array(positions[-1]) - np.array(positions[0])))
                px_per_frame = net_px / (len(positions) - 1)
                if net_px < self.JITTER_NET_PX_DEADBAND or px_per_frame < self.JITTER_FRAME_PX_DEADBAND:
                    speed_kmh = 0.0
                else:
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
    WARMUP_FRAMES = 0
    STOPPED_SPEED_KMH = 5.0
    STOPPED_ANCHOR_RADIUS_PX = 25.0

    def __init__(
        self,
        counting_lines: list[CountingLineSpec] | None = None,
        counting_zone: list[tuple[int, int]] | None = None,
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

        # Asia/Jakarta calendar day tracking (WIB = UTC+7)
        self.counting_enabled: bool = True   # gated off while the timeshift buffer loads
        wib_now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=7)))
        self._current_date_wib: str = wib_now.strftime("%Y-%m-%d")
        self._today_baseline: dict[str, int] = {}

        self.count_mode: str = "line"
        self._id_counter = IdCounter(zone=counting_zone)
        self._visible_ids: set[int] = set()
        self._conf_votes: dict[int, dict[int, float]] = {}  # tid -> {class_id: weighted_conf}
        self._max_conf: dict[int, float] = {}               # tid -> highest confidence seen
        self._stopped_since: dict[int, float] = {}          # tid -> epoch when stopped started
        self._stopped_anchor: dict[int, tuple[float, float]] = {}
        self._stopped_moving_frames: dict[int, int] = defaultdict(int)
        self._track_last_seen: dict[int, float] = {}

    @property
    def zone_points(self) -> list[tuple[int, int]]:
        return list(self._id_counter.zone)

    def set_zone(self, zone: list[tuple[int, int]] | None) -> None:
        self._id_counter.set_zone(zone)

    def set_count_mode(self, mode: str) -> None:
        """Switch between line-crossing and per-ID counting.

        The daily tally carries over. Only vehicles already counted by the previous
        mode are marked as counted in the new mode, so uncounted vehicles currently
        in the frame are immediately counted when switching to 'id' mode.
        """
        if mode not in COUNT_MODES:
            raise ValueError(f"count_mode must be one of {COUNT_MODES}, got {mode!r}")
        if mode == self.count_mode:
            return
        carried = self.cumulative_counts
        if mode == "id":
            already_crossed = {tid for _, line in self._lines for tid in line._crossed_ids}
            self._id_counter.set_baseline_counts(carried)
            self._id_counter.mark_counted(already_crossed, flush_uncounted=True)
        else:
            if self._lines:
                self._lines[0][1].set_baseline_counts(carried)
                for _, line in self._lines[1:]:
                    line.set_baseline_counts({})
            for _, line in self._lines:
                line.mark_counted(self._id_counter._counted)
        self.count_mode = mode

    def set_today_baseline(self, baseline_counts: dict[str, int]) -> None:
        """Initialize or resume counts from today's database records.

        Allows counting to continue seamlessly across reconnects on the same day.
        Applies to the primary counting line so total_count reflects the true daily total
        without multiplying across multiple lines.
        """
        self._today_baseline = dict(baseline_counts or {})
        self._id_counter.set_baseline_counts(self._today_baseline)
        if self._lines:
            self._lines[0][1].set_baseline_counts(self._today_baseline)
            for _, line in self._lines[1:]:
                line.set_baseline_counts({})

    @property
    def line_specs(self) -> list[CountingLineSpec]:
        return [
            CountingLineSpec(
                name=name,
                p1=(int(round(l.p1[0])), int(round(l.p1[1]))),
                p2=(int(round(l.p2[0])), int(round(l.p2[1]))),
            )
            for name, l in self._lines
        ]

    def set_lines(self, counting_lines: list[CountingLineSpec]) -> None:
        """Update line geometries while strictly preserving existing counts.

        Clears _prev_sides when endpoints change to prevent false crossings
        when lines are moved across stationary or moving tracks.
        """
        new_lines: list[tuple[str, CountingLine]] = []
        old_map = {name: line for name, line in self._lines}
        for spec in (counting_lines or []):
            if spec.name in old_map:
                line = old_map[spec.name]
                new_p1 = np.array(spec.p1, dtype=float)
                new_p2 = np.array(spec.p2, dtype=float)
                if not np.array_equal(line.p1, new_p1) or not np.array_equal(line.p2, new_p2):
                    line.p1 = new_p1
                    line.p2 = new_p2
                    line._prev_sides.clear()  # reset side state to prevent ghost crossing
                    line._missing_frames.clear()
                    line._last_state.clear()
                new_lines.append((spec.name, line))
            else:
                line = CountingLine(spec.p1, spec.p2)
                new_lines.append((spec.name, line))
        self._lines = new_lines

    set_line_specs = set_lines

    def update(self, tracks: list[Track]) -> FrameMetrics:
        """Process one frame's tracks and return an analytics snapshot."""

        # ── Check midnight rollover (Asia/Jakarta = UTC+7) ────────────────────
        wib_now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=7)))
        today_str = wib_now.strftime("%Y-%m-%d")
        if today_str != self._current_date_wib:
            # Pindah hari: reset counter harian ke 0
            self._current_date_wib = today_str
            self._today_baseline = {}
            for _, line in self._lines:
                line.total_count = 0
                line.count_per_class.clear()
            self._id_counter.set_baseline_counts({})

        # ── Multi-line counting (side state stays warm in ID mode) ─────────────
        count_per_line: dict[str, int] = {}
        total_count  = 0
        count_per_class: dict[str, int] = defaultdict(int)
        crossing_events: list[dict[str, Any]] = []
        frame_ts = time.time()

        warmup_done = self._frame_index >= self.WARMUP_FRAMES
        do_count = self.counting_enabled and warmup_done
        id_mode = self.count_mode == "id"
        self._visible_ids = {t.track_id for t in tracks}

        # ── Per-ID counting & zone-aware class stabilization ──────────────────
        if id_mode:
            self._id_counter.update(tracks, count=do_count)
            for t in tracks:
                self._track_last_seen[t.track_id] = frame_ts
        else:
            for t in tracks:
                tid = t.track_id
                conf = max(t.confidence, 0.1)
                prev_max = self._max_conf.get(tid, 0.0)
                cvotes = self._conf_votes.get(tid)
                if cvotes is None:
                    cvotes = defaultdict(float)
                    self._conf_votes[tid] = cvotes

                if conf >= 0.78 and 0.0 < prev_max < 0.55:
                    cvotes.clear()

                if prev_max >= 0.75 and conf < 0.50:
                    w = conf * 0.1
                else:
                    w = conf
                    if conf > prev_max:
                        self._max_conf[tid] = conf

                cvotes[t.class_id] += w
                best_cls_id = max(cvotes.items(), key=lambda kv: kv[1])[0]
                if best_cls_id != t.class_id:
                    t.set_class_id(best_cls_id)
                self._track_last_seen[tid] = frame_ts

        for name, line in self._lines:
            line.update(tracks, count=do_count and not id_mode)
            count_per_line[name] = line.total_count
            total_count += line.total_count
            for cls_name, cnt in line.count_per_class.items():
                count_per_class[cls_name] += cnt

            # Confirmed crossings (prev_side → side) — persisted per event
            for track, prev, side in line.last_crossings:
                crossing_events.append({
                    "line_name":   name,
                    "track_id":    track.track_id,
                    "class_name":  track.class_name,
                    "class_id":    track.class_id,
                    "confidence":  round(track.confidence, 3),
                    "direction":   "positive" if side > prev else "negative",
                    "timestamp":   frame_ts,
                })

        count_per_class = dict(count_per_class)

        if id_mode:
            total_count = self._id_counter.total_count
            count_per_class = dict(self._id_counter.count_per_class)
            count_per_line = {ID_MODE_LINE_NAME: total_count}
            for track, cls in self._id_counter.last_counted:
                crossing_events.append({
                    "line_name":   ID_MODE_LINE_NAME,
                    "track_id":    track.track_id,
                    "class_name":  cls,
                    "class_id":    CLASS_NAMES.index(cls) if cls in CLASS_NAMES else track.class_id,
                    "confidence":  round(track.confidence, 3),
                    "direction":   "id",
                    "timestamp":   frame_ts,
                })

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

        # ── Serialise tracks & compute stopped duration (< 5 km/h or anchored) ─
        speed_map = {s.track_id: s.speed_kmh for s in speed_samples}
        tracks_data = []
        for t in tracks:
            tid = t.track_id
            cx, cy = t.center
            spd = speed_map.get(tid)
            anchor = self._stopped_anchor.get(tid)
            dist_from_anchor = (
                math.hypot(cx - anchor[0], cy - anchor[1]) if anchor is not None else 0.0
            )

            is_stationary = (
                (spd is not None and spd < self.STOPPED_SPEED_KMH)
                or (anchor is not None and dist_from_anchor <= self.STOPPED_ANCHOR_RADIUS_PX)
            )

            if is_stationary:
                self._stopped_moving_frames[tid] = 0
                start_ts = self._stopped_since.setdefault(tid, frame_ts)
                self._stopped_anchor.setdefault(tid, (cx, cy))
                stopped_sec = round(max(0.1, frame_ts - start_ts), 1)
                if spd is not None and spd >= self.STOPPED_SPEED_KMH:
                    spd = 0.0
            else:
                if tid in self._stopped_since:
                    # Debounce: require at least 8 consecutive moving frames before clearing stopped timer
                    moving_count = self._stopped_moving_frames.get(tid, 0) + 1
                    self._stopped_moving_frames[tid] = moving_count
                    if moving_count >= 8:
                        self._stopped_since.pop(tid, None)
                        self._stopped_anchor.pop(tid, None)
                        self._stopped_moving_frames.pop(tid, None)
                        stopped_sec = 0.0
                    else:
                        start_ts = self._stopped_since[tid]
                        stopped_sec = round(max(0.1, frame_ts - start_ts), 1)
                else:
                    stopped_sec = 0.0

            tracks_data.append({
                "id":          tid,
                "bbox":        list(t.bbox),
                "class_id":    t.class_id,
                "class_name":  t.class_name,
                "confidence":  round(t.confidence, 3),
                "speed_kmh":   spd,
                "stopped_sec": stopped_sec,
            })

        for tid, last_ts in list(self._track_last_seen.items()):
            if tid not in self._visible_ids and frame_ts - last_ts > 3.0:
                self._track_last_seen.pop(tid, None)
                self._stopped_since.pop(tid, None)
                self._stopped_anchor.pop(tid, None)
                if frame_ts - last_ts > 60.0:
                    self._conf_votes.pop(tid, None)
                    self._max_conf.pop(tid, None)

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
            crossings=crossing_events,
        )

    @property
    def cumulative_counts(self) -> dict[str, int]:
        if self.count_mode == "id":
            return dict(self._id_counter.count_per_class)
        merged: dict[str, int] = defaultdict(int)
        for _, line in self._lines:
            for cls_name, cnt in line.count_per_class.items():
                merged[cls_name] += cnt
        return dict(merged)

    def set_line_specs(self, specs: list[CountingLineSpec]) -> None:
        """Replace counting lines live without resetting vehicle counters.

        Preserves total_count, count_per_class, and track states from matching
        prior lines (matched by name or by index) so user line edits never reset counts.
        """
        old_lines_map = {name: line for name, line in self._lines}
        old_lines_list = [line for _, line in self._lines]

        new_lines: list[tuple[str, CountingLine]] = []
        for idx, spec in enumerate(specs or []):
            new_line = CountingLine(spec.p1, spec.p2)
            # Find matching prior line by name, or fallback by index
            matched = old_lines_map.get(spec.name)
            if matched is None and idx < len(old_lines_list):
                matched = old_lines_list[idx]

            if matched is not None:
                new_line.total_count = matched.total_count
                new_line.count_per_class = defaultdict(int, matched.count_per_class)
                new_line._prev_sides = dict(matched._prev_sides)
                new_line._missing_frames = dict(matched._missing_frames)
                new_line._last_state = dict(matched._last_state)
                new_line._crossed_ids = dict(matched._crossed_ids)
                new_line._cooldowns = dict(matched._cooldowns)

            new_lines.append((spec.name, new_line))

        self._lines = new_lines
