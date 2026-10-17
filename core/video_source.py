"""Video input abstraction — file, webcam, or RTSP stream."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

import cv2
import numpy as np


@dataclass
class FrameMeta:
    index: int          # absolute frame number since source opened
    timestamp: float    # wall-clock seconds since source opened
    source: str         # path or stream URL


class VideoSource:
    """Unified interface for reading frames from file, webcam, or RTSP.

    Usage:
        with VideoSource("traffic.mp4") as src:
            for frame, meta in src.stream(skip=2):
                process(frame)
    """

    def __init__(
        self,
        source: str | int,
        *,
        width: int | None = None,
        height: int | None = None,
        loop: bool = False,
    ) -> None:
        self.source = source
        self._target_w = width
        self._target_h = height
        self.loop = loop
        self._cap: cv2.VideoCapture | None = None
        self._frame_idx: int = 0
        self._t0: float = 0.0

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "VideoSource":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self.source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video source: {self.source!r}")
        self._frame_idx = 0
        self._t0 = time.monotonic()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def fps(self) -> float:
        if self._cap is None:
            return 0.0
        return self._cap.get(cv2.CAP_PROP_FPS) or 25.0

    @property
    def width(self) -> int:
        if self._cap is None:
            return 0
        return int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    @property
    def height(self) -> int:
        if self._cap is None:
            return 0
        return int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    @property
    def frame_count(self) -> int:
        """Total frames in source (-1 for live streams)."""
        if self._cap is None:
            return -1
        n = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return n if n > 0 else -1

    @property
    def is_live(self) -> bool:
        """True for webcam or RTSP streams (no fixed frame count)."""
        return isinstance(self.source, int) or str(self.source).startswith("rtsp")

    # ── Reading ───────────────────────────────────────────────────────────────

    def read(self) -> tuple[np.ndarray | None, FrameMeta]:
        """Read a single frame. Returns (None, meta) on end-of-stream."""
        if self._cap is None:
            raise RuntimeError("VideoSource not opened. Call open() first.")

        ret, frame = self._cap.read()

        if not ret:
            if self.loop and not self.is_live:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
                if not ret:
                    return None, self._make_meta()

        if frame is not None and (self._target_w or self._target_h):
            frame = self._resize(frame)

        meta = self._make_meta()
        self._frame_idx += 1
        return frame, meta

    def stream(
        self, skip: int = 0
    ) -> Generator[tuple[np.ndarray, FrameMeta], None, None]:
        """Yield (frame, meta) continuously. skip=N processes every (N+1)th frame."""
        if self._cap is None:
            raise RuntimeError("VideoSource not opened. Call open() first.")

        while True:
            frame, meta = self.read()
            if frame is None:
                break

            if skip > 0 and (meta.index % (skip + 1)) != 0:
                continue

            yield frame, meta

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_meta(self) -> FrameMeta:
        return FrameMeta(
            index=self._frame_idx,
            timestamp=time.monotonic() - self._t0,
            source=str(self.source),
        )

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        target_w = self._target_w or w
        target_h = self._target_h or h
        if (w, h) == (target_w, target_h):
            return frame
        return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

    def __repr__(self) -> str:
        return (
            f"VideoSource(source={self.source!r}, "
            f"{self.width}x{self.height} @ {self.fps:.1f}fps)"
        )
