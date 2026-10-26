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

    def test_line_counts_motorcycle_occluded_at_line_same_id(self):
        line = CountingLine((0, 100), (1280, 100))
        # Frame 1: motorcycle above line
        line.update([make_track(7, 200, 30, 240, 85, class_id=2)])
        # Frames 2-5: motorcycle occluded at the line (absent from tracks)
        for _ in range(4):
            line.update([])
        # Frame 6: motorcycle re-emerges below line with same ID
        crossed = line.update([make_track(7, 200, 115, 240, 170, class_id=2)])
        assert len(crossed) == 1
        assert line.total_count == 1
        assert line.count_per_class["motorcycle"] == 1

    def test_line_counts_motorcycle_occluded_at_line_with_new_id(self):
        line = CountingLine((0, 100), (1280, 100))
        # Frames 1-2: motorcycle approaches line from above (ID 15)
        line.update([make_track(15, 300, 20, 340, 70, class_id=2)])
        line.update([make_track(15, 302, 40, 342, 90, class_id=2)])
        # Frames 3-4: motorcycle occluded right on the line
        line.update([])
        line.update([])
        # Frame 5: motorcycle reappears just below the line with new tracker ID 29
        crossed = line.update([make_track(29, 305, 110, 345, 160, class_id=2)])
        assert len(crossed) == 1
        assert line.total_count == 1
        assert line.count_per_class["motorcycle"] == 1

    def test_id_mode_counting_zone_only_counts_when_bbox_fully_inside(self):
        zone = [(200, 150), (1000, 150), (1000, 600), (200, 600)]
        engine = AnalyticsEngine(counting_zone=zone)
        engine.set_count_mode("id")

        # 1. Same truck (ID 60) outside zone (behind obstacle, detected as 'car' and 'bus' with high conf) -> NOT counted
        m1 = engine.update([make_track(60, 50, 200, 180, 320, class_id=0)])
        assert m1.total_count == 0
        m2 = engine.update([make_track(60, 150, 200, 310, 320, class_id=1)])
        assert m2.total_count == 0

        # 2. Same truck (ID 60) enters 100% inside obstacle-free zone (detected as 'truck' conf 0.60)
        # -> Out-of-zone 'car'/'bus' votes are cleared, so it is COUNTED and LABELED as 'truck'!
        m3 = engine.update([Track(track_id=60, bbox=(220, 200, 420, 330), confidence=0.60, class_id=3)])
        assert m3.total_count == 1
        assert m3.count_per_class == {"truck": 1}
        assert m3.tracks[0]["class_name"] == "truck"
        assert "bus" not in m3.count_per_class
        assert "car" not in m3.count_per_class

        # 3. Vehicle partially exits zone on the right (x2=1050 > 1000, misclassified as 'bus')
        # -> must NOT double-count and must keep locked class 'truck'
        m4 = engine.update([make_track(60, 880, 200, 1050, 330, class_id=1)])
        assert m4.total_count == 1
        assert m4.count_per_class == {"truck": 1}
        assert m4.tracks[0]["class_name"] == "truck"


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


def test_confidence_weighted_class_stabilization_and_anti_bus_spike():
    zone = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    engine = AnalyticsEngine(fps=25.0, pixels_per_meter=10.0, counting_zone=zone)
    engine.set_count_mode("id")

    # 1) Car occluded as low-conf truck (0.42) -> clear high-conf car (0.88, 0.89) -> low-conf truck (0.51)
    engine.update([Track(track_id=1, bbox=(100, 100, 180, 170), confidence=0.42, class_id=3)])
    m2 = engine.update([Track(track_id=1, bbox=(100, 110, 180, 180), confidence=0.88, class_id=0)])
    m3 = engine.update([Track(track_id=1, bbox=(100, 120, 180, 190), confidence=0.89, class_id=0)])
    m4 = engine.update([Track(track_id=1, bbox=(100, 130, 180, 200), confidence=0.51, class_id=3)])

    assert m2.tracks[0]["class_name"] == "car"
    assert m3.tracks[0]["class_name"] == "car"
    assert m4.tracks[0]["class_name"] == "car"
    assert m4.count_per_class.get("car", 0) == 1
    assert m4.count_per_class.get("truck", 0) == 0

    # 2) Truck with consistent truck frames must NOT flip to bus on a 1-frame bus spike or bbox expansion
    for i in range(4):
        engine.update([Track(track_id=2, bbox=(600, 100 + i * 10, 710, 190 + i * 10), confidence=0.76, class_id=3)])
    bigger_spike = Track(
        track_id=2,
        bbox=(580, 120, 760, 260),
        confidence=0.80,
        class_id=1,
    )
    m_spike = engine.update([bigger_spike])
    assert m_spike.tracks[0]["class_name"] == "truck"
    assert m_spike.count_per_class.get("truck", 0) == 1
    assert m_spike.count_per_class.get("bus", 0) == 0


def test_stopped_vehicle_jitter_deadband_and_stopped_duration():
    engine = AnalyticsEngine(fps=25.0, pixels_per_meter=10.0)

    # Simulate a stopped car with +/-1.2 px bbox jitter (previously caused ~10.8 km/h false speed)
    metrics = None
    for frame_idx in range(10):
        jitter = 1.2 if (frame_idx % 2 == 0) else -1.2
        metrics = engine.update([
            Track(
                track_id=7,
                bbox=(200.0 + jitter, 150.0 - jitter, 280.0 + jitter, 220.0 - jitter),
                confidence=0.88,
                class_id=0,
            )
        ])

    assert metrics is not None
    tr = metrics.tracks[0]
    assert tr["speed_kmh"] == 0.0
    assert tr["stopped_sec"] >= 0.0

