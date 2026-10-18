"""Video input abstraction — file, webcam, or RTSP stream."""
from __future__ import annotations

import os
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
        self._backlog_dropped: int = 0   # live-buffered frames skipped to stay on the live edge

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "VideoSource":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    # Low-latency FFMPEG flags for live/HLS sources. Without these the demuxer
    # waits for full probe/analyze and buffers whole segments → read rate ~15fps
    # instead of the source's 20+fps, and the feed lags behind live.
    _LIVE_FFMPEG_OPTIONS = (
        "fflags;nobuffer|flags;low_delay|analyzeduration;1000000|probesize;500000|"
        "timeout;5000000|reconnect;1|reconnect_streamed;1|reconnect_delay_max;5"
    )

    def open(self) -> None:
        prev_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        if self._is_live_source(self.source):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._LIVE_FFMPEG_OPTIONS
        try:
            self._cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        finally:
            if prev_opts is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = prev_opts
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video source: {self.source!r}")
        if self._is_live_source(self.source):
            # Cap the ship-in buffer where the backend honours it (webcam-like
            # sources); FFmpeg/HLS may ignore it, the backlog drain in read()
            # then covers it.
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            except Exception:
                pass
        self._frame_idx = 0
        self._t0 = time.monotonic()

    @staticmethod
    def _is_live_source(source: str | int) -> bool:
        if isinstance(source, int):
            return True
        s = str(source).lower()
        return s.startswith("rtsp") or s.startswith(("http://", "https://")) or ".m3u8" in s

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
        """True for webcam, RTSP, or HLS/HTTP live streams (no fixed frame count)."""
        if isinstance(self.source, int):
            return True
        s = str(self.source).lower()
        return s.startswith("rtsp") or s.startswith("http://") or s.startswith("https://") or ".m3u8" in s

    # ── Reading ───────────────────────────────────────────────────────────────

    def read(self) -> tuple[np.ndarray | None, FrameMeta]:
        """Read a single frame. Returns (None, meta) on end-of-stream.

        For live sources every read also drains buffered frames that arrived
        while the previous frame was being processed, keeping the processed
        frame at the live edge (video no longer falls behind and then skips)."""
        if self._cap is None:
            raise RuntimeError("VideoSource not opened. Call open() first.")

        ret, frame = self._cap.read()

        if self.is_live and ret:
            # grab() is cheap (< a few ms) while the frame comes from the local
            # buffer — discard those; the frame that costs real network time is
            # the freshest one, so retrieve it as ours.
            while True:
                t0 = time.perf_counter()
                ok = self._cap.grab()
                if not ok:
                    break
                if time.perf_counter() - t0 < 0.004:
                    self._backlog_dropped += 1
                    continue
                ok2, fresher = self._cap.retrieve()
                if ok2 and fresher is not None:
                    frame = fresher
                break

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
