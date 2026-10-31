"""ByteTrack multi-object tracking via the supervision library.

Wraps supervision.ByteTrack to consume Detection objects and produce
Track objects with stable IDs across frames.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import supervision as sv

from engine.yolo_infer import Detection, CLASS_NAMES


@dataclass
class Track:
    """A detected+tracked vehicle with a stable ID across frames."""
    track_id: int
    bbox: tuple[float, float, float, float]   # x1, y1, x2, y2
    confidence: float
    class_id: int
    class_name: str = field(init=False)
    is_coasted: bool = False

    def __post_init__(self) -> None:
        self.class_name = CLASS_NAMES[self.class_id] if 0 <= self.class_id < len(CLASS_NAMES) else "unknown"

    def set_class_id(self, class_id: int) -> None:
        self.class_id = class_id
        self.class_name = CLASS_NAMES[class_id] if 0 <= self.class_id < len(CLASS_NAMES) else "unknown"

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


class Tracker:
    """ByteTrack wrapper with temporal class smoothing and track coasting.

    Key improvements:
    1. Temporal Class Smoothing: accumulates confidence per class across frames
       with confidence-weighted voting. Eliminates single-frame class flickering.
    2. Track Coasting: retains active tracks from ByteTrack's lost buffer for up
       to `max_coast_frames` using Kalman filter predictions. Eliminates ID blinking
       and track loss during momentary occlusion or detection dips.
    """

    def __init__(
        self,
        *,
        track_activation_threshold: float = 0.20,
        lost_track_buffer: int = 45,
        minimum_matching_threshold: float = 0.8,
        frame_rate: int = 25,
        max_coast_frames: int = 6,
    ) -> None:
        self.max_coast_frames = max_coast_frames
        self._byte_tracker = sv.ByteTrack(
            track_activation_threshold=track_activation_threshold,
            lost_track_buffer=lost_track_buffer,
            minimum_matching_threshold=minimum_matching_threshold,
            frame_rate=frame_rate,
        )
        self._class_votes: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self._max_conf: dict[int, float] = defaultdict(float)
        self._hits: dict[int, int] = defaultdict(int)
        self._last_conf: dict[int, float] = {}
        self._last_seen_frame: dict[int, int] = {}

    def update(self, detections: list[Detection]) -> list[Track]:
        """Feed new detections, get back tracks with stable IDs and smoothed classes."""
        if not detections:
            sv_dets = sv.Detections.empty()
        else:
            sv_dets = self._to_sv_detections(detections)

        tracked = self._byte_tracker.update_with_detections(sv_dets)
        current_frame = self._byte_tracker.frame_id
        
        active_tracks: list[Track] = []
        active_ids: set[int] = set()

        if tracked.tracker_id is not None and len(tracked) > 0:
            for i in range(len(tracked)):
                tid = int(tracked.tracker_id[i])
                active_ids.add(tid)
                x1, y1, x2, y2 = tracked.xyxy[i].tolist()
                conf = float(tracked.confidence[i]) if tracked.confidence is not None else 1.0
                raw_cls = int(tracked.class_id[i]) if tracked.class_id is not None else 0
                
                self._hits[tid] += 1
                self._last_conf[tid] = conf
                self._last_seen_frame[tid] = current_frame

                # High-confidence detection clears earlier distant/weak votes (<0.55)
                if conf >= 0.75 and 0.0 < self._max_conf[tid] < 0.55:
                    self._class_votes[tid].clear()
                
                if conf > self._max_conf[tid]:
                    self._max_conf[tid] = conf

                # Decay previous votes slightly to maintain responsiveness
                for c in list(self._class_votes[tid].keys()):
                    self._class_votes[tid][c] *= 0.98

                # If an established class exists with high confidence, penalize weak detections
                if self._max_conf[tid] >= 0.75 and conf < 0.45:
                    vote_weight = conf * 0.15
                else:
                    vote_weight = conf

                self._class_votes[tid][raw_cls] += vote_weight
                best_cls = max(self._class_votes[tid].items(), key=lambda kv: kv[1])[0]

                active_tracks.append(
                    Track(
                        track_id=tid,
                        bbox=(x1, y1, x2, y2),
                        confidence=conf,
                        class_id=best_cls,
                        is_coasted=False,
                    )
                )

        # Coast active confirmed tracks that momentarily missed detection
        if self.max_coast_frames > 0:
            for lt in self._byte_tracker.lost_tracks:
                if not lt.is_activated or lt.external_track_id in active_ids:
                    continue
                lost_duration = current_frame - lt.end_frame
                if 1 <= lost_duration <= self.max_coast_frames:
                    tid = int(lt.external_track_id)
                    x1, y1, x2, y2 = lt.tlbr.tolist()
                    
                    # Sanity check: ignore collapsed or inverted boxes
                    if x2 <= x1 or y2 <= y1:
                        continue

                    best_cls = max(self._class_votes[tid].items(), key=lambda kv: kv[1])[0] if self._class_votes[tid] else 0
                    decayed_conf = max(0.2, self._last_conf.get(tid, 0.5) * (0.85 ** lost_duration))
                    
                    active_tracks.append(
                        Track(
                            track_id=tid,
                            bbox=(x1, y1, x2, y2),
                            confidence=decayed_conf,
                            class_id=best_cls,
                            is_coasted=True,
                        )
                    )

        # Evict old tracking history to bound memory
        if current_frame % 100 == 0:
            stale_cutoff = current_frame - 150
            for tid in list(self._last_seen_frame.keys()):
                if self._last_seen_frame[tid] < stale_cutoff and tid not in active_ids:
                    self._class_votes.pop(tid, None)
                    self._max_conf.pop(tid, None)
                    self._hits.pop(tid, None)
                    self._last_conf.pop(tid, None)
                    self._last_seen_frame.pop(tid, None)

        return active_tracks

    def reset(self) -> None:
        """Reset tracker state (call when switching video source)."""
        self._byte_tracker.reset()
        self._class_votes.clear()
        self._max_conf.clear()
        self._hits.clear()
        self._last_conf.clear()
        self._last_seen_frame.clear()

    # ── Conversion helpers ────────────────────────────────────────────────────

    @staticmethod
    def _to_sv_detections(detections: list[Detection]) -> sv.Detections:
        xyxy = np.array([d.bbox for d in detections], dtype=np.float32)
        confidence = np.array([d.confidence for d in detections], dtype=np.float32)
        class_id = np.array([d.class_id for d in detections], dtype=int)
        return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)
