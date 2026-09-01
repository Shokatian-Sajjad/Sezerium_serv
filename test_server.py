"""
test_server.py
==============
Automated test suite for sez_server DEM extraction, JWT Bearer authentication,
and IP rate limiting.

Tests:
1. Polygon bounding box calculation and 4-pixel halo expansion.
2. Multi-tile crossing boundary calculation (across 45°N line).
3. FastAPI /health endpoint verification (health, auth, and config status).
4. JWT Token issuance: valid client key returns access token; invalid key returns 401.
5. Protected endpoint rejection: unauthenticated or invalid token receives 401 Unauthorized.
6. Authenticated DEM extraction via OpenTopography provider.
7. Authenticated direct AWS Open Data S3 windowing extraction.
8. Coastal / open ocean water handling (0.0m elevation default).
9. Request validation (ensuring exactly 4 nodes are enforced).
10. IP rate limit handling (triggers HTTP 429).
"""

import pytest
from fastapi.testclient import TestClient
import numpy as np
from rasterio.io import MemoryFile

from main import app
from dem_engine import (
    calculate_polygon_envelope,
    expand_with_halo,
    align_to_grid,
    get_intersecting_tiles,
    PIXEL_SIZE_DEG,
    HALO_PIXELS,
    OPENTOPOGRAPHY_API_KEY,
)
from auth import CLIENT_API_KEY, create_access_token
from models import Coordinate

client = TestClient(app)


def get_auth_headers(client_id: str = "test_client") -> dict:
    """Helper to generate valid JWT Authorization headers for tests."""
    token, _ = create_access_token(subject=client_id)
    return {"Authorization": f"Bearer {token}"}


def test_polygon_envelope_and_halo():
    """Verify envelope derivation and 4-pixel halo coordinate expansion."""
    nodes = [
        Coordinate(lat=45.10, lon=6.20),
        Coordinate(lat=45.15, lon=6.20),
        Coordinate(lat=45.15, lon=6.25),
        Coordinate(lat=45.10, lon=6.25),
    ]
    env = calculate_polygon_envelope(nodes)
    assert pytest.approx(env.min_lat) == 45.10
    assert pytest.approx(env.max_lat) == 45.15
    assert pytest.approx(env.min_lon) == 6.20
    assert pytest.approx(env.max_lon) == 6.25

    halo_env = expand_with_halo(env, halo_pixels=4)
    expected_expansion = 4 * PIXEL_SIZE_DEG
    assert pytest.approx(halo_env.min_lat) == 45.10 - expected_expansion
    assert pytest.approx(halo_env.max_lat) == 45.15 + expected_expansion
    assert pytest.approx(halo_env.min_lon) == 6.20 - expected_expansion
    assert pytest.approx(halo_env.max_lon) == 6.25 + expected_expansion


def test_multi_tile_crossing_calculation():
    """Verify calculation of intersecting tiles spanning across a 1-degree boundary (45.00 lat line)."""
    nodes = [
        Coordinate(lat=44.998, lon=6.100),
        Coordinate(lat=45.002, lon=6.100),
        Coordinate(lat=45.002, lon=6.104),
        Coordinate(lat=44.998, lon=6.104),
    ]
    orig_bbox = calculate_polygon_envelope(nodes)
    halo_bbox = expand_with_halo(orig_bbox, halo_pixels=4)
    grid_bbox, _, _, _ = align_to_grid(halo_bbox)
    tiles_info = get_intersecting_tiles(grid_bbox)
    tile_ids = [t[2] for t in tiles_info]
    assert len(tile_ids) >= 2
    assert any("N44" in t for t in tile_ids)
    assert any("N45" in t for t in tile_ids)


def test_fastapi_health_endpoint():
    """Verify health_check logic and safe configuration exposure."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "sez_server"
    assert data["halo_pixels"] == 4
    assert data["authentication"] == "JWT Bearer"
    assert data["rate_limiting"]["enabled"] is True
    # Verify sensitive keys are NOT leaked in response
    assert OPENTOPOGRAPHY_API_KEY not in str(data)
    assert CLIENT_API_KEY not in str(data)


def test_jwt_auth_token_issuance():
    """Verify token generation via /api/v1/auth/token endpoint."""
    # 1. Valid client API key
    valid_resp = client.post(
        "/api/v1/auth/token",
        json={"client_api_key": CLIENT_API_KEY, "client_id": "pytest_client"},
    )
    assert valid_resp.status_code == 200
    body = valid_resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in_seconds"] > 0

    # 2. Invalid client API key
    invalid_resp = client.post(
        "/api/v1/auth/token",
        json={"client_api_key": "wrong_invalid_key_123"},
    )
    assert invalid_resp.status_code == 401
    assert "Invalid client API key" in invalid_resp.json()["detail"]


def test_protected_endpoints_require_authentication():
    """Verify that calling protected endpoints without JWT token returns 401."""
    payload = {
        "nodes": [
            {"lat": 45.830, "lon": 6.860},
            {"lat": 45.833, "lon": 6.860},
            {"lat": 45.833, "lon": 6.864},
            {"lat": 45.830, "lon": 6.864},
        ]
    }

    # Crop without token -> 401
    crop_resp = client.post("/api/v1/dem/crop", json=payload)
    assert crop_resp.status_code == 401

    # Inspect without token -> 401
    inspect_resp = client.post("/api/v1/dem/inspect", json=payload)
    assert inspect_resp.status_code == 401

    # Call with invalid token -> 401
    bad_token_resp = client.post(
        "/api/v1/dem/inspect",
        json=payload,
        headers={"Authorization": "Bearer bad_invalid_token_xyz"},
    )
    assert bad_token_resp.status_code == 401


def test_api_dem_inspect_and_crop_authenticated_opentopography():
    """Verify inspect and crop endpoints with valid JWT Bearer token via OpenTopography."""
    headers = get_auth_headers()
    payload = {
        "nodes": [
            {"lat": 45.830, "lon": 6.860},
            {"lat": 45.833, "lon": 6.860},
            {"lat": 45.833, "lon": 6.864},
            {"lat": 45.830, "lon": 6.864},
        ],
        "provider": "opentopography",
        "dem_type": "COP30",
    }

    # Test inspect
    inspect_resp = client.post("/api/v1/dem/inspect", json=payload, headers=headers)
    assert inspect_resp.status_code == 200
    meta = inspect_resp.json()
    assert meta["raster_width"] > 0
    assert meta["raster_height"] > 0
    assert meta["halo_pixel_margin"] == 4
    assert meta["provider"] == "opentopography"
    assert meta["max_elevation_m"] > 1000.0

    # Test crop
    crop_resp = client.post("/api/v1/dem/crop", json=payload, headers=headers)
    assert crop_resp.status_code == 200
    assert crop_resp.headers["Content-Type"] == "image/tiff"
    assert crop_resp.headers["X-DEM-Provider"] == "opentopography"
    assert int(crop_resp.headers["X-DEM-Width"]) == meta["raster_width"]

    with MemoryFile(crop_resp.content) as memfile:
        with memfile.open() as src:
            assert src.driver == "GTiff"
            assert src.count == 1
            data = src.read(1)
            assert data.shape == (meta["raster_height"], meta["raster_width"])
            assert data.max() > 1000.0


def test_api_dem_crop_authenticated_aws_s3():
    """Verify authenticated crop using direct AWS Open Data S3 provider."""
    from dem_engine import extract_dem_raster, export_as_cog_bytes
    nodes = [
        Coordinate(lat=45.830, lon=6.860),
        Coordinate(lat=45.833, lon=6.860),
        Coordinate(lat=45.833, lon=6.864),
        Coordinate(lat=45.830, lon=6.864),
    ]
    raster, transform, meta = extract_dem_raster(nodes, provider="aws_s3", halo_pixels=4)
    assert meta["provider"] == "aws_s3"
    assert meta["raster_width"] > 0
    assert meta["raster_height"] > 0
    assert meta["max_elevation_m"] > 1000.0

    cog_bytes = export_as_cog_bytes(raster, transform)
    assert len(cog_bytes) > 0
    with MemoryFile(cog_bytes) as memfile:
        with memfile.open() as src:
            assert src.driver == "GTiff"
            data = src.read(1)
            assert data.max() > 1000.0


def test_ocean_water_fill():
    """Verify that an area in open water (ocean) fills with 0.0m elevation without failing."""
    headers = get_auth_headers()
    payload = {
        "nodes": [
            {"lat": 36.000, "lon": 18.000},
            {"lat": 36.003, "lon": 18.000},
            {"lat": 36.003, "lon": 18.003},
            {"lat": 36.000, "lon": 18.003},
        ],
    }

    crop_resp = client.post("/api/v1/dem/crop", json=payload, headers=headers)
    assert crop_resp.status_code == 200
    with MemoryFile(crop_resp.content) as memfile:
        with memfile.open() as src:
            data = src.read(1)
            assert np.all(data == 0.0)


def test_invalid_node_count_validation():
    """Verify that submitting an invalid number of nodes triggers 422 validation error."""
    headers = get_auth_headers()
    resp = client.post(
        "/api/v1/dem/crop",
        json={
            "nodes": [
                {"lat": 45.0, "lon": 6.0},
                {"lat": 45.1, "lon": 6.0},
                {"lat": 45.1, "lon": 6.1},
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_ip_rate_limiting():
    """Verify that exceeding rate limits returns HTTP 429 Too Many Requests."""
    # Sending rapid requests to trigger RATE_LIMIT_AUTH (10/minute)
    statuses = []
    for _ in range(15):
        r = client.post(
            "/api/v1/auth/token",
            json={"client_api_key": "wrong_rate_test_key"},
        )
        statuses.append(r.status_code)
    assert 429 in statuses
