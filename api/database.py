"""SQLite persistence layer using aiosqlite.

Stores hourly aggregates of traffic metrics.
In-memory ring buffer holds the last 5 minutes of per-frame metrics.
"""
from __future__ import annotations

import json
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from api.schemas import MetricsMessage, HourlyAggregate

# DB file location — overridable via DATABASE_PATH env var
import os as _os
DB_PATH = Path(_os.environ.get("DATABASE_PATH", str(Path(__file__).parent.parent / "data" / "traffic.db")))

# In-memory ring buffer: last 5 min at 25fps ≈ 7500 frames, cap at 10000
_RING_BUFFER_MAX = 10_000
_ring_buffer: deque[MetricsMessage] = deque(maxlen=_RING_BUFFER_MAX)


# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS hourly_aggregates (
    hour              TEXT PRIMARY KEY,   -- ISO-8601 hour: 2024-01-15T14:00:00
    total_count       INTEGER NOT NULL DEFAULT 0,
    count_per_class   TEXT    NOT NULL DEFAULT '{}',   -- JSON
    avg_speed_kmh     REAL    NOT NULL DEFAULT 0.0,
    peak_vehicles     INTEGER NOT NULL DEFAULT 0,
    alert_count       INTEGER NOT NULL DEFAULT 0,
    frame_count       INTEGER NOT NULL DEFAULT 0       -- for incremental avg
);
"""


# ── Connection ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    """Create tables if they don't exist. Called at app startup."""
    async with get_db() as db:
        await db.executescript(_CREATE_TABLES)
        await db.commit()


# ── Ring buffer ───────────────────────────────────────────────────────────────

def ring_push(metrics: MetricsMessage) -> None:
    """Append a metrics snapshot to the in-memory ring buffer."""
    _ring_buffer.append(metrics)


def ring_recent(window_seconds: float = 300.0) -> list[MetricsMessage]:
    """Return frames from the last `window_seconds` seconds."""
    cutoff = time.time() - window_seconds
    return [m for m in _ring_buffer if m.timestamp >= cutoff]


def ring_clear() -> None:
    _ring_buffer.clear()


# ── Hourly aggregation ────────────────────────────────────────────────────────

def _hour_key(ts: float) -> str:
    """Format timestamp as ISO-8601 hour string."""
    import datetime
    dt = datetime.datetime.utcfromtimestamp(ts).replace(minute=0, second=0, microsecond=0)
    return dt.isoformat()


async def upsert_hourly(metrics: MetricsMessage) -> None:
    """Incrementally update the hourly aggregate row for this frame's timestamp."""
    hour = _hour_key(metrics.timestamp)

    async with get_db() as db:
        row = await (await db.execute(
            "SELECT * FROM hourly_aggregates WHERE hour = ?", (hour,)
        )).fetchone()

        if row is None:
            count_per_class = json.dumps(metrics.count_per_class)
            await db.execute(
                """INSERT INTO hourly_aggregates
                   (hour, total_count, count_per_class, avg_speed_kmh,
                    peak_vehicles, alert_count, frame_count)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (
                    hour,
                    metrics.total_count,
                    count_per_class,
                    metrics.avg_speed_kmh,
                    metrics.vehicles_in_frame,
                    len(metrics.alerts),
                ),
            )
        else:
            # Incremental running average for speed
            n = row["frame_count"]
            new_avg = (row["avg_speed_kmh"] * n + metrics.avg_speed_kmh) / (n + 1)

            existing_cpc = json.loads(row["count_per_class"])
            for cls, cnt in metrics.count_per_class.items():
                existing_cpc[cls] = cnt   # count_per_class is cumulative, just overwrite

            await db.execute(
                """UPDATE hourly_aggregates SET
                   total_count     = ?,
                   count_per_class = ?,
                   avg_speed_kmh   = ?,
                   peak_vehicles   = MAX(peak_vehicles, ?),
                   alert_count     = alert_count + ?,
                   frame_count     = frame_count + 1
                   WHERE hour = ?""",
                (
                    metrics.total_count,
                    json.dumps(existing_cpc),
                    round(new_avg, 2),
                    metrics.vehicles_in_frame,
                    len(metrics.alerts),
                    hour,
                ),
            )

        await db.commit()


# ── Query helpers ─────────────────────────────────────────────────────────────

async def fetch_hourly_range(
    start_iso: str, end_iso: str
) -> list[HourlyAggregate]:
    async with get_db() as db:
        rows = await (await db.execute(
            """SELECT * FROM hourly_aggregates
               WHERE hour >= ? AND hour <= ?
               ORDER BY hour""",
            (start_iso, end_iso),
        )).fetchall()

    result = []
    for row in rows:
        result.append(HourlyAggregate(
            hour=row["hour"],
            total_count=row["total_count"],
            count_per_class=json.loads(row["count_per_class"]),
            avg_speed_kmh=row["avg_speed_kmh"],
            peak_vehicles=row["peak_vehicles"],
            alert_count=row["alert_count"],
        ))
    return result


async def fetch_summary(start_iso: str, end_iso: str) -> dict:
    async with get_db() as db:
        rows = await (await db.execute(
            """SELECT * FROM hourly_aggregates
               WHERE hour >= ? AND hour <= ?
               ORDER BY hour""",
            (start_iso, end_iso),
        )).fetchall()

    if not rows:
        return {}

    total_vehicles = 0
    count_per_class: dict[str, int] = {}
    speed_sum = 0.0
    frame_count_sum = 0
    peak_count = 0
    peak_hour = None
    total_alerts = 0

    for row in rows:
        total_vehicles = max(total_vehicles, row["total_count"])  # cumulative, take max
        cpc = json.loads(row["count_per_class"])
        for cls, cnt in cpc.items():
            count_per_class[cls] = max(count_per_class.get(cls, 0), cnt)
        speed_sum += row["avg_speed_kmh"] * row["frame_count"]
        frame_count_sum += row["frame_count"]
        total_alerts += row["alert_count"]
        if row["peak_vehicles"] > peak_count:
            peak_count = row["peak_vehicles"]
            peak_hour = row["hour"]

    avg_speed = round(speed_sum / frame_count_sum, 2) if frame_count_sum else 0.0

    return {
        "period_start": start_iso,
        "period_end": end_iso,
        "total_vehicles": total_vehicles,
        "count_per_class": count_per_class,
        "avg_speed_kmh": avg_speed,
        "peak_hour": peak_hour,
        "peak_count": peak_count,
        "total_alerts": total_alerts,
    }
