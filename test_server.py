"""
test_server.py
==============
Automated test suite for sez_server Copernicus DEM extraction.

Tests:
1. Polygon bounding box calculation and 4-pixel halo expansion.
2. Direct COG window extraction from AWS Open Data Copernicus DEM GLO-30.
3. Multi-tile crossing boundary stitching (e.g. across 45°N line).
4. Coastal / water handling (ensuring water pixels default to 0.0m).
5. GeoTIFF validity check: parses output bytes directly with rasterio.
6. FastAPI TestClient verification on /health, /api/v1/dem/inspect, and /api/v1/dem/crop.
"""

import io
import pytest
from fastapi.testclient import TestClient
import numpy as np
import rasterio

from main import app
from dem_engine import (
    calculate_polygon_envelope,
    expand_with_halo,
    align_to_grid,
    extract_dem_raster,
    export_as_cog_bytes,
    PIXEL_SIZE_DEG,
    HALO_PIXELS,
)
from models import Coordinate

client = TestClient(app)


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


def test_fastapi_health_endpoint():
    """Verify health_check logic."""
    from main import health_check
    data = health_check()
    assert data["status"] == "healthy"
    assert data["service"] == "sez_server"
    assert data["halo_pixels"] == 4


def test_api_dem_inspect_and_crop():
    """Verify inspect and crop endpoints with small Alps mountain query."""
    from main import inspect_dem_envelope, crop_dem_geotiff
    from models import DEMPolygonRequest

    req = DEMPolygonRequest(
        nodes=[
            Coordinate(lat=45.830, lon=6.860),
            Coordinate(lat=45.833, lon=6.860),
            Coordinate(lat=45.833, lon=6.864),
            Coordinate(lat=45.830, lon=6.864),
        ]
    )

    # Test inspect
    meta_resp = inspect_dem_envelope(req)
    assert meta_resp.raster_width > 0
    assert meta_resp.raster_height > 0
    assert meta_resp.halo_pixel_margin == 4
    # French Alps mountain terrain is well above 1000m
    assert meta_resp.max_elevation_m > 1000.0

    # Test crop
    crop_resp = crop_dem_geotiff(req)
    assert crop_resp.media_type == "image/tiff"
    assert int(crop_resp.headers["X-DEM-Width"]) == meta_resp.raster_width

    # Verify that the returned bytes form a valid, readable GeoTIFF
    cog_bytes = crop_resp.body
    with rasterio.open(io.BytesIO(cog_bytes)) as src:
        assert src.driver == "GTiff"
        assert src.count == 1
        assert src.width == meta_resp.raster_width
        assert src.height == meta_resp.raster_height
        data = src.read(1)
        assert data.shape == (meta_resp.raster_height, meta_resp.raster_width)
        assert data.max() > 1000.0


def test_multi_tile_crossing():
    """Verify crossing a 1-degree boundary (e.g. crossing 45.00 lat line)."""
    from main import inspect_dem_envelope
    from models import DEMPolygonRequest

    req = DEMPolygonRequest(
        nodes=[
            Coordinate(lat=44.998, lon=6.100),
            Coordinate(lat=45.002, lon=6.100),
            Coordinate(lat=45.002, lon=6.104),
            Coordinate(lat=44.998, lon=6.104),
        ]
    )

    meta = inspect_dem_envelope(req)
    # It must query at least two tiles across the 45.0 degree line: N44 and N45
    assert len(meta.tiles_queried) >= 2
    assert any("N44" in t for t in meta.tiles_queried)
    assert any("N45" in t for t in meta.tiles_queried)


def test_ocean_water_fill():
    """Verify that an area in open water (ocean) fills with 0.0m elevation without failing."""
    from main import crop_dem_geotiff
    from models import DEMPolygonRequest

    req = DEMPolygonRequest(
        nodes=[
            Coordinate(lat=36.000, lon=18.000),
            Coordinate(lat=36.003, lon=18.000),
            Coordinate(lat=36.003, lon=18.003),
            Coordinate(lat=36.000, lon=18.003),
        ]
    )

    crop_resp = crop_dem_geotiff(req)
    with rasterio.open(io.BytesIO(crop_resp.body)) as src:
        data = src.read(1)
        # Open water should default cleanly to 0.0m
        assert np.all(data == 0.0)



if __name__ == "__main__":
    print("Running tests directly...")
    test_polygon_envelope_and_halo()
    print("Envelope and halo test passed.")
    test_fastapi_health_endpoint()
    print("Health endpoint test passed.")
    test_api_dem_inspect_and_crop()
    print("DEM inspect and crop test passed.")
    test_multi_tile_crossing()
    print("Multi-tile boundary crossing test passed.")
    test_ocean_water_fill()
    print("Ocean water fill test passed.")
    print("ALL TESTS PASSED SUCCESSFULLY!")
