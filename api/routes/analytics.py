"""REST endpoints for historical analytics queries.

All heavy reads go to SQLite (hourly aggregates).
Recent real-time data comes from the in-memory ring buffer.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Query, status

from api.database import fetch_hourly_range, fetch_summary, ring_recent
from api.schemas import (
    AnalyticsSummaryResponse,
    HourlyAggregate,
    RecentMetricsResponse,
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
