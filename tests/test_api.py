"""API integration tests — no ONNX model or GPU required.

Uses httpx AsyncClient with FastAPI's TestClient.
The pipeline is never actually started in these tests; we test the
REST scaffolding and schema validation only.

Run with:
    pytest tests/test_api.py -v
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Patch database to use a temp file so tests don't touch the real DB
import tempfile, api.database as _db_module
from pathlib import Path
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
_db_module.DB_PATH = Path(_tmp_db.name)

from api.main import app


@pytest_asyncio.fixture
async def client():
    from api.database import init_db
    await init_db()   # ensure tables exist before each test
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


# ── Root ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    r = await client.get("/")
    assert r.status_code == 200
    assert "TrafficVision" in r.json()["message"]


# ── Pipeline status (not running) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_status_not_running(client: AsyncClient):
    r = await client.get("/pipeline/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["frame_index"] == 0


# ── Pipeline start validation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_start_invalid_conf(client: AsyncClient):
    """confidence_threshold > 1.0 should be rejected by Pydantic."""
    r = await client.post("/pipeline/start", json={"source": "0", "confidence_threshold": 1.5})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_pipeline_start_invalid_jpeg_quality(client: AsyncClient):
    r = await client.post("/pipeline/start", json={"source": "0", "jpeg_quality": 0})
    assert r.status_code == 422


# ── Analytics: recent (empty ring buffer) ────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_recent_empty(client: AsyncClient):
    r = await client.get("/analytics/recent")
    assert r.status_code == 200
    body = r.json()
    assert body["frames"] == []
    assert body["window_seconds"] == 300


@pytest.mark.asyncio
async def test_analytics_recent_custom_window(client: AsyncClient):
    r = await client.get("/analytics/recent?window=60")
    assert r.status_code == 200
    assert r.json()["window_seconds"] == 60


@pytest.mark.asyncio
async def test_analytics_recent_window_too_small(client: AsyncClient):
    r = await client.get("/analytics/recent?window=5")
    assert r.status_code == 422


# ── Analytics: hourly (empty DB) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_hourly_empty(client: AsyncClient):
    r = await client.get("/analytics/hourly")
    assert r.status_code == 200
    assert r.json() == []


# ── Analytics: summary (no data) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_summary_no_data(client: AsyncClient):
    r = await client.get("/analytics/summary")
    assert r.status_code == 404


# ── Schemas: MetricsMessage round-trip ───────────────────────────────────────

def test_metrics_message_schema():
    from api.schemas import MetricsMessage
    import time
    payload = {
        "timestamp": time.time(),
        "frame_index": 42,
        "total_count": 10,
        "count_per_class": {"car": 8, "bus": 2},
        "vehicles_in_frame": 3,
        "avg_speed_kmh": 45.0,
        "speed_samples": [40.0, 45.0, 50.0],
        "alerts": [],
        "tracks": [
            {"id": 1, "bbox": [0.0, 0.0, 100.0, 100.0],
             "class_id": 0, "class_name": "car", "confidence": 0.9, "speed_kmh": None}
        ],
    }
    msg = MetricsMessage(**payload)
    assert msg.frame_index == 42
    assert msg.tracks[0].class_name == "car"
    # Round-trip via JSON
    restored = MetricsMessage.model_validate_json(msg.model_dump_json())
    assert restored.total_count == 10


def test_pipeline_start_request_coerces_source():
    from api.schemas import PipelineStartRequest
    req = PipelineStartRequest(source="2")
    assert req.source == 2   # int coercion


def test_counting_line_as_spec():
    from api.schemas import CountingLineConfig
    line = CountingLineConfig(x1=0, y1=100, x2=1280, y2=100)
    spec = line.as_spec()
    assert spec.p1 == (0, 100)
    assert spec.p2 == (1280, 100)
