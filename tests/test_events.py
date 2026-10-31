# Tests for Phase 1 SQLite persistence (server.event_store) â€” all sync, no asyncio.
#
# AnalyticsEngine and events_db are synchronous; each test uses its own temp
# DB so the real LOCALAPPDATA file is never touched.
#
# Run with: pytest tests/test_events.py -v
from __future__ import annotations

import datetime as dt

import pytest

import server.event_store as events_db
from engine.traffic_metrics import AnalyticsEngine
from engine.traffic_metrics import CountingLineSpec
from engine.vehicle_tracker import Track


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def setup_db(tmp_path):
    events_db.configure(tmp_path / "events.db")
    return events_db.init_events_db()


def make_track(track_id: int, x1: float, y1: float, x2: float, y2: float, class_id: int = 0) -> Track:
    return Track(track_id=track_id, bbox=(x1, y1, x2, y2), confidence=0.9, class_id=class_id)


def make_engine() -> AnalyticsEngine:
    return AnalyticsEngine(counting_lines=[CountingLineSpec("line1", (0, 300), (1280, 300))])


def drive_crossing(engine: AnalyticsEngine, track_id: int = 1, class_id: int = 0):
    engine.update([make_track(track_id, 0, 0, 200, 200, class_id=class_id)])
    return engine.update([make_track(track_id, 100, 300, 300, 400, class_id=class_id)])


def save_events(test_events, session_id: str = "sess1", camera_id: str = "cam1"):
    events_db.open_session(session_id, camera_id)
    rows = []
    for ev in test_events:
        rows.append(events_db.make_event(
            camera_id=camera_id,
            session_id=session_id,
            track_id=ev["track_id"],
            vehicle_type=ev["class_name"],
            confidence=ev["confidence"],
            direction=ev["direction"],
            counting_line_id=ev["line_name"],
            timestamp_epoch=ev["timestamp"],
        ))
    if rows:
        events_db.write_events(rows)
    return rows


def utc_epoch(y: int, m: int, d: int, h: int, mi: int = 0) -> float:
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


# â”€â”€ 1. A counted crossing inserts exactly one row â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_count_inserts_one_row(tmp_path):
    setup_db(tmp_path)
    engine = make_engine()
    crossings = drive_crossing(engine).crossings
    assert len(crossings) == 1

    saved = save_events(crossings)

    rows = events_db.query_events()
    assert rows["total"] == 1
    row = rows["items"][0]
    assert row["event_id"] == saved[0]["event_id"]
    assert row["track_id"] == 1
    assert row["vehicle_type"] == "car"
    assert row["counting_line_id"] == "line1"
    assert row["direction"] in ("positive", "negative")


# â”€â”€ 2. Repeated insert tidak menduplikasi â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_no_duplicate_on_reinsert(tmp_path):
    setup_db(tmp_path)
    engine = make_engine()
    crossings = drive_crossing(engine).crossings

    saved = save_events(crossings)
    # Simulasi retry: event yang SAMA dikirim ulang 2x
    events_db.write_events([saved[0], saved[0]])

    rows = events_db.query_events()
    assert rows["total"] == 1


def test_idling_near_line_does_not_recount(tmp_path):
    setup_db(tmp_path)
    engine = AnalyticsEngine(counting_lines=[CountingLineSpec("line1", (0, 300), (1280, 300))])
    # Kendaraan masuk garis, lalu jitter Â±2 px di sekitarnya
    engine.update([make_track(5, 0, 0, 200, 280)])
    crossings = []
    for jitter in (0, 2, -2, 3, -1, 2):
        crossings.extend(engine.update([make_track(5, 100 + jitter, 250 + jitter, 320 + jitter, 300 + jitter)]).crossings)
    assert len(crossings) == 0     # tidak ada crossing baru saat bergetar di garis


# â”€â”€ 3. Re-open DB tidak menghapus data (simulasi refresh/relaunch) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_reopen_preserves_events(tmp_path):
    setup_db(tmp_path)
    engine = make_engine()
    crossings = drive_crossing(engine).crossings
    save_events(crossings)

    # Buka ulang: re-init + fresh connection, tanpa delete
    events_db.init_events_db()
    s = events_db.history_summary()
    assert s["total_events"] == 1


# â”€â”€ 4. Stop deteksi tidak menghapus data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_close_session_keeps_events(tmp_path):
    setup_db(tmp_path)
    engine = make_engine()
    crossings = drive_crossing(engine).crossings
    save_events(crossings)

    before = events_db.query_events()
    events_db.close_session("sess1", status="stopped")
    after = events_db.query_events()

    assert before["total"] == after["total"] == 1
    sessions = events_db.list_sessions()
    assert sessions[0]["status"] == "stopped"
    assert sessions[0]["ended_at_utc"] is not None


# â”€â”€ 5. Restart backend: data lama terbaca lagi â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_reset_pool_keeps_data(tmp_path):
    setup_db(tmp_path)
    engine = make_engine()
    crossings = drive_crossing(engine).crossings
    save_events(crossings)

    events_db.reset_pool()
    s = events_db.history_summary()
    assert s["total_events"] == 1
    assert s["count_per_class"].get("car") == 1


# --- 6. Reuse of a track id across sessions must not merge rows ---------------

def test_track_id_scoped_by_session(tmp_path):
    setup_db(tmp_path)
    ev1 = events_db.make_event(vehicle_type="car", track_id=1, session_id="sess-A")
    ev2 = events_db.make_event(vehicle_type="car", track_id=1, session_id="sess-B")
    events_db.write_events([ev1, ev2])

    rows = events_db.query_events()
    assert rows["total"] == 2
    sids = {r["session_id"] for r in rows["items"]}
    assert sids == {"sess-A", "sess-B"}


# --- 7. A db failure is reported, never silently dropped ---------------------

def test_failed_batch_is_reported(tmp_path, monkeypatch):
    setup_db(tmp_path)
    one = events_db.make_event(vehicle_type="car", track_id=1)

    def mock_write(batch):
        import sqlite3 as _s
        raise _s.OperationalError("disk error simulation")

    monkeypatch.setattr(events_db, "write_events", mock_write)
    result = events_db.writer._write_with_retry([one])
    assert result == 0


# --- 8. Filters and pagination return the right rows -------------------------

def _t(y, m, d, h, mi):
    return dt.datetime(y, m, d, h, mi, tzinfo=dt.timezone.utc).timestamp()


def _make(ts, vtype, tid):
    return events_db.make_event(vehicle_type=vtype, track_id=tid, timestamp_epoch=ts)


def test_filters_and_pagination(tmp_path):
    setup_db(tmp_path)
    rows = [
        _make(_t(2026, 10, 17, 1, 0), "car", 1),
        _make(_t(2026, 10, 10, 2, 0), "car", 2),
        _make(_t(2026, 10, 10, 3, 0), "motorcycle", 3),
    ]
    events_db.write_events(rows)

    cars = events_db.query_events(vehicle_type="car")
    assert cars["total"] == 2

    page1 = events_db.query_events(limit=2, offset=0)
    page2 = events_db.query_events(limit=2, offset=2)
    assert page1["total"] == page2["total"] == 3
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1

    day = events_db.query_events(date_from="2026-10-17", date_to="2026-10-17")
    assert day["total"] == 1


# --- 9. Hourly buckets match event timestamps (Asia/Jakarta) -----------------

def test_hourly_buckets_jakarta(tmp_path):
    setup_db(tmp_path)
    rows = [
        _make(_t(2026, 10, 17, 3, 30), "car", 1),      # 10:30 WIB same day
        _make(_t(2026, 10, 17, 17, 0), "car", 2),      # 00:00 WIB NEXT day
    ]
    events_db.write_events(rows)

    s = events_db.history_summary()
    hours = {h["hour"]: h["total"] for h in s["hourly"]}
    assert hours.get("2026-10-17T10:00:00") == 1
    assert hours.get("2026-10-18T00:00:00") == 1
    assert s["total_events"] == 2


# --- 10. Today cumulative counts resume ---------------------------------------

def test_today_counts_and_resume(tmp_path):
    setup_db(tmp_path)
    now_epoch = dt.datetime.now(dt.timezone.utc).timestamp()
    events_db.write_events([
        events_db.make_event(vehicle_type="motorcycle", timestamp_epoch=now_epoch),
        events_db.make_event(vehicle_type="motorcycle", timestamp_epoch=now_epoch),
        events_db.make_event(vehicle_type="car", timestamp_epoch=now_epoch),
    ])
    counts = events_db.get_today_counts()
    assert counts.get("motorcycle") == 2
    assert counts.get("car") == 1