# Project Description & Specifications: sez_server

## 1. Project Overview
- **Project Name:** `sez_server`
- **Working Directory:** `C:\Users\sajjad\Desktop\sez_server`
- **Purpose:** High-performance geospatial elevation service that streams Cloud Optimized GeoTIFFs (COGs) of requested geographic polygons using on-the-fly spatial windowing from the AWS Open Data Copernicus DEM GLO-30 dataset.

---

## 2. Environment & Tooling Specifications
- **Python Version:** Python 3.13 (3.13.2 64-bit)
- **Virtual Environment:** `.venv` (created via `py -3.13 -m venv .venv`)
- **NumPy Specification:** NumPy 2.x (`numpy==2.5.2` installed)
- **Geospatial Engine:** Rasterio (`rasterio==1.5.1`) with GDAL COG reading and writing
- **Web Framework:** FastAPI (`0.141.1`) with Uvicorn (`0.52.4`)
- **Testing:** Pytest (`pytest==9.1.1`)

---

## 3. Project Architecture & Algorithm Design

### A. Targeted Spatial Windowing (Minimal Data Transfer)
Instead of downloading complete 1° × 1° DEM tiles (~70MB-100MB each), the server uses GDAL's virtual filesystem (`/vsicurl/`) and HTTP Range Requests (`rasterio.windows.from_bounds()`). Only the byte ranges of the internal COG blocks intersecting the target area are fetched over the network.

### B. 4-Pixel Halo Expansion (Real Terrain)
To facilitate edge-distortion-free client-side reprojection, warping, and upscaling:
1. The server receives 4 polygon nodes.
2. Derives the envelope: $[lon_{min}, lat_{min}, lon_{max}, lat_{max}]$.
3. Expands the envelope outward by 4 DEM pixels ($\approx 4 \times 0.00027778^\circ \approx 123\,\text{m}$):
   $$\text{expansion} = 4 \times \frac{1^\circ}{3600}$$
4. The requested window retrieves genuine real-world elevation data for this extra border margin.
5. Clients can perform convolution/spline warping right up to the edge and optionally crop off the 4-pixel perimeter.

### C. Multi-Tile Spanning & Seamless Mosaicing
If the requested polygon crosses integer degree lines (e.g. crossing latitude $45.0^\circ$ or longitude $6.0^\circ$), the server:
1. Calculates all intersecting 1° × 1° tiles (e.g., `N44_E006`, `N45_E006`).
2. Slices each sub-window and mosaics them directly into a unified Float32 canvas.
3. Automatically sets non-existent tiles (e.g., open sea / ocean areas not present in the Copernicus DEM) to `0.0` meters elevation.

### D. Zero-Delay Ocean Pre-flight Cache
Non-existent ocean tiles return HTTP 404. To prevent GDAL retry loops on ocean tiles, a fast pre-flight check (`check_tile_exists_on_s3`) probes the tile via HTTP HEAD and caches the result with an LRU cache.

### E. Cloud Optimized GeoTIFF (COG) Output
The resulting array is encoded in-memory as a tiled, single-band GeoTIFF with **Deflate** compression and **floating-point predictor (predictor=3)**, and streamed as `image/tiff`.

---

## 4. Directory Structure
```
C:\Users\sajjad\Desktop\sez_server\
├── .venv\                  # Python 3.13 Virtual Environment
├── D.md                    # Project description and architectural details (this file)
├── CLIENT_GUIDE.md         # Guide for connecting clients (Python, JS, cURL)
├── requirements.txt        # Pinned dependencies
├── models.py               # Pydantic models (Coordinate, DEMPolygonRequest, etc.)
├── dem_engine.py           # Core Copernicus DEM windowing and mosaicing engine
├── main.py                 # FastAPI server application
└── test_server.py          # Pytest test suite
```

---

## 5. Copernicus DEM GLO-30 Specifications
- **Data Source:** AWS Open Data Registry S3 bucket `s3://copernicus-dem-30m`
- **HTTP Base URL:** `https://copernicus-dem-30m.s3.amazonaws.com`
- **Tile Folder & File Pattern:**
  `Copernicus_DSM_COG_10_{N|S}{lat:02d}_00_{E|W}{lon:03d}_00_DEM/Copernicus_DSM_COG_10_{N|S}{lat:02d}_00_{E|W}{lon:03d}_00_DEM.tif`
- **Pixel Spacing:** $1/3600^\circ \approx 0.0002777777777778^\circ$ ($\approx 30.9\,\text{m}$ at the equator).
- **Tile Dimensions:** $3600 \times 3600$ pixels per $1^\circ \times 1^\circ$ tile.
- **Coordinate Reference System:** WGS 84 (`EPSG:4326`).
- **Data Type:** 32-bit Floating Point (`Float32`), elevations in meters relative to EGM2008 geoid.
- **Ocean / Sea Policy:** Open water bodies do not have files in Copernicus DEM. When a tile is absent (HTTP 404), the server assigns `0.0` meters elevation to the affected grid cells.

---

## 6. API Endpoints Reference

### 1. `GET /health`
Returns operational status and capabilities.
- **Response Format:** JSON
```json
{
  "status": "healthy",
  "service": "sez_server",
  "dem_dataset": "Copernicus DEM GLO-30 (30m)",
  "halo_pixels": 4,
  "supported_response_formats": ["image/tiff (COG)", "application/json"]
}
```

### 2. `POST /api/v1/dem/inspect`
Inspects the polygon and halo bounding box, list of queried tiles, raster dimensions, and elevation range without returning raster data.
- **Request Format:** JSON
```json
{
  "nodes": [
    {"lat": 45.830, "lon": 6.860},
    {"lat": 45.833, "lon": 6.860},
    {"lat": 45.833, "lon": 6.864},
    {"lat": 45.830, "lon": 6.864}
  ]
}
```
- **Response Format:** JSON (`DEMMetadataResponse`)
```json
{
  "requested_envelope": {
    "min_lon": 6.86,
    "min_lat": 45.83,
    "max_lon": 6.864,
    "max_lat": 45.833
  },
  "halo_envelope": {
    "min_lon": 6.858888888888889,
    "min_lat": 45.82888888888889,
    "max_lon": 6.865277777777778,
    "max_lat": 45.83416666666667
  },
  "halo_pixel_margin": 4,
  "raster_width": 23,
  "raster_height": 19,
  "min_elevation_m": 4212.95,
  "max_elevation_m": 4810.72,
  "tiles_queried": ["Copernicus_DSM_COG_10_N45_00_E006_00_DEM"],
  "crs": "EPSG:4326",
  "resolution_deg": 0.0002777777777777778
}
```

### 3. `POST /api/v1/dem/crop`
Performs spatial windowing, adds the 4-pixel halo, and streams back the Cloud Optimized GeoTIFF file.
- **Request Format:** JSON with 4 nodes (same as inspect).
- **Response Media Type:** `image/tiff`
- **Response Headers:**
  - `Content-Disposition`: `inline; filename="dem_halo_crop.tif"`
  - `X-DEM-Width`: Raster width in pixels (including 4-pixel margins on both sides).
  - `X-DEM-Height`: Raster height in pixels (including 4-pixel margins on both sides).
  - `X-DEM-Min-Elevation`: Minimum elevation in meters.
  - `X-DEM-Max-Elevation`: Maximum elevation in meters.
  - `X-DEM-Halo-Pixels`: Number of halo pixels added (`4`).
  - `X-DEM-Tiles-Queried`: Comma-separated list of 1° tiles queried.

---

## 7. Running, Testing & Verification

```powershell
# Run the automated test suite
.\.venv\Scripts\pytest -v test_server.py

# Start the server with Uvicorn (port 8000, auto-reload)
.\.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- **Swagger Documentation:** Available at `http://localhost:8000/docs`
- **Client Integration Guide:** See [`CLIENT_GUIDE.md`](./CLIENT_GUIDE.md) for Python, JavaScript, Browser/React, and cURL snippets.


