"""
dem_engine.py
=============
High-performance Copernicus & Global DEM extraction engine supporting both
OpenTopography API and direct AWS Open Data S3 windowing.

Features:
- Takes 4 polygon nodes, derives exact bounding box envelope.
- Expands the bounding box outward by a 4-pixel halo in all 4 cardinal directions.
- Dual Provider Architecture:
  1. OpenTopography Global DEM API (COP30, COP90, SRTMGL1, AW3D30, NASADEM) using API key from .env.
  2. Direct AWS S3 COG streaming (copernicus-dem-30m) using GDAL HTTP range requests with zero unnecessary data transfer.
- Mosaics multiple 1° x 1° tiles seamlessly if the envelope crosses integer boundaries.
- Automatically sets ocean/water areas (where DEM tiles do not exist) to 0.0 meters.
- Generates standard Cloud Optimized GeoTIFF (COG) bytes with Deflate compression (predictor=3).
"""

import os
import math
import io
import logging
from typing import List, Tuple, Dict, Any, Optional
import urllib.request
import urllib.parse
import urllib.error
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from dotenv import load_dotenv

from models import Coordinate, BoundingBox

# Load environment variables safely from .env if present
load_dotenv()

logger = logging.getLogger("sez_server.dem_engine")

# Configuration & Secrets (loaded from .env)
OPENTOPOGRAPHY_API_KEY = os.getenv("OPENTOPOGRAPHY_API_KEY", "").strip()
OPENTOPOGRAPHY_BASE_URL = os.getenv("OPENTOPOGRAPHY_BASE_URL", "https://portal.opentopography.org/API/globaldem").strip()
DEFAULT_DEM_PROVIDER = os.getenv("DEM_PROVIDER", "opentopography" if OPENTOPOGRAPHY_API_KEY else "aws_s3").strip().lower()
DEFAULT_DEM_TYPE = os.getenv("DEFAULT_DEM_TYPE", "COP30").strip()

# Constants for Copernicus DEM GLO-30 (30m resolution)
PIXEL_SIZE_DEG = 1.0 / 3600.0  # Approx 0.0002777777777777778 degrees (~30.9 meters at equator)
AWS_BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
HALO_PIXELS = 4


@lru_cache(maxsize=20000)
def check_tile_exists_on_s3(tile_id: str) -> bool:
    """
    Fast pre-flight check using HTTP HEAD to determine if tile exists on AWS S3.
    This avoids GDAL's multi-step retry delays when hitting ocean/water areas (404).
    Results are cached in memory for zero-latency repeat lookups.
    """
    tile_url = f"{AWS_BASE_URL}/{tile_id}/{tile_id}.tif"
    req = urllib.request.Request(tile_url, method="HEAD", headers={"User-Agent": "sez_server/1.2.0"})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def format_tile_id(lat_floor: int, lon_floor: int) -> str:
    """
    Builds the official Copernicus DEM GLO-30 tile folder name.
    
    Examples:
        lat=45, lon=6   -> Copernicus_DSM_COG_10_N45_00_E006_00_DEM
        lat=-1, lon=-50 -> Copernicus_DSM_COG_10_S01_00_W050_00_DEM
    """
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    lat_abs = abs(lat_floor)
    lon_abs = abs(lon_floor)
    return f"Copernicus_DSM_COG_10_{ns}{lat_abs:02d}_00_{ew}{lon_abs:03d}_00_DEM"


def calculate_polygon_envelope(nodes: List[Coordinate]) -> BoundingBox:
    """
    Derives the minimum bounding box containing all 4 polygon nodes.
    """
    lats = [n.lat for n in nodes]
    lons = [n.lon for n in nodes]
    return BoundingBox(
        min_lon=min(lons),
        min_lat=min(lats),
        max_lon=max(lons),
        max_lat=max(lats),
    )


def expand_with_halo(envelope: BoundingBox, halo_pixels: int = HALO_PIXELS) -> BoundingBox:
    """
    Expands the bounding box outward by `halo_pixels` in geographic space.
    This guarantees that genuine surrounding terrain data is fetched for edge warping.
    """
    expansion = halo_pixels * PIXEL_SIZE_DEG
    return BoundingBox(
        min_lon=max(-180.0, envelope.min_lon - expansion),
        min_lat=max(-90.0, envelope.min_lat - expansion),
        max_lon=min(180.0, envelope.max_lon + expansion),
        max_lat=min(90.0, envelope.max_lat + expansion),
    )


def align_to_grid(bbox: BoundingBox) -> Tuple[BoundingBox, int, int, Any]:
    """
    Snaps bounds to the global Copernicus DEM 3600-px/deg grid.
    Returns:
        snapped_bbox: Snapped BoundingBox
        width: Number of columns
        height: Number of rows
        transform: Affine transform for the output raster
    """
    col_min = math.floor(bbox.min_lon / PIXEL_SIZE_DEG)
    col_max = math.ceil(bbox.max_lon / PIXEL_SIZE_DEG)
    row_min = math.floor(bbox.min_lat / PIXEL_SIZE_DEG)
    row_max = math.ceil(bbox.max_lat / PIXEL_SIZE_DEG)

    snapped_min_lon = col_min * PIXEL_SIZE_DEG
    snapped_max_lon = col_max * PIXEL_SIZE_DEG
    snapped_min_lat = row_min * PIXEL_SIZE_DEG
    snapped_max_lat = row_max * PIXEL_SIZE_DEG

    width = col_max - col_min
    height = row_max - row_min

    # Affine transform: top-left corner is (snapped_min_lon, snapped_max_lat)
    transform = from_origin(snapped_min_lon, snapped_max_lat, PIXEL_SIZE_DEG, PIXEL_SIZE_DEG)

    return (
        BoundingBox(
            min_lon=snapped_min_lon,
            min_lat=snapped_min_lat,
            max_lon=snapped_max_lon,
            max_lat=snapped_max_lat,
        ),
        width,
        height,
        transform,
    )


def get_intersecting_tiles(bbox: BoundingBox) -> List[Tuple[int, int, str]]:
    """
    Finds all 1x1 degree tiles that intersect with the bounding box.
    Returns list of (lat_floor, lon_floor, tile_id).
    """
    min_lat_floor = math.floor(bbox.min_lat)
    max_lat_floor = math.floor(bbox.max_lat)
    if bbox.max_lat == max_lat_floor and max_lat_floor > min_lat_floor:
        max_lat_floor -= 1

    min_lon_floor = math.floor(bbox.min_lon)
    max_lon_floor = math.floor(bbox.max_lon)
    if bbox.max_lon == max_lon_floor and max_lon_floor > min_lon_floor:
        max_lon_floor -= 1

    tiles = []
    for lat_f in range(min_lat_floor, max_lat_floor + 1):
        for lon_f in range(min_lon_floor, max_lon_floor + 1):
            tile_id = format_tile_id(lat_f, lon_f)
            tiles.append((lat_f, lon_f, tile_id))
    return tiles


def extract_dem_raster_aws_s3(
    nodes: List[Coordinate],
    halo_pixels: int = HALO_PIXELS,
) -> Tuple[np.ndarray, Any, Dict[str, Any]]:
    """
    Extracts elevation data directly from AWS Open Data Copernicus DEM GLO-30 COGs
    using spatial windowing and HTTP range requests.
    """
    orig_bbox = calculate_polygon_envelope(nodes)
    halo_bbox = expand_with_halo(orig_bbox, halo_pixels=halo_pixels)
    grid_bbox, width, height, transform = align_to_grid(halo_bbox)

    tiles_info = get_intersecting_tiles(grid_bbox)
    tiles_queried = [t[2] for t in tiles_info]

    # Initialize canvas with 0.0m (ocean / nodata default)
    canvas = np.zeros((height, width), dtype=np.float32)

    # GDAL configuration for optimal AWS Open Data COG streaming
    gdal_env = rasterio.Env(
        AWS_NO_SIGN_REQUEST="YES",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_TIMEOUT="10",
    )

    with gdal_env:
        for lat_f, lon_f, tile_id in tiles_info:
            tile_min_lon = float(lon_f)
            tile_max_lon = float(lon_f + 1)
            tile_min_lat = float(lat_f)
            tile_max_lat = float(lat_f + 1)

            # Intersection between tile and request grid_bbox
            inter_min_lon = max(grid_bbox.min_lon, tile_min_lon)
            inter_max_lon = min(grid_bbox.max_lon, tile_max_lon)
            inter_min_lat = max(grid_bbox.min_lat, tile_min_lat)
            inter_max_lat = min(grid_bbox.max_lat, tile_max_lat)

            if inter_min_lon >= inter_max_lon or inter_min_lat >= inter_max_lat:
                continue

            # Calculate destination slice in canvas
            dest_col_start = int(round((inter_min_lon - grid_bbox.min_lon) / PIXEL_SIZE_DEG))
            dest_col_end = int(round((inter_max_lon - grid_bbox.min_lon) / PIXEL_SIZE_DEG))
            dest_row_start = int(round((grid_bbox.max_lat - inter_max_lat) / PIXEL_SIZE_DEG))
            dest_row_end = int(round((grid_bbox.max_lat - inter_min_lat) / PIXEL_SIZE_DEG))

            req_w = dest_col_end - dest_col_start
            req_h = dest_row_end - dest_row_start
            if req_w <= 0 or req_h <= 0:
                continue

            if not check_tile_exists_on_s3(tile_id):
                # Ocean tile absent on S3: default to 0.0m
                continue

            tile_url = f"{AWS_BASE_URL}/{tile_id}/{tile_id}.tif"

            try:
                with rasterio.open(tile_url) as src:
                    win = from_bounds(
                        inter_min_lon,
                        inter_min_lat,
                        inter_max_lon,
                        inter_max_lat,
                        transform=src.transform,
                    )
                    data = src.read(1, window=win, out_shape=(req_h, req_w), resampling=Resampling.nearest)
                    if src.nodata is not None:
                        data = np.where(data == src.nodata, 0.0, data)
                    canvas[dest_row_start:dest_row_end, dest_col_start:dest_col_end] = data.astype(np.float32)
            except Exception as e:
                logger.warning(f"Error reading AWS tile {tile_id}: {e}")

    min_elev = float(np.nanmin(canvas))
    max_elev = float(np.nanmax(canvas))

    meta = {
        "requested_envelope": orig_bbox.model_dump(),
        "halo_envelope": grid_bbox.model_dump(),
        "halo_pixel_margin": halo_pixels,
        "raster_width": width,
        "raster_height": height,
        "min_elevation_m": min_elev,
        "max_elevation_m": max_elev,
        "tiles_queried": tiles_queried,
        "crs": "EPSG:4326",
        "resolution_deg": PIXEL_SIZE_DEG,
        "provider": "aws_s3",
        "dem_type": "COP30",
    }

    return canvas, transform, meta


def extract_dem_raster_opentopography(
    nodes: List[Coordinate],
    dem_type: str = "COP30",
    halo_pixels: int = HALO_PIXELS,
    api_key: Optional[str] = None,
) -> Tuple[np.ndarray, Any, Dict[str, Any]]:
    """
    Extracts elevation data from OpenTopography Global DEM API (COP30, COP90, SRTMGL1, AW3D30, etc.)
    with automatic 4-pixel halo expansion.
    """
    key = (api_key or OPENTOPOGRAPHY_API_KEY).strip()
    if not key:
        raise ValueError(
            "OpenTopography API key is missing. Set OPENTOPOGRAPHY_API_KEY in .env or provide an API key."
        )

    orig_bbox = calculate_polygon_envelope(nodes)
    halo_bbox = expand_with_halo(orig_bbox, halo_pixels=halo_pixels)
    grid_bbox, width, height, transform = align_to_grid(halo_bbox)

    params = {
        "demtype": dem_type,
        "south": halo_bbox.min_lat,
        "north": halo_bbox.max_lat,
        "west": halo_bbox.min_lon,
        "east": halo_bbox.max_lon,
        "outputFormat": "GTiff",
        "API_Key": key,
    }

    query_str = urllib.parse.urlencode(params)
    url = f"{OPENTOPOGRAPHY_BASE_URL}?{query_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "sez_server/1.2.0"})

    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            status_code = resp.status
            content = resp.read()
    except urllib.error.HTTPError as e:
        status_code = e.code
        content = e.read()
    except Exception as e:
        raise RuntimeError(f"OpenTopography connection error: {str(e)}")

    # 204 No Content -> Ocean / Open Water with no DEM data
    if status_code == 204:
        canvas = np.zeros((height, width), dtype=np.float32)
        meta = {
            "requested_envelope": orig_bbox.model_dump(),
            "halo_envelope": grid_bbox.model_dump(),
            "halo_pixel_margin": halo_pixels,
            "raster_width": width,
            "raster_height": height,
            "min_elevation_m": 0.0,
            "max_elevation_m": 0.0,
            "tiles_queried": [f"OpenTopography_{dem_type}_Ocean"],
            "crs": "EPSG:4326",
            "resolution_deg": PIXEL_SIZE_DEG,
            "provider": "opentopography",
            "dem_type": dem_type,
        }
        return canvas, transform, meta

    if status_code != 200:
        error_msg = content.decode("utf-8", errors="ignore")[:300].strip()
        raise RuntimeError(f"OpenTopography API error (HTTP {status_code}): {error_msg}")

    # Read returned GeoTIFF in memory
    with rasterio.open(io.BytesIO(content)) as src:
        data = src.read(1).astype(np.float32)
        resp_transform = src.transform
        if src.nodata is not None:
            data = np.where(data == src.nodata, 0.0, data)
        data = np.nan_to_num(data, nan=0.0)

        min_elev = float(np.nanmin(data))
        max_elev = float(np.nanmax(data))
        r_height, r_width = data.shape

        meta = {
            "requested_envelope": orig_bbox.model_dump(),
            "halo_envelope": BoundingBox(
                min_lon=src.bounds.left,
                min_lat=src.bounds.bottom,
                max_lon=src.bounds.right,
                max_lat=src.bounds.top,
            ).model_dump(),
            "halo_pixel_margin": halo_pixels,
            "raster_width": r_width,
            "raster_height": r_height,
            "min_elevation_m": min_elev,
            "max_elevation_m": max_elev,
            "tiles_queried": [f"OpenTopography_{dem_type}"],
            "crs": str(src.crs or "EPSG:4326"),
            "resolution_deg": abs(src.transform.a) if hasattr(src.transform, "a") else PIXEL_SIZE_DEG,
            "provider": "opentopography",
            "dem_type": dem_type,
        }
        return data, resp_transform, meta


def extract_dem_raster(
    nodes: List[Coordinate],
    dem_type: Optional[str] = None,
    provider: Optional[str] = None,
    halo_pixels: int = HALO_PIXELS,
) -> Tuple[np.ndarray, Any, Dict[str, Any]]:
    """
    Main extraction entry point. Dispatches to OpenTopography or AWS S3 based on
    configuration, with automatic fallback for COP30 if OpenTopography fails.
    """
    chosen_provider = (provider or DEFAULT_DEM_PROVIDER).strip().lower()
    chosen_dem_type = (dem_type or DEFAULT_DEM_TYPE).strip()

    if chosen_provider == "opentopography":
        try:
            return extract_dem_raster_opentopography(
                nodes=nodes,
                dem_type=chosen_dem_type,
                halo_pixels=halo_pixels,
            )
        except Exception as e:
            # Fallback to direct AWS S3 Copernicus GLO-30 if applicable
            if chosen_dem_type.upper() == "COP30":
                logger.warning(
                    f"OpenTopography extraction failed ({e}). Falling back to direct AWS S3 Copernicus GLO-30."
                )
                return extract_dem_raster_aws_s3(nodes=nodes, halo_pixels=halo_pixels)
            raise e
    else:
        return extract_dem_raster_aws_s3(nodes=nodes, halo_pixels=halo_pixels)


def export_as_cog_bytes(raster_data: np.ndarray, transform: Any) -> bytes:
    """
    Serializes the 2D elevation array as an in-memory Cloud Optimized GeoTIFF (COG).
    Uses Deflate compression with predictor 3 for floating point elevation data.
    """
    height, width = raster_data.shape

    # Choose block sizes (standard 256 or 512, or dimensions if smaller)
    block_x = min(256, width)
    block_y = min(256, height)
    # Block sizes must be multiples of 16 for standard tiling if >= 16
    if block_x >= 16:
        block_x = (block_x // 16) * 16
    if block_y >= 16:
        block_y = (block_y // 16) * 16

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "nodata": None,
        "width": width,
        "height": height,
        "count": 1,
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
        "predictor": 3,
    }

    # If large enough, enable tiling for COG compliance
    if block_x >= 16 and block_y >= 16 and width >= 64 and height >= 64:
        profile["tiled"] = True
        profile["blockxsize"] = block_x
        profile["blockysize"] = block_y

    mem_buffer = io.BytesIO()
    with rasterio.open(mem_buffer, "w", **profile) as dst:
        dst.write(raster_data, 1)

    return mem_buffer.getvalue()
