"""ByteTrack multi-object tracking via the supervision library.

Wraps supervision.ByteTrack to consume Detection objects and produce
Track objects with stable IDs across frames, plus ID Lock stitching
when a vehicle's bounding box expands after clearing an obstacle or
flickers briefly in heavy traffic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import supervision as sv

from core.detector import Detection, CLASS_NAMES


@dataclass
class Track:
    """A detected+tracked vehicle with a stable ID across frames."""
    track_id: int
    bbox: tuple[float, float, float, float]   # x1, y1, x2, y2
    confidence: float
    class_id: int
    class_name: str = field(init=False)

    def __post_init__(self) -> None:
        self.class_name = CLASS_NAMES[self.class_id] if 0 <= self.class_id < len(CLASS_NAMES) else "unknown"

    def set_class_id(self, class_id: int) -> None:
        self.class_id = class_id
        self.class_name = CLASS_NAMES[class_id] if 0 <= class_id < len(CLASS_NAMES) else "unknown"

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) / 2, (y1 + y2) / 2

    @property
    def xywh(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.bbox
        return x1, y1, x2 - x1, y2 - y1

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass
class _TrackHistoryState:
    canonical_id: int
    bbox: tuple[float, float, float, float]
    class_id: int
    confidence: float
    vx: float = 0.0
    vy: float = 0.0
    age: int = 1
    lost_frames: int = 0


def _box_metrics(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    """Return (iou, io_min, center_dist) between two xyxy boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    a_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    b_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    acx, acy = (ax1 + ax2) * 0.5, (ay1 + ay2) * 0.5
    bcx, bcy = (bx1 + bx2) * 0.5, (by1 + by2) * 0.5
    cdist = math.hypot(acx - bcx, acy - bcy)

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1 or a_area <= 0 or b_area <= 0:
        return 0.0, 0.0, cdist

    inter = (ix2 - ix1) * (iy2 - iy1)
    union = a_area + b_area - inter
    iou = inter / union if union > 0 else 0.0
    io_min = inter / min(a_area, b_area)
    return iou, io_min, cdist


def _shift_box(
    bbox: tuple[float, float, float, float],
    dx: float,
    dy: float,
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


def _match_score_to_state(
    bbox: tuple[float, float, float, float],
    class_id: int,
    st: _TrackHistoryState,
) -> float:
    """Return >0 match score if bbox/class_id is a valid continuation of st, else 0.0."""
    dt = max(1, st.lost_frames + 1)
    proj_box = _shift_box(st.bbox, st.vx * dt, st.vy * dt)
    iou, io_min, cdist = _box_metrics(proj_box, bbox)
    iou_raw, io_min_raw, cdist_raw = _box_metrics(st.bbox, bbox)
    iou = max(iou, iou_raw)
    io_min = max(io_min, io_min_raw)
    cdist = min(cdist, cdist_raw)

    tx1, ty1, tx2, ty2 = bbox
    sx1, sy1, sx2, sy2 = st.bbox
    t_diag = max(math.hypot(tx2 - tx1, ty2 - ty1), 30.0)
    s_diag = max(math.hypot(sx2 - sx1, sy2 - sy1), 30.0)
    ref_diag = max(t_diag, s_diag)

    # Case A: Four-wheeler emerging from obstacle (e.g. partial bus -> full truck)
    if class_id != 2 and st.class_id != 2 and st.lost_frames <= 15:
        if io_min >= 0.45 or (iou >= 0.20 and cdist <= 0.75 * ref_diag):
            return max(0.01, io_min + iou - (cdist / (ref_diag * 2.0)))

    # Case B: Same-class vehicle/motorcycle briefly occluded or reshaped in traffic
    if class_id == st.class_id and cdist <= 0.85 * ref_diag:
        tcx, tcy = (tx1 + tx2) * 0.5, (ty1 + ty2) * 0.5
        scx, scy = (sx1 + sx2) * 0.5, (sy1 + sy2) * 0.5
        disp_x, disp_y = tcx - scx, tcy - scy
        speed = math.hypot(st.vx, st.vy)
        if speed > 1.5:
            dot = (disp_x * st.vx + disp_y * st.vy) / (speed * max(math.hypot(disp_x, disp_y), 1.0))
            if dot < -0.25:
                return 0.0
        return max(0.01, 1.0 - (cdist / ref_diag) + 0.5 * iou)

    return 0.0


class Tracker:
    """ByteTrack wrapper with ID Lock across obstacle emergence and brief occlusions."""

    def __init__(
        self,
        *,
        track_activation_threshold: float = 0.20,
        lost_track_buffer: int = 45,
        minimum_matching_threshold: float = 0.8,
        frame_rate: int = 25,
    ) -> None:
        self._lost_track_buffer = lost_track_buffer
        self._byte_tracker = sv.ByteTrack(
            track_activation_threshold=track_activation_threshold,
            lost_track_buffer=lost_track_buffer,
            minimum_matching_threshold=minimum_matching_threshold,
            frame_rate=frame_rate,
        )
        self._history: dict[int, _TrackHistoryState] = {}
        self._id_aliases: dict[int, int] = {}

    def update(self, detections: list[Detection]) -> list[Track]:
        """Feed new detections, get back tracks with locked stable IDs."""
        if not detections:
            sv_dets = sv.Detections.empty()
        else:
            sv_dets = self._to_sv_detections(detections)

        tracked = self._byte_tracker.update_with_detections(sv_dets)
        raw_tracks = self._from_sv_detections(tracked)
        return self._lock_and_stitch_ids(raw_tracks, detections)

    def reset(self) -> None:
        """Reset tracker state (call when switching video source)."""
        self._byte_tracker.reset()
        self._history.clear()
        self._id_aliases.clear()

    def _lock_and_stitch_ids(
        self,
        raw_tracks: list[Track],
        detections: list[Detection],
    ) -> list[Track]:
        raw_ids: list[int] = []
        for t in raw_tracks:
            raw_id = t.track_id
            raw_ids.append(raw_id)
            if raw_id in self._id_aliases:
                t.track_id = self._id_aliases[raw_id]

        # 1. Merge stacked four-wheeler tracks in the same frame (e.g. old 'bus' ID on cab
        # and newly spawned 'truck' ID on full body) -> keep older canonical ID + fuller box.
        if len(raw_tracks) >= 2:
            drop_indices: set[int] = set()
            for i in range(len(raw_tracks)):
                if i in drop_indices or raw_tracks[i].class_id == 2:
                    continue
                for j in range(i + 1, len(raw_tracks)):
                    if j in drop_indices or raw_tracks[j].class_id == 2:
                        continue
                    ti, tj = raw_tracks[i], raw_tracks[j]
                    _, io_min, _ = _box_metrics(ti.bbox, tj.bbox)
                    if io_min < 0.60:
                        continue

                    si = self._history.get(ti.track_id)
                    sj = self._history.get(tj.track_id)
                    age_i = si.age if si is not None else 0
                    age_j = sj.age if sj is not None else 0

                    if (age_i, -ti.track_id) >= (age_j, -tj.track_id):
                        keep_idx, drop_idx = i, j
                    else:
                        keep_idx, drop_idx = j, i

                    keep_t, drop_t = raw_tracks[keep_idx], raw_tracks[drop_idx]
                    self._id_aliases[raw_ids[drop_idx]] = keep_t.track_id

                    if drop_t.area >= 1.20 * keep_t.area and drop_t.confidence >= keep_t.confidence - 0.18:
                        keep_t.bbox = drop_t.bbox
                        keep_t.set_class_id(drop_t.class_id)
                        keep_t.confidence = max(keep_t.confidence, drop_t.confidence)

                    drop_indices.add(drop_idx)
                    if i in drop_indices:
                        break

            if drop_indices:
                raw_tracks = [t for idx, t in enumerate(raw_tracks) if idx not in drop_indices]
                raw_ids = [rid for idx, rid in enumerate(raw_ids) if idx not in drop_indices]

        # 2. Stitch newly spawned IDs onto recently lost tracks (box expansion after obstacle
        # or brief occlusion in traffic).
        active_canonical = {t.track_id for t in raw_tracks if t.track_id in self._history}
        lost_candidates = [
            st for cid, st in self._history.items()
            if cid not in active_canonical and st.lost_frames <= 20
        ]
        used_lost_ids: set[int] = set()

        for idx, t in enumerate(raw_tracks):
            if t.track_id in self._history:
                continue

            best_cid: int | None = None
            best_score: float = 0.0
            for st in lost_candidates:
                if st.canonical_id in used_lost_ids:
                    continue
                score = _match_score_to_state(t.bbox, t.class_id, st)
                if score > best_score:
                    best_score = score
                    best_cid = st.canonical_id

            if best_cid is not None:
                used_lost_ids.add(best_cid)
                self._id_aliases[raw_ids[idx]] = best_cid
                t.track_id = best_cid
                active_canonical.add(best_cid)

        # 3. Rescue unmatched detections that ByteTrack held in 1-frame tentative state
        # when a tracked vehicle's box suddenly expanded (clearing an obstacle) or shifted.
        remaining_lost = [
            st for st in lost_candidates
            if st.canonical_id not in used_lost_ids and st.lost_frames <= 5
        ]
        if remaining_lost and detections:
            for det in detections:
                already_covered = False
                for rt in raw_tracks:
                    iou_rt, io_min_rt, _ = _box_metrics(det.bbox, rt.bbox)
                    if iou_rt >= 0.45 or (det.class_id != 2 and rt.class_id != 2 and io_min_rt >= 0.60):
                        already_covered = True
                        break
                if already_covered:
                    continue

                best_st: _TrackHistoryState | None = None
                best_score = 0.0
                for st in remaining_lost:
                    if st.canonical_id in used_lost_ids:
                        continue
                    score = _match_score_to_state(det.bbox, det.class_id, st)
                    if score > best_score:
                        best_score = score
                        best_st = st

                if best_st is not None:
                    used_lost_ids.add(best_st.canonical_id)
                    active_canonical.add(best_st.canonical_id)
                    raw_tracks.append(
                        Track(
                            track_id=best_st.canonical_id,
                            bbox=det.bbox,
                            confidence=det.confidence,
                            class_id=det.class_id,
                        )
                    )

        # 4. Update track history states & age out expired tracks
        seen_now: set[int] = set()
        for t in raw_tracks:
            cid = t.track_id
            seen_now.add(cid)
            prev = self._history.get(cid)
            if prev is None:
                self._history[cid] = _TrackHistoryState(
                    canonical_id=cid,
                    bbox=t.bbox,
                    class_id=t.class_id,
                    confidence=t.confidence,
                )
            else:
                dt = max(1, prev.lost_frames + 1)
                pcx = (prev.bbox[0] + prev.bbox[2]) * 0.5
                pcy = (prev.bbox[1] + prev.bbox[3]) * 0.5
                ncx, ncy = t.center
                inst_vx = (ncx - pcx) / dt
                inst_vy = (ncy - pcy) / dt
                prev.vx = 0.6 * prev.vx + 0.4 * inst_vx if prev.age > 1 else inst_vx
                prev.vy = 0.6 * prev.vy + 0.4 * inst_vy if prev.age > 1 else inst_vy
                prev.bbox = t.bbox
                prev.class_id = t.class_id
                prev.confidence = t.confidence
                prev.age += 1
                prev.lost_frames = 0

        for cid in list(self._history):
            if cid not in seen_now:
                self._history[cid].lost_frames += 1
                if self._history[cid].lost_frames > self._lost_track_buffer:
                    del self._history[cid]

        if len(self._id_aliases) > 2000:
            live_cids = set(self._history)
            self._id_aliases = {k: v for k, v in self._id_aliases.items() if v in live_cids}

        return raw_tracks

    # ── Conversion helpers ────────────────────────────────────────────────────

    @staticmethod
    def _to_sv_detections(detections: list[Detection]) -> sv.Detections:
        xyxy = np.array([d.bbox for d in detections], dtype=np.float32)
        confidence = np.array([d.confidence for d in detections], dtype=np.float32)
        class_id = np.array([d.class_id for d in detections], dtype=int)
        return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)

    @staticmethod
    def _from_sv_detections(sv_dets: sv.Detections) -> list[Track]:
        if sv_dets.tracker_id is None or len(sv_dets) == 0:
            return []
        tracks = []
        for i in range(len(sv_dets)):
            x1, y1, x2, y2 = sv_dets.xyxy[i].tolist()
            tracks.append(
                Track(
                    track_id=int(sv_dets.tracker_id[i]),
                    bbox=(x1, y1, x2, y2),
                    confidence=float(sv_dets.confidence[i]) if sv_dets.confidence is not None else 1.0,
                    class_id=int(sv_dets.class_id[i]) if sv_dets.class_id is not None else 0,
                )
            )
        return tracks
