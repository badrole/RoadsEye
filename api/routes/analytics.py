"""REST endpoints for historical analytics queries.

All heavy reads go to SQLite (hourly aggregates + persistent vehicle events).
Recent real-time data comes from the in-memory ring buffer.
"""
from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, HTTPException, Query, status

from api.database import fetch_hourly_range, fetch_summary, ring_recent
from api.events_db import backup, history_summary, list_sessions, query_events
from api.schemas import (
    AnalyticsSummaryResponse,
    BackupResponse,
    EventsPage,
    HistorySummaryResponse,
    HourlyAggregate,
    RecentMetricsResponse,
    ProcessingSessionRecord,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.datetime.utcnow().replace(minute=0, second=0, microsecond=0).isoformat()


def _iso_hours_ago(n: int) -> str:
    dt = datetime.datetime.utcnow() - datetime.timedelta(hours=n)
    return dt.replace(minute=0, second=0, microsecond=0).isoformat()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/recent", response_model=RecentMetricsResponse)
async def recent_metrics(
    window: int = Query(default=300, ge=10, le=3600, description="Window in seconds"),
) -> RecentMetricsResponse:
    """Return per-frame metrics from the last `window` seconds (ring buffer)."""
    frames = ring_recent(window_seconds=float(window))
    return RecentMetricsResponse(window_seconds=window, frames=frames)


@router.get("/hourly", response_model=list[HourlyAggregate])
async def hourly_data(
    hours: int = Query(default=24, ge=1, le=168, description="How many hours back to fetch"),
) -> list[HourlyAggregate]:
    """Return hourly aggregates for the last N hours."""
    end = _iso_now()
    start = _iso_hours_ago(hours)
    return await fetch_hourly_range(start, end)


@router.get("/summary", response_model=AnalyticsSummaryResponse)
async def summary(
    hours: int = Query(default=24, ge=1, le=168, description="Summarise last N hours"),
) -> AnalyticsSummaryResponse:
    """Return a summary of traffic over the last N hours."""
    end = _iso_now()
    start = _iso_hours_ago(hours)
    data = await fetch_summary(start, end)

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No data found for the requested period.",
        )

    return AnalyticsSummaryResponse(**data)


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_ring_buffer() -> None:
    """Clear the in-memory ring buffer (does not affect SQLite)."""
    from api.database import ring_clear
    ring_clear()


# ── Persistent event history (Phase 1) ───────────────────────────────────────

@router.get("/history/summary", response_model=HistorySummaryResponse)
async def historical_summary(
    camera_id: str | None = Query(default=None),
    vehicle_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="Jakarta-local date: YYYY-MM-DD or ISO"),
    date_to: str | None = Query(default=None, description="Jakarta-local date: YYYY-MM-DD or ISO"),
) -> HistorySummaryResponse:
    """Cumulative totals computed from persisted events — survives
    refreshes, detection stops, and backend restarts. Hours are bucketed in
    Asia/Jakarta local time. DB read runs in the threadpool."""
    data = await asyncio.to_thread(
        history_summary,
        camera_id=camera_id,
        vehicle_type=vehicle_type,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
    )
    return HistorySummaryResponse(**data)


@router.get("/events", response_model=EventsPage)
async def event_history(
    camera_id: str | None = Query(default=None),
    vehicle_type: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> EventsPage:
    """Paginated, filterable vehicle-event history (most recent first)."""
    data = await asyncio.to_thread(
        query_events,
        camera_id=camera_id,
        vehicle_type=vehicle_type,
        session_id=session_id,
        date_from=date_from,
        date_to=date_to,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return EventsPage(total=data["total"], page=page, page_size=page_size, items=data["items"])


@router.get("/sessions", response_model=list[ProcessingSessionRecord])
async def processing_sessions(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ProcessingSessionRecord]:
    """Processing sessions (started/stopped), most recent first."""
    rows = await asyncio.to_thread(list_sessions, limit=limit)
    return [ProcessingSessionRecord(**r) for r in rows]


@router.post("/history/backup", response_model=BackupResponse)
async def backup_history(
    path: str | None = Query(default=None, description="Optional absolute path for the backup file"),
) -> BackupResponse:
    """Create a safe online backup of the events database (VACUUM INTO — WAL-safe)."""
    try:
        resolved = await asyncio.to_thread(backup, path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")
    return BackupResponse(path=str(resolved))
