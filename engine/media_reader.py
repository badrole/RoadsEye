"""Video input abstraction — file, webcam, or RTSP stream."""
from __future__ import annotations

from collections import deque
import concurrent.futures
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Generator
import urllib.parse

import cv2
import httpx
import numpy as np


@dataclass
class FrameMeta:
    index: int          # absolute frame number since source opened
    timestamp: float    # wall-clock seconds since source opened
    source: str         # path or stream URL
    pos_ms: float = 0.0 # stream playback position (PTS) in milliseconds


class _HlsPrefetcher:
    """Prefetches HLS .ts segments in parallel byte ranges to bypass per-socket throttling."""

    _HEADERS = {"User-Agent": "Lavf/60.16.100"}
    _RANGE_WORKERS = 12
    _MIN_RANGE_BYTES = 512_000

    def __init__(self, url: str) -> None:
        self.url = url
        self._dir = Path(tempfile.mkdtemp(prefix="roadseye_hls_"))
        self._q: queue.Queue[Path] = queue.Queue(maxsize=3)
        self._stop = threading.Event()
        self._seen: deque[str] = deque(maxlen=256)
        self._seq = 0
        self._client = httpx.Client(headers=self._HEADERS, follow_redirects=True, timeout=15.0)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=self._RANGE_WORKERS)
        self._thread: threading.Thread | None = None

    def start_and_prime(self) -> Path:
        media_url, segs, is_vod = self._fetch_segments(self.url)
        if not segs:
            raise RuntimeError("Empty or unsupported HLS playlist")
        self.url = media_url
        if not is_vod and len(segs) > 3:
            self._seen.extend(segs[:-3])
            segs = segs[-3:]
        first_url = segs[0]
        self._seen.append(first_url)
        first_path = self._download_segment(first_url)
        if first_path is None:
            raise RuntimeError(f"Failed to download initial HLS segment: {first_url}")
        self._thread = threading.Thread(target=self._run, name="hls-prefetcher", daemon=True)
        self._thread.start()
        return first_path

    def next_segment(self, timeout: float = 12.0) -> Path | None:
        deadline = time.monotonic() + timeout
        while not self._stop.is_set():
            rem = deadline - time.monotonic()
            if rem <= 0:
                return None
            try:
                return self._q.get(timeout=min(0.2, rem))
            except queue.Empty:
                continue
        return None

    def stop_signal(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._pool.shutdown(wait=False, cancel_futures=True)
        try:
            self._client.close()
        except Exception:
            pass
        shutil.rmtree(self._dir, ignore_errors=True)

    def _fetch_segments(self, playlist_url: str, depth: int = 0) -> tuple[str, list[str], bool]:
        r = self._client.get(playlist_url, timeout=10.0)
        r.raise_for_status()
        text = r.text
        if "#EXT-X-KEY" in text or "#EXT-X-MAP" in text:
            return playlist_url, [], False
        base = str(r.url)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        entries = [urllib.parse.urljoin(base, ln) for ln in lines if not ln.startswith("#")]
        if not entries:
            return base, [], False
        if depth < 2 and all(".m3u8" in u.lower() for u in entries):
            return self._fetch_segments(entries[-1], depth + 1)
        is_vod = "#EXT-X-ENDLIST" in text
        return base, entries, is_vod

    def _download_segment(self, seg_url: str) -> Path | None:
        if self._stop.is_set():
            return None
        try:
            head = self._client.head(seg_url, timeout=5.0)
            size = int(head.headers.get("content-length") or 0)
            ranges_ok = "bytes" in head.headers.get("accept-ranges", "").lower()
        except Exception:
            size, ranges_ok = 0, False

        data: bytes | None = None
        if ranges_ok and size >= self._MIN_RANGE_BYTES:
            n = self._RANGE_WORKERS
            step = size // n
            spans = [(i * step, size - 1 if i == n - 1 else (i + 1) * step - 1) for i in range(n)]

            def _fetch_part(span: tuple[int, int]) -> bytes:
                if self._stop.is_set():
                    raise RuntimeError("stopped")
                resp = self._client.get(
                    seg_url,
                    headers={"Range": f"bytes={span[0]}-{span[1]}"},
                    timeout=15.0,
                )
                if resp.status_code != 206:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                return resp.content

            try:
                data = b"".join(self._pool.map(_fetch_part, spans))
            except Exception:
                data = None

        if data is None and not self._stop.is_set():
            try:
                resp = self._client.get(seg_url, timeout=20.0)
                if resp.status_code == 200:
                    data = resp.content
            except Exception:
                return None

        if not data or self._stop.is_set():
            return None
        self._seq += 1
        out = self._dir / f"seg_{self._seq:06d}.ts"
        out.write_bytes(data)
        return out

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _, segs, is_vod = self._fetch_segments(self.url)
                unseen = [u for u in segs if u not in self._seen]
                if not unseen:
                    if is_vod:
                        break
                    self._stop.wait(1.0)
                    continue
                if not is_vod and len(unseen) > 4:
                    self._seen.extend(unseen[:-3])
                    unseen = unseen[-3:]
                for seg_url in unseen:
                    if self._stop.is_set():
                        break
                    self._seen.append(seg_url)
                    path = self._download_segment(seg_url)
                    if path is None:
                        continue
                    while not self._stop.is_set():
                        try:
                            self._q.put(path, timeout=0.2)
                            break
                        except queue.Full:
                            pass
                    if self._stop.is_set() and path.exists():
                        path.unlink(missing_ok=True)
                if is_vod:
                    break
            except Exception:
                self._stop.wait(1.0)


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
        max_width: int = 1280,
        loop: bool = False,
    ) -> None:
        self.source = source
        self._target_w = width
        self._target_h = height
        self._max_w = max_width
        self.loop = loop
        self._cap: cv2.VideoCapture | None = None
        self._hls: _HlsPrefetcher | None = None
        self._cur_seg_path: Path | None = None
        self._io_lock = threading.Lock()   # read() vs close()/release() — FFmpeg aborts on overlap
        self._frame_idx: int = 0
        self._t0: float = 0.0
        self._pos_ms: float = 0.0
        self._cached_fps: float = 0.0
        self._cached_w: int = 0
        self._cached_h: int = 0

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "VideoSource":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    # RTSP uses real-time RTP packets over TCP with low-delay flags.
    # HLS/HTTP uses segmented streams where nobuffer/low_delay breaks B-frame
    # reordering and stalls at every .ts segment boundary.
    _RTSP_FFMPEG_OPTIONS = (
        "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|"
        "analyzeduration;1000000|probesize;500000|max_delay;500000|stimeout;10000000"
    )
    _HTTP_FFMPEG_OPTIONS = (
        "reconnect;1|reconnect_streamed;1|reconnect_delay_max;5|"
        "timeout;10000000|analyzeduration;1500000|probesize;1000000"
    )

    def open(self) -> None:
        prev_opts = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        s = str(self.source).lower() if not isinstance(self.source, int) else ""
        if self.is_hls and s.startswith(("http://", "https://")):
            hls = _HlsPrefetcher(str(self.source))
            try:
                first_seg = hls.start_and_prime()
                cap = cv2.VideoCapture(str(first_seg), cv2.CAP_FFMPEG)
                if cap.isOpened():
                    self._hls = hls
                    self._cur_seg_path = first_seg
                    self._cap = cap
                else:
                    cap.release()
                    hls.close()
            except Exception:
                hls.close()

        if self._cap is None:
            if s.startswith(("rtsp://", "rtsps://")):
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._RTSP_FFMPEG_OPTIONS
            elif s.startswith(("http://", "https://")) or ".m3u8" in s:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._HTTP_FFMPEG_OPTIONS
            try:
                if isinstance(self.source, int):
                    if sys.platform == "win32":
                        self._cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
                        if not self._cap.isOpened():
                            self._cap.release()
                            self._cap = cv2.VideoCapture(self.source)
                    else:
                        self._cap = cv2.VideoCapture(self.source)
                else:
                    self._cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            finally:
                if prev_opts is None:
                    os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
                else:
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = prev_opts
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video source: {self.source!r}")
        if self._is_live_source(self.source) and not self.is_hls:
            try:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            except Exception:
                pass
        raw_fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        # Guard against bogus MPEG-TS / HLS clock values (e.g. 90000 fps or 0 fps)
        self._cached_fps = raw_fps if 5.0 <= raw_fps <= 60.0 else 25.0
        raw_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        raw_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if not self._target_w and not self._target_h and self._max_w and raw_w > self._max_w:
            scale = self._max_w / raw_w
            self._cached_w = self._max_w
            self._cached_h = int(round(raw_h * scale))
        else:
            self._cached_w = self._target_w or raw_w
            self._cached_h = self._target_h or raw_h
        self._frame_idx = 0
        self._t0 = time.monotonic()

    @staticmethod
    def _is_live_source(source: str | int) -> bool:
        if isinstance(source, int):
            return True
        s = str(source).lower()
        return s.startswith("rtsp") or s.startswith(("http://", "https://")) or ".m3u8" in s

    @property
    def is_hls(self) -> bool:
        if isinstance(self.source, int):
            return False
        return ".m3u8" in str(self.source).lower()

    def close(self) -> None:
        if self._hls is not None:
            self._hls.stop_signal()
        with self._io_lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            if self._cur_seg_path is not None:
                self._cur_seg_path.unlink(missing_ok=True)
                self._cur_seg_path = None
            if self._hls is not None:
                self._hls.close()
                self._hls = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def fps(self) -> float:
        if self._cap is None:
            return 0.0
        return self._cached_fps or 25.0

    @property
    def width(self) -> int:
        if self._cap is None:
            return 0
        return self._cached_w

    @property
    def height(self) -> int:
        if self._cap is None:
            return 0
        return self._cached_h

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

        The I/O lock keeps cap.read() and cap.release() from overlapping:
        OpenCV/FFmpeg hard-aborts the whole process (async_lock assertion)
        when release() races an in-flight read."""
        with self._io_lock:
            if self._cap is None:
                return None, self._make_meta()
            ret, frame = self._cap.read()
            if not ret and self._hls is not None:
                ret, frame = self._advance_hls_segment()
            elif not ret and self.loop and not self.is_live:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
        if frame is not None:
            frame = self._resize(frame)
        meta = self._make_meta()
        self._frame_idx += 1
        return frame, meta

    def _advance_hls_segment(self) -> tuple[bool, np.ndarray | None]:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        if self._cur_seg_path is not None:
            self._cur_seg_path.unlink(missing_ok=True)
            self._cur_seg_path = None
        while self._hls is not None:
            next_path = self._hls.next_segment(timeout=12.0)
            if next_path is None:
                return False, None
            self._cur_seg_path = next_path
            self._cap = cv2.VideoCapture(str(next_path), cv2.CAP_FFMPEG)
            if self._cap.isOpened():
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    return True, frame
            self._cap.release()
            self._cap = None
            next_path.unlink(missing_ok=True)
            self._cur_seg_path = None
        return False, None

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

    def _make_meta(self, pos_ms: float = 0.0) -> FrameMeta:
        return FrameMeta(
            index=self._frame_idx,
            timestamp=time.monotonic() - self._t0,
            source=str(self.source),
            pos_ms=pos_ms,
        )

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if self._target_w or self._target_h:
            target_w = self._target_w or w
            target_h = self._target_h or h
        elif self._max_w and w > self._max_w:
            target_w = self._max_w
            target_h = int(round(h * (self._max_w / w)))
        else:
            return frame
        if (w, h) == (target_w, target_h):
            return frame
        return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

    def __repr__(self) -> str:
        return (
            f"VideoSource(source={self.source!r}, "
            f"{self.width}x{self.height} @ {self.fps:.1f}fps)"
        )
