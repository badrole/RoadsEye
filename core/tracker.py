"""ByteTrack multi-object tracking via the supervision library.

Wraps supervision.ByteTrack to consume Detection objects and produce
Track objects with stable IDs across frames.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
        self.class_name = CLASS_NAMES[self.class_id] if self.class_id < len(CLASS_NAMES) else "unknown"

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) / 2, (y1 + y2) / 2

    @property
    def xywh(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.bbox
        return x1, y1, x2 - x1, y2 - y1


class Tracker:
    """ByteTrack wrapper.

    Usage:
        tracker = Tracker()
        for frame, meta in source.stream():
            detections = detector.detect(frame)
            tracks = tracker.update(detections)
    """

    def __init__(
        self,
        *,
        track_activation_threshold: float = 0.25,
        lost_track_buffer: int = 30,
        minimum_matching_threshold: float = 0.8,
        frame_rate: int = 25,
    ) -> None:
        self._byte_tracker = sv.ByteTrack(
            track_activation_threshold=track_activation_threshold,
            lost_track_buffer=lost_track_buffer,
            minimum_matching_threshold=minimum_matching_threshold,
            frame_rate=frame_rate,
        )

    def update(self, detections: list[Detection]) -> list[Track]:
        """Feed new detections, get back tracks with stable IDs."""
        if not detections:
            sv_dets = sv.Detections.empty()
        else:
            sv_dets = self._to_sv_detections(detections)

        tracked = self._byte_tracker.update_with_detections(sv_dets)
        return self._from_sv_detections(tracked)

    def reset(self) -> None:
        """Reset tracker state (call when switching video source)."""
        self._byte_tracker.reset()

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
