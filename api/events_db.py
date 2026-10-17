# -*- coding: utf-8 -*-
"""Persistent event database (Phase 1) — stdlib sqlite3 (sync).

Why sync sqlite3 instead of aiosqlite: the async plugin/thread stack in this
project's venv deadlocks under pytest on Windows (worker-thread wedge). Sync
calls run off the inference path via asyncio.to_thread in the pipeline loop,
and FastAPI runs sync endpoints in its threadpool automatically.

Location (in priority order):
    1. ROADSEYE_DB_PATH env var (absolute path to the .db file)
    2. %LOCALAPPDATA%\\RoadsEye\\data\\roadseye.db  (Windows default)
    3. ~/.roadseye/data/roadseye.db                 (non-Windows fallback)

Design:
- WAL mode, short transactions, thread-local connections.
- event_id is the primary key; INSERT OR IGNORE makes retries idempotent.
- History is never deleted by normal operation (stop/refresh/restart).
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    env = os.environ.get("ROADSEYE_DB_PATH", "").strip()
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "RoadsEye" / "data" / "roadseye.db"
    return Path.home() / ".roadseye" / "data" / "roadseye.db"


DB_PATH: Path = _default_db_path()


def configure(path: str | Path) -> None:
    # Override DB path (tests). Closes pooled connections so a stale
    # thread-local from a previous test file cannot survive.
    global DB_PATH
    DB_PATH = Path(path)
    reset_pool()


def reset_pool() -> None:
    with _pool_lock:
        for db in list(_pool.values()):
            try:
                db.close()
            except sqlite3.Error:
                pass
        _pool.clear()


_pool: dict[int, sqlite3.Connection] = {}
_pool_lock = threading.Lock()

_JAKARTA_OFFSET_S = 7 * 3600   # Asia/Jakarta = UTC+7, no DST

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_sessions (
    session_id     TEXT PRIMARY KEY,
    camera_id      TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    ended_at_utc   TEXT,
    status         TEXT NOT NULL DEFAULT 'running',
    source_detail  TEXT
);

CREATE TABLE IF NOT EXISTS vehicle_events (
    event_id         TEXT PRIMARY KEY,
    camera_id        TEXT NOT NULL,
    session_id       TEXT NOT NULL REFERENCES processing_sessions(session_id),
    track_id         INTEGER NOT NULL,        -- ByteTrack ID, scoped to camera+session
    vehicle_type     TEXT NOT NULL,
    timestamp_utc    TEXT NOT NULL,           -- ISO-8601 UTC, canonical
    timestamp_epoch  REAL NOT NULL,           -- unix seconds, fast filtering/aggregation
    direction        TEXT,                    -- side change across the line: positive/negative
    confidence       REAL,
    counting_line_id TEXT NOT NULL,
    created_at_utc   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ve_timestamp ON vehicle_events (timestamp_epoch);
CREATE INDEX IF NOT EXISTS idx_ve_camera    ON vehicle_events (camera_id);
CREATE INDEX IF NOT EXISTS idx_ve_type      ON vehicle_events (vehicle_type);
CREATE INDEX IF NOT EXISTS idx_ve_session   ON vehicle_events (session_id);

CREATE TABLE IF NOT EXISTS system_logs (
    log_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc   TEXT NOT NULL,
    level    TEXT NOT NULL,
    component TEXT,
    message  TEXT NOT NULL
);
"""


def _get_conn() -> sqlite3.Connection:
    tid = threading.get_ident()
    with _pool_lock:
        conn = _pool.get(tid)
        if conn is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            _pool[tid] = conn
        return conn


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _log_stderr(msg: str) -> None:
    # Never print() from the writer thread under pytest — pytest's fd-capture
    # proxy deadlocks on Windows. Plain file + original stderr instead.
    try:
        with open(DB_PATH.parent / "roadseye.log", "a", encoding="utf-8", errors="replace") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass
    try:
        sys.__stderr__.write(msg + "\n")
        sys.__stderr__.flush()
    except Exception:
        pass


def init_events_db() -> Path:
    """Create schema if missing; returns resolved absolute path.

    Never deletes data. If the existing file is corrupt, this raises —
    a fresh empty database is NOT silently created over it.
    """
    resolved = DB_PATH.resolve()
    _get_conn().executescript(_SCHEMA)   # implicit commit
    return resolved


# ── Session lifecycle ────────────────────────────────────────────────────────

def open_session(session_id: str, camera_id: str, source_detail: str | None = None) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO processing_sessions
           (session_id, camera_id, started_at_utc, status, source_detail)
           VALUES (?, ?, ?, 'running', ?)""",
        (session_id, camera_id, utc_now_iso(), source_detail),
    )
    conn.commit()


def close_session(session_id: str, status: str = "stopped") -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE processing_sessions SET ended_at_utc = ?, status = ? WHERE session_id = ?",
        (utc_now_iso(), status, session_id),
    )
    conn.commit()


def list_sessions(limit: int = 50) -> list[dict]:
    rows = _get_conn().execute(
        """SELECT * FROM processing_sessions
           ORDER BY started_at_utc DESC LIMIT ?""", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Event writer (background thread, never blocks the pipeline) ──────────────

_QUEUE_MAX = 10_000
_RETRY_BACKOFF = (0.2, 0.5, 1.0)   # seconds; after the last retry the batch is logged


def make_event(
    *,
    camera_id: str,
    session_id: str,
    track_id: int,
    vehicle_type: str,
    confidence: float | None,
    direction: str | None,
    counting_line_id: str,
    timestamp_epoch: float | None = None,
) -> dict[str, Any]:
    ts = timestamp_epoch if timestamp_epoch is not None else time.time()
    return {
        "event_id": uuid.uuid4().hex,
        "camera_id": camera_id,
        "session_id": session_id,
        "track_id": track_id,
        "vehicle_type": vehicle_type,
        "timestamp_utc": _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(),
        "timestamp_epoch": ts,
        "direction": direction,
        "confidence": confidence,
        "counting_line_id": counting_line_id,
        "created_at_utc": utc_now_iso(),
    }


_INSERT_SQL = """INSERT OR IGNORE INTO vehicle_events
    (event_id, camera_id, session_id, track_id, vehicle_type,
     timestamp_utc, timestamp_epoch, direction, confidence,
     counting_line_id, created_at_utc)
    VALUES (:event_id, :camera_id, :session_id, :track_id, :vehicle_type,
            :timestamp_utc, :timestamp_epoch, :direction, :confidence,
            :counting_line_id, :created_at_utc)"""


def write_events(batch: list[dict]) -> int:
    """Insert a batch in one short transaction; returns rows actually inserted."""
    conn = _get_conn()
    with conn:
        cur = conn.executemany(_INSERT_SQL, batch)
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


class EventWriter:
    """Queues confirmed events; a daemon thread writes them in short
    transactions. enqueue() never blocks the pipeline loop. Retries use
    INSERT OR IGNORE keyed by event_id — safe, never duplicates. A failed
    batch is logged to system_logs + stderr — never discarded silently.
    """

    def __init__(self) -> None:
        self._q: list[dict] = []
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self.dropped: int = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="events-writer", daemon=True)
        self._thread.start()

    def enqueue(self, record: dict[str, Any] | None) -> None:
        if record is None:
            return
        dropped_now = False
        with self._cv:
            if len(self._q) >= _QUEUE_MAX:
                self.dropped += 1
                dropped_now = True
            else:
                self._q.append(record)
            self._cv.notify()
        if dropped_now:
            _log_stderr(f"[events_db] queue full — event dropped (total {self.dropped})")

    def stop(self, timeout: float = 30.0) -> None:
        """Flush remaining events, then let the writer thread idle."""
        with self._cv:
            batch = self._q[:]
            self._q.clear()
        if batch:
            self._write_with_retry(batch)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._q:
                    self._cv.wait(timeout=1.0)
                batch = self._q[:]
                self._q.clear()
            self._write_with_retry(batch)

    def _write_with_retry(self, batch: list[dict]) -> None:
        for attempt, backoff in enumerate((0.0,) + _RETRY_BACKOFF):
            if backoff:
                time.sleep(backoff)
            try:
                write_events(batch)
                return
            except sqlite3.Error as exc:
                if attempt == len(_RETRY_BACKOFF):
                    # NOT silent: log to system_logs (fresh attempt) + stderr
                    try:
                        log_system("error", "events_db",
                                   f"Persist failed after retries — {len(batch)} events lost. {exc}")
                    except Exception:
                        pass
                    _log_stderr(f"[events_db] {len(batch)} events LOST after retries: {exc}")
                    return


writer = EventWriter()


def log_system(level: str, component: str, message: str) -> None:
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO system_logs (ts_utc, level, component, message) VALUES (?, ?, ?, ?)",
            (utc_now_iso(), level, component, message),
        )
        conn.commit()
    except sqlite3.Error:
        _log_stderr(f"[events_db] system_logs unavailable: {message}")


# ── Time filter helpers (Asia/Jakarta) ───────────────────────────────────────

def _parse_local(value: str, *, end_of_day: bool) -> _dt.datetime:
    """Parse 'YYYY-MM-DD' or ISO datetime as Asia/Jakarta local time."""
    v = value.strip()
    try:
        dt = _dt.datetime.fromisoformat(v)
    except ValueError:
        dt = _dt.datetime.strptime(v[:10], "%Y-%m-%d")
    tz = _dt.timezone(_dt.timedelta(seconds=_JAKARTA_OFFSET_S))
    if dt.tzinfo is None:
        if len(v) <= 10 and end_of_day:                       # plain date
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999_999)
        dt = dt.replace(tzinfo=tz)
    return dt


def _jakarta_range_to_epoch(date_from: str | None, date_to: str | None) -> tuple[float | None, float | None]:
    """Convert Jakarta-local date filters to UTC epoch bounds.

    Midnight-crossing ranges are handled by plain epoch comparison.
    """
    lo = hi = None
    if date_from:
        lo = _parse_local(date_from, end_of_day=False).timestamp()
    if date_to:
        hi = _parse_local(date_to, end_of_day=True).timestamp()
    return lo, hi


def _event_filters(
    camera_id: str | None,
    vehicle_type: str | None,
    session_id: str | None,
    date_from: str | None,
    date_to: str | None,
) -> str:
    clauses, params = [], []
    if camera_id:
        clauses.append("camera_id = ?"); params.append(camera_id)
    if vehicle_type:
        clauses.append("vehicle_type = ?"); params.append(vehicle_type)
    if session_id:
        clauses.append("session_id = ?"); params.append(session_id)
    lo, hi = _jakarta_range_to_epoch(date_from, date_to)
    if lo is not None:
        clauses.append("timestamp_epoch >= ?"); params.append(lo)
    if hi is not None:
        clauses.append("timestamp_epoch <= ?"); params.append(hi)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ── Queries ──────────────────────────────────────────────────────────────────

def query_events(
    *,
    camera_id: str | None = None,
    vehicle_type: str | None = None,
    session_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Paginated, filterable event history (newest first). Never loads the
    whole table — SQL LIMIT/OFFSET only."""
    where, params = _event_filters(camera_id, vehicle_type, session_id, date_from, date_to)
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM vehicle_events" + where, params).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM vehicle_events" + where +
        " ORDER BY timestamp_epoch DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


def history_summary(
    *,
    camera_id: str | None = None,
    vehicle_type: str | None = None,
    session_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Cumulative totals + hourly breakdown, computed from persisted events.

    Hours are bucketed in Asia/Jakarta local time (UTC+7); midnight-crossing
    ranges are correct because everything compares on UTC epoch.
    Aggregate SQL only — no per-event rows in memory.
    """
    where, params = _event_filters(camera_id, vehicle_type, session_id, date_from, date_to)
    conn = _get_conn()
    totals = conn.execute(
        """SELECT COUNT(*) AS total_events,
                  MIN(timestamp_epoch) AS first_ts,
                  MAX(timestamp_epoch) AS last_ts
           FROM vehicle_events""" + where, params).fetchone()
    per_class_rows = conn.execute(
        "SELECT vehicle_type, COUNT(*) AS n FROM vehicle_events" + where +
        " GROUP BY vehicle_type", params).fetchall()
    per_line_rows = conn.execute(
        "SELECT counting_line_id, COUNT(*) AS n FROM vehicle_events" + where +
        " GROUP BY counting_line_id", params).fetchall()
    hourly_rows = conn.execute(
        """SELECT strftime('%Y-%m-%dT%H:00:00',
               datetime(timestamp_epoch, 'unixepoch', '+7 hours')) AS hour,
               COUNT(*) AS n
           FROM vehicle_events""" + where + " GROUP BY hour ORDER BY hour", params).fetchall()
    hourly_class_rows = conn.execute(
        """SELECT strftime('%Y-%m-%dT%H:00:00',
               datetime(timestamp_epoch, 'unixepoch', '+7 hours')) AS hour,
               vehicle_type, COUNT(*) AS n
           FROM vehicle_events""" + where + " GROUP BY hour, vehicle_type", params).fetchall()

    count_per_class = {r["vehicle_type"]: r["n"] for r in per_class_rows}
    count_per_line = {r["counting_line_id"]: r["n"] for r in per_line_rows}

    hourly: list[dict] = [{"hour": r["hour"], "total": r["n"], "count_per_class": {}} for r in hourly_rows]
    by_hour = {h["hour"]: h for h in hourly}
    for r in hourly_class_rows:
        if r["hour"] in by_hour:
            by_hour[r["hour"]]["count_per_class"][r["vehicle_type"]] = r["n"]

    peak = max(hourly, key=lambda h: h["total"]) if hourly else None

    def _iso_ts(ts: float | None) -> str | None:
        if ts is None:
            return None
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()

    return {
        "total_events": totals["total_events"] or 0,
        "count_per_class": count_per_class,
        "count_per_line": count_per_line,
        "hourly": hourly,
        "peak_hour": peak["hour"] if peak else None,
        "peak_count": peak["total"] if peak else 0,
        "first_event_utc": _iso_ts(totals["first_ts"]),
        "last_event_utc": _iso_ts(totals["last_ts"]),
    }


def backup(path: str | Path | None = None) -> Path:
    """Safe online backup via VACUUM INTO (WAL-safe, non-destructive)."""
    if path is None:
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = DB_PATH.parent / f"backup-roadseye-{stamp}.db"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    conn.execute("VACUUM INTO ?", (str(path),))
    return path.resolve()
