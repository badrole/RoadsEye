"""Pydantic v2 schemas for all API request/response types.

These are the wire-format models — separate from the internal dataclasses
in core/ so the API contract is explicit and independently versioned.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from core.analytics import CountingLineSpec


# ── WebSocket message schemas ─────────────────────────────────────────────────

class TrackData(BaseModel):
    """Single tracked vehicle, sent in metrics WebSocket messages."""
    id: int
    bbox: list[float]
    class_id: int
    class_name: str
    confidence: float
    speed_kmh: float | None = None


class MetricsMessage(BaseModel):
    """JSON payload pushed over /ws/metrics each processed frame."""
    timestamp: float
    frame_index: int
    # Counts
    total_count: int
    count_per_class: dict[str, int]
    count_per_line: dict[str, int] = Field(default_factory=dict)
    vehicles_in_frame: int
    # Speed
    avg_speed_kmh: float
    speed_samples: list[float]
    # Alerts
    alerts: list[str]
    # Tracks for frontend overlay
    tracks: list[TrackData]

    @classmethod
    def from_frame_metrics(cls, m: Any) -> "MetricsMessage":
        """Convert core.analytics.FrameMetrics → MetricsMessage."""
        return cls(
            timestamp=m.timestamp,
            frame_index=m.frame_index,
            total_count=m.total_count,
            count_per_class=m.count_per_class,
            count_per_line=m.count_per_line,
            vehicles_in_frame=m.vehicles_in_frame,
            avg_speed_kmh=m.avg_speed_kmh,
            speed_samples=m.speed_samples,
            alerts=m.alerts,
            tracks=[TrackData(**t) for t in m.tracks],
        )


# ── REST: pipeline config ─────────────────────────────────────────────────────

class CountingLineConfig(BaseModel):
    """A named counting line defined by two pixel-coordinate endpoints."""
    name: str = "line"
    x1: int
    y1: int
    x2: int
    y2: int

    def as_spec(self) -> "CountingLineSpec":
        from core.analytics import CountingLineSpec  # noqa: PLC0415
        return CountingLineSpec(
            name=self.name,
            p1=(self.x1, self.y1),
            p2=(self.x2, self.y2),
        )


class PipelineStartRequest(BaseModel):
    """POST /pipeline/start body."""
    source: str | int = Field(
        default=0,
        description="Video file path, webcam index, RTSP URL, or 'demo'",
    )
    confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    iou_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    skip_frames: int = Field(default=0, ge=0)
    counting_lines: list[CountingLineConfig] = Field(default_factory=list)
    pixels_per_meter: float = Field(default=10.0, gt=0)
    loop: bool = False
    draw_annotations: bool = True
    jpeg_quality: int = Field(default=80, ge=1, le=100)

    @field_validator("source", mode="before")
    @classmethod
    def coerce_source(cls, v: Any) -> str | int:
        if isinstance(v, str) and v.lower() != "demo":
            try:
                return int(v)
            except ValueError:
                pass
        return v


# ── REST: analytics history ───────────────────────────────────────────────────

class HourlyAggregate(BaseModel):
    hour: str
    total_count: int
    count_per_class: dict[str, int]
    avg_speed_kmh: float
    peak_vehicles: int
    alert_count: int


class AnalyticsSummaryResponse(BaseModel):
    period_start: str
    period_end: str
    total_vehicles: int
    count_per_class: dict[str, int]
    avg_speed_kmh: float
    peak_hour: str | None
    peak_count: int
    total_alerts: int


class RecentMetricsResponse(BaseModel):
    window_seconds: int
    frames: list[MetricsMessage]


# ── REST: pipeline status ─────────────────────────────────────────────────────

class CountingLineStatus(BaseModel):
    """Counting line info returned in pipeline status."""
    name: str
    x1: int
    y1: int
    x2: int
    y2: int


class PipelineStatus(BaseModel):
    running: bool
    source: str | int | None = None
    frame_index: int = 0
    uptime_seconds: float = 0.0
    fps: float = 0.0
    demo_mode: bool = False
    counting_lines: list[CountingLineStatus] = Field(default_factory=list)


# ── Health check ──────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    timestamp: float = Field(default_factory=time.time)
