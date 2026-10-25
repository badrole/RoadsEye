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

from core.detector import CLASS_NAMES
from core.tracker import Track, _box_metrics, _shift_box

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


def _bbox_fully_in_zone(
    bbox: tuple[float, float, float, float],
    zone: list[tuple[int, int]],
) -> bool:
    """Return True iff the entire bounding box (all 4 corners, edge midpoints, and center)
    lies inside the polygon `zone`."""
    if len(zone) < 3:
        return True
    x1, y1, x2, y2 = bbox
    mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    test_points = (
        (x1, y1),
        (x2, y1),
        (x2, y2),
        (x1, y2),
        (mx, my),
        (mx, y1),
        (mx, y2),
        (x1, my),
        (x2, my),
    )
    return all(_point_in_polygon(px, py, zone) for px, py in test_points)


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
    missing_frames: int = 0
    max_area: float = 0.0
    vote_ref_area: float = 0.0
    recent_areas: deque[float] = field(default_factory=lambda: deque(maxlen=6))
    votes: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    vx: float = 0.0
    vy: float = 0.0
    last_seen: float = 0.0


class IdCounter:
    """Counts every vehicle in frame once by tracker ID.

    When a counting zone (`zone`, >= 3 polygon vertices) is configured, a vehicle is
    counted ONLY after its entire bounding box is 100% inside the obstacle-free zone,
    and its vehicle type is determined from detections while fully inside the zone."""

    MIN_VEHICLE_HITS = 10
    MIN_MOTORCYCLE_HITS = 3
    MAX_HOLD_FRAMES = 30
    EXIT_FLUSH_MISSING_FRAMES = 3
    MIN_EXIT_HITS = 2
    FORGET_AFTER_S = 60.0

    def __init__(self, zone: list[tuple[int, int]] | None = None) -> None:
        self.zone: list[tuple[int, int]] = [
            (int(round(p[0])), int(round(p[1]))) for p in (zone or [])
        ]
        self._records: dict[int, _IdTrackRecord] = {}
        self._counted: set[int] = set()
        self._counted_class: dict[int, str] = {}
        self._last_seen: dict[int, float] = {}
        self._flush_immediate: bool = False
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
        if flush_uncounted:
            self._flush_immediate = True

    def update(self, tracks: list[Track], count: bool = True) -> None:
        now_ts = time.time()
        self.last_counted = []
        current_ids = {t.track_id for t in tracks}
        has_zone = len(self.zone) >= 3

        for track in tracks:
            tid = track.track_id
            area = max(track.area, 1.0)
            in_zone_full = _bbox_fully_in_zone(track.bbox, self.zone) if has_zone else True
            rec = self._records.get(tid)

            # Spatial ID lock fallback: if a new four-wheeler track appears overlapping
            # a recently seen four-wheeler track (e.g. emerging from behind an obstacle),
            # inherit the existing record so it cannot double-count.
            if rec is None and track.class_id != 2:
                merged_from = self._find_overlapping_four_wheeler(track, current_ids, now_ts)
                if merged_from is not None:
                    rec = self._records.pop(merged_from)
                    rec.tid = tid
                    self._records[tid] = rec
                    if merged_from in self._counted:
                        self._counted.discard(merged_from)
                        self._counted.add(tid)
                    if merged_from in self._counted_class:
                        self._counted_class[tid] = self._counted_class.pop(merged_from)
                    self._last_seen.pop(merged_from, None)

            if rec is None:
                rec = _IdTrackRecord(
                    tid=tid,
                    bbox=track.bbox,
                    last_track=track,
                    max_area=area,
                    vote_ref_area=area,
                    last_seen=now_ts,
                )
                self._records[tid] = rec
            else:
                dt = max(1, rec.missing_frames + 1)
                pcx = (rec.bbox[0] + rec.bbox[2]) * 0.5
                pcy = (rec.bbox[1] + rec.bbox[3]) * 0.5
                ncx, ncy = track.center
                ivx, ivy = (ncx - pcx) / dt, (ncy - pcy) / dt
                rec.vx = 0.6 * rec.vx + 0.4 * ivx if rec.hits > 1 else ivx
                rec.vy = 0.6 * rec.vy + 0.4 * ivy if rec.hits > 1 else ivy
                rec.bbox = track.bbox
                rec.last_track = track
                rec.missing_frames = 0
                rec.last_seen = now_ts

            rec.hits += 1
            rec.recent_areas.append(area)
            if area > rec.max_area:
                rec.max_area = area

            # When a counting zone is active and the vehicle enters it 100% for the first time,
            # clear out-of-zone partial votes so only obstacle-free detections decide the class.
            if has_zone and in_zone_full:
                if rec.zone_hits == 0:
                    for cls_key in list(rec.votes):
                        rec.votes[cls_key] *= 0.05
                    rec.vote_ref_area = area
                rec.zone_hits += 1

            # If bounding box grew significantly (vehicle just emerged from obstacle into clear area),
            # decay older small-box votes so the full-body classification dominates.
            if rec.vote_ref_area > 0 and area >= 1.25 * rec.vote_ref_area:
                for cls_key in list(rec.votes):
                    rec.votes[cls_key] *= 0.20
                rec.vote_ref_area = area

            # Lock class once an already-counted vehicle starts leaving the counting zone
            if has_zone and tid in self._counted and not in_zone_full:
                locked_cls = self._counted_class.get(tid)
                if locked_cls and locked_cls in CLASS_NAMES:
                    track.set_class_id(CLASS_NAMES.index(locked_cls))
                self._last_seen[tid] = now_ts
                continue

            vote_weight = (area / max(rec.max_area, 1.0)) * max(track.confidence, 0.1)
            if area >= 0.85 * rec.max_area:
                vote_weight *= 1.5
            if has_zone and not in_zone_full:
                vote_weight *= 0.05
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

            if count and self._is_ready_to_count(rec, track, has_zone, in_zone_full):
                self._commit_count(tid, track, best_cls)

        self._flush_immediate = False

        # Handle tracks that disappeared from current frame
        for tid, rec in list(self._records.items()):
            if tid not in current_ids:
                rec.missing_frames += 1
                if (
                    count
                    and not has_zone
                    and tid not in self._counted
                    and rec.missing_frames == self.EXIT_FLUSH_MISSING_FRAMES
                    and rec.hits >= self.MIN_EXIT_HITS
                ):
                    best_cls = max(rec.votes.items(), key=lambda kv: kv[1])[0]
                    self._commit_count(tid, rec.last_track, best_cls)

        for tid, ts in list(self._last_seen.items()):
            if now_ts - ts > self.FORGET_AFTER_S and tid not in current_ids:
                del self._last_seen[tid]
                self._counted.discard(tid)
                self._counted_class.pop(tid, None)
                self._records.pop(tid, None)

    def _is_ready_to_count(
        self,
        rec: _IdTrackRecord,
        track: Track,
        has_zone: bool,
        in_zone_full: bool,
    ) -> bool:
        if has_zone:
            return in_zone_full and rec.zone_hits >= 1
        if self._flush_immediate and not self._is_box_expanding(rec):
            return True
        if track.class_id == 2:
            return rec.hits >= self.MIN_MOTORCYCLE_HITS
        if rec.hits >= self.MAX_HOLD_FRAMES:
            return True
        if rec.hits < self.MIN_VEHICLE_HITS:
            return False
        return not self._is_box_expanding(rec)

    @staticmethod
    def _is_box_expanding(rec: _IdTrackRecord) -> bool:
        """True while the bounding box is still noticeably growing frame-over-frame
        (e.g. a truck emerging from behind an obstacle or frame edge)."""
        areas = list(rec.recent_areas)
        if len(areas) < 3:
            return False
        prev_mean = sum(areas[:-1]) / len(areas[:-1])
        return prev_mean > 0 and areas[-1] > 1.08 * prev_mean

    def _find_overlapping_four_wheeler(
        self,
        track: Track,
        current_ids: set[int],
        now_ts: float,
    ) -> int | None:
        tx1, ty1, tx2, ty2 = track.bbox
        t_diag = max(math.hypot(tx2 - tx1, ty2 - ty1), 30.0)
        best_tid: int | None = None
        best_score = 0.0

        for old_tid, rec in self._records.items():
            if old_tid in current_ids or rec.last_track.class_id == 2:
                continue
            if now_ts - rec.last_seen > 2.5 or rec.missing_frames > 25:
                continue
            dt = max(1, rec.missing_frames)
            proj_box = _shift_box(rec.bbox, rec.vx * dt, rec.vy * dt)
            iou, io_min, cdist = _box_metrics(proj_box, track.bbox)
            rx1, ry1, rx2, ry2 = rec.bbox
            r_diag = max(math.hypot(rx2 - rx1, ry2 - ry1), 30.0)
            ref_diag = max(t_diag, r_diag)

            if io_min >= 0.45 or (iou >= 0.20 and cdist <= 0.75 * ref_diag):
                score = io_min + iou - (cdist / (ref_diag * 2.0))
                if score > best_score:
                    best_score = score
                    best_tid = old_tid

        return best_tid

    def _commit_count(self, tid: int, track: Track, cls: str) -> None:
        self.total_count += 1
        self.count_per_class[cls] += 1
        self._counted.add(tid)
        self._counted_class[tid] = cls
        self.last_counted.append((track, cls))


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
    WARMUP_FRAMES = 0

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

        # ── Per-ID counting ───────────────────────────────────────────────────
        if id_mode:
            self._id_counter.update(tracks, count=do_count)
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
