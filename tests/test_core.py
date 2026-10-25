"""Unit tests for core engine components.

Run with: pytest tests/test_core.py -v
No ONNX model or GPU required — detector is mocked.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.analytics import AnalyticsEngine, CountingLine, AnomalyDetector
from core.detector import Detection
from core.tracker import Track


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_track(track_id: int, x1: float, y1: float, x2: float, y2: float, class_id: int = 0) -> Track:
    return Track(track_id=track_id, bbox=(x1, y1, x2, y2), confidence=0.9, class_id=class_id)


def make_detection(x1: float, y1: float, x2: float, y2: float, class_id: int = 0) -> Detection:
    return Detection(bbox=(x1, y1, x2, y2), confidence=0.9, class_id=class_id)


# ── Detection tests ───────────────────────────────────────────────────────────

class TestDetection:
    def test_class_name_assigned(self):
        d = make_detection(0, 0, 100, 100, class_id=0)
        assert d.class_name == "car"

    def test_class_name_bus(self):
        d = make_detection(0, 0, 100, 100, class_id=1)
        assert d.class_name == "bus"

    def test_xywh(self):
        d = make_detection(10, 20, 110, 70, class_id=0)
        x, y, w, h = d.xywh
        assert x == 10 and y == 20 and w == 100 and h == 50

    def test_area(self):
        d = make_detection(0, 0, 50, 100, class_id=0)
        assert d.area == pytest.approx(5000.0)


# ── Track tests ───────────────────────────────────────────────────────────────

class TestTrack:
    def test_center(self):
        t = make_track(1, 0, 0, 100, 100)
        cx, cy = t.center
        assert cx == pytest.approx(50.0)
        assert cy == pytest.approx(50.0)

    def test_class_name(self):
        t = make_track(1, 0, 0, 100, 100, class_id=2)
        assert t.class_name == "motorcycle"


# ── CountingLine tests ────────────────────────────────────────────────────────

class TestCountingLine:
    def test_no_crossing_on_first_frame(self):
        line = CountingLine((0, 100), (1280, 100))
        track = make_track(1, 0, 50, 100, 90)   # center y=70, above line
        crossed = line.update([track])
        assert crossed == []
        assert line.total_count == 0

    def test_crossing_detected(self):
        """Track moves from above line (y<100) to below (y>100)."""
        line = CountingLine((0, 100), (1280, 100))

        # Frame 1: track above line
        track_above = make_track(1, 0, 0, 100, 80, class_id=0)   # center y=40
        line.update([track_above])

        # Frame 2: track below line
        track_below = make_track(1, 0, 120, 100, 200, class_id=0)  # center y=160
        crossed = line.update([track_below])

        assert len(crossed) == 1
        assert line.total_count == 1
        assert line.count_per_class["car"] == 1

    def test_no_false_crossing_same_side(self):
        """Track stays on same side - no count."""
        line = CountingLine((0, 100), (1280, 100))
        # Boxes whose bottom edge never crosses y=100
        for y_top in [0, 10, 20, 30]:
            track = make_track(1, 0, y_top, 100, y_top + 60)
            line.update([track])
        assert line.total_count == 0

    def test_multiple_tracks(self):
        line = CountingLine((0, 100), (1280, 100))

        # Both tracks above
        line.update([
            make_track(1, 0, 0, 100, 80),
            make_track(2, 200, 0, 300, 80),
        ])

        # Both cross
        crossed = line.update([
            make_track(1, 0, 120, 100, 200),
            make_track(2, 200, 120, 300, 200),
        ])
        assert len(crossed) == 2
        assert line.total_count == 2


# ── AnalyticsEngine tests ─────────────────────────────────────────────────────

class TestAnalyticsEngine:
    def test_empty_frame(self):
        engine = AnalyticsEngine()
        metrics = engine.update([])
        assert metrics.vehicles_in_frame == 0
        assert metrics.total_count == 0
        assert metrics.avg_speed_kmh == 0.0

    def test_vehicles_in_frame_count(self):
        engine = AnalyticsEngine()
        tracks = [make_track(i, i * 100, 0, i * 100 + 80, 80) for i in range(5)]
        metrics = engine.update(tracks)
        assert metrics.vehicles_in_frame == 5

    def test_count_increments_on_crossing(self):
        from core.analytics import CountingLineSpec
        engine = AnalyticsEngine(counting_lines=[CountingLineSpec("line", (0, 100), (1280, 100))])

        # Frame 1: above
        engine.update([make_track(1, 0, 0, 100, 80)])
        # Frame 2: below (crossing)
        metrics = engine.update([make_track(1, 0, 120, 100, 200)])

        assert metrics.total_count == 1

    def test_line_specs_preserve_counts_on_edit(self):
        from core.analytics import CountingLineSpec
        engine = AnalyticsEngine(counting_lines=[CountingLineSpec("line1", (0, 100), (1280, 100))])

        # Cross line1
        engine.update([make_track(1, 0, 0, 100, 80)])
        metrics = engine.update([make_track(1, 0, 120, 100, 200)])
        assert metrics.total_count == 1

        # Move line1 to new coordinates (e.g. y=350)
        engine.set_line_specs([CountingLineSpec("line1", (0, 350), (1280, 350))])

        # Total count must NOT reset to 0!
        m2 = engine.update([])
        assert m2.total_count == 1
        assert m2.count_per_line["line1"] == 1

    def test_tracks_in_metrics(self):
        engine = AnalyticsEngine()
        tracks = [make_track(42, 0, 0, 100, 100, class_id=1)]
        metrics = engine.update(tracks)
        assert len(metrics.tracks) == 1
        assert metrics.tracks[0]["id"] == 42
        assert metrics.tracks[0]["class_name"] == "bus"

    def test_frame_index_increments(self):
        engine = AnalyticsEngine()
        m1 = engine.update([])
        m2 = engine.update([])
        assert m2.frame_index == m1.frame_index + 1


# ── AnomalyDetector tests ─────────────────────────────────────────────────────

class TestAnomalyDetector:
    def test_no_alert_on_small_history(self):
        detector = AnomalyDetector()
        # Fill with 10 frames — not enough for spike detection (needs 60)
        for _ in range(10):
            alerts = detector.update([make_track(1, 0, 0, 100, 100)])
        assert not any("surge" in a.message for a in alerts)

    def test_stopped_vehicle_alert(self):
        detector = AnomalyDetector()
        detector.STOPPED_MIN_FRAMES = 3   # speed up test

        # Same position for 3+ frames
        track = make_track(99, 50, 50, 150, 150)
        alerts = []
        for _ in range(4):
            alerts = detector.update([track])

        assert any("Stopped" in a.message for a in alerts)


    def test_id_mode_counts_motorcycles_in_frame_immediately(self):
        engine = AnalyticsEngine()
        engine.set_count_mode("id")

        # Frame 1: 2 motorcycles (class_id=2) and 1 car (class_id=0) in frame
        tracks = [
            make_track(10, 50, 50, 90, 120, class_id=2),
            make_track(11, 150, 60, 190, 130, class_id=2),
            make_track(12, 300, 100, 420, 220, class_id=0),
        ]
        m1 = engine.update(tracks)
        assert m1.total_count == 3
        assert m1.count_per_class["motorcycle"] == 2
        assert m1.count_per_class["car"] == 1
        assert len(m1.crossings) == 3
        assert {c["track_id"] for c in m1.crossings} == {10, 11, 12}

        # Frame 2: same IDs still in frame -> must NOT double-count
        m2 = engine.update(tracks)
        assert m2.total_count == 3
        assert m2.count_per_class["motorcycle"] == 2
        assert m2.crossings == []

    def test_switch_line_to_id_counts_uncounted_in_frame_vehicles(self):
        from core.analytics import CountingLineSpec
        engine = AnalyticsEngine(counting_lines=[CountingLineSpec("line1", (0, 200), (1280, 200))])

        # Track 1 crosses line1; Track 2 (motorcycle) is in frame above line1 (uncounted)
        engine.update([
            make_track(1, 0, 50, 100, 150, class_id=0),
            make_track(2, 200, 50, 250, 120, class_id=2),
        ])
        m_line = engine.update([
            make_track(1, 0, 220, 100, 320, class_id=0),
            make_track(2, 200, 60, 250, 130, class_id=2),
        ])
        assert m_line.total_count == 1
        assert m_line.count_per_class == {"car": 1}

        # Switch to "id" mode while Track 1 and Track 2 are still in frame:
        # Track 1 was already counted by line1 (must not double-count),
        # Track 2 (motorcycle) was NOT counted yet (must be counted immediately).
        engine.set_count_mode("id")
        m_id = engine.update([
            make_track(1, 0, 240, 100, 340, class_id=0),
            make_track(2, 200, 70, 250, 140, class_id=2),
        ])
        assert m_id.total_count == 2
        assert m_id.count_per_class["car"] == 1
        assert m_id.count_per_class["motorcycle"] == 1
        assert len(m_id.crossings) == 1
        assert m_id.crossings[0]["track_id"] == 2
        assert m_id.crossings[0]["class_name"] == "motorcycle"


def test_hls_pipeline_preserves_all_segment_frames():
    import asyncio
    from core.pipeline import Pipeline, PipelineConfig
    from core.video_source import FrameMeta

    class FakeHlsSource:
        fps = 50.0
        is_live = True
        is_hls = True

        def __init__(self, total: int = 30) -> None:
            self._total = total
            self._idx = 0

        def read(self):
            if self._idx >= self._total:
                return None, FrameMeta(self._idx, 0.0, "http://example/live.m3u8")
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            meta = FrameMeta(self._idx, 0.0, "http://example/live.m3u8")
            self._idx += 1
            return frame, meta

        def close(self) -> None:
            pass

    class FakeDetector:
        def __init__(self) -> None:
            self.calls = 0

        def detect(self, _frame):
            self.calls += 1
            return []

    pipeline = Pipeline(PipelineConfig(source="http://example/live.m3u8", draw_annotations=False))

    def fake_setup():
        from core.tracker import Tracker
        pipeline._source = FakeHlsSource(30)
        pipeline._detector = FakeDetector()
        pipeline._tracker = Tracker(frame_rate=50)
        pipeline._analytics = AnalyticsEngine(fps=50.0)

    pipeline.setup = fake_setup

    async def collect():
        out = []
        async for jpeg, met in pipeline.run_async():
            out.append((jpeg, met))
        return out

    results = asyncio.run(collect())
    assert len(results) == 30
    assert pipeline._detector.calls == 30
