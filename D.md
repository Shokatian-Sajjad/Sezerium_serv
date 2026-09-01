# Project Description & Specifications: sez_server

## 1. Project Overview
- **Project Name:** `sez_server`
- **Working Directory:** `C:\Users\sajjad\Desktop\sez_server`
- **Purpose:** High-performance geospatial elevation service that streams Cloud Optimized GeoTIFFs (COGs) of requested geographic polygons using on-the-fly spatial windowing from the **OpenTopography Global DEM API** and direct **AWS Open Data Copernicus DEM GLO-30** dataset.

---

## 2. Environment & Tooling Specifications
- **Python Version:** Python 3.13 (3.13.2 64-bit)
- **Virtual Environment:** `.venv` (created via `py -3.13 -m venv .venv`)
- **NumPy Specification:** NumPy 2.x (`numpy==2.5.2` installed)
- **Geospatial Engine:** Rasterio (`rasterio==1.5.1`) with GDAL COG reading and writing
- **Web Framework:** FastAPI (`0.141.1`) with Uvicorn (`0.52.4`)
- **Environment Management:** `python-dotenv==1.2.3`
- **HTTP Client:** `httpx==0.28.1`
- **Testing:** Pytest (`pytest==9.1.1`)

---

## 3. Project Architecture & Algorithm Design

### A. Dual Provider Architecture
1. **OpenTopography Global DEM API (`provider: "opentopography"`)**:
   - Queries OpenTopography Global Datasets API with authorized API key (`OPENTOPOGRAPHY_API_KEY` from `.env`).
   - Supports multiple global datasets: `COP30` (default), `COP90`, `SRTMGL1`, `AW3D30`, `NASADEM`.
   - Bounding boxes are queried with halo margin and returned as in-memory GeoTIFFs.
   - Handles ocean/water bodies returning HTTP 204 No Content by populating `0.0m` elevation arrays.
2. **Direct AWS S3 Streaming (`provider: "aws_s3"`)**:
   - Reads directly from `s3://copernicus-dem-30m` using GDAL's virtual filesystem (`/vsicurl/`) and HTTP Range Requests (`rasterio.windows.from_bounds()`).
   - Slices and mosaics 1° × 1° tiles seamlessly across degree boundaries.
3. **Automatic Fallback**:
   - If OpenTopography is the default provider and encounters a quota or network error while requesting `COP30`, it automatically falls back to direct AWS S3 streaming.

### B. 4-Pixel Halo Expansion (Real Terrain)
To facilitate edge-distortion-free client-side reprojection, warping, and upscaling:
1. The server receives 4 polygon nodes.
2. Derives the envelope: $[lon_{min}, lat_{min}, lon_{max}, lat_{max}]$.
3. Expands the envelope outward by 4 DEM pixels ($\approx 4 \times 0.00027778^\circ \approx 123\,\text{m}$):
   $$\text{expansion} = 4 \times \frac{1^\circ}{3600}$$
4. The requested window retrieves genuine real-world elevation data for this extra border margin.
5. Clients can perform convolution/spline warping right up to the edge and optionally crop off the 4-pixel perimeter.

### C. Multi-Tile Spanning & Seamless Mosaicing
If the requested polygon crosses integer degree lines (e.g. crossing latitude $45.0^\circ$ or longitude $6.0^\circ$):
1. Calculates all intersecting 1° × 1° tiles (e.g., `N44_E006`, `N45_E006`).
2. Slices each sub-window and mosaics them directly into a unified Float32 canvas.
3. Automatically sets non-existent tiles (e.g., open sea / ocean areas not present in the Copernicus DEM) to `0.0` meters elevation.

### D. Zero-Delay Ocean Pre-flight Cache (AWS Provider)
Non-existent ocean tiles return HTTP 404. To prevent GDAL retry loops on ocean tiles, a fast pre-flight check (`check_tile_exists_on_s3`) probes the tile via HTTP HEAD and caches the result with an LRU cache.

### E. Cloud Optimized GeoTIFF (COG) Output
The resulting array is encoded in-memory as a tiled, single-band GeoTIFF with **Deflate** compression and **floating-point predictor (predictor=3)**, and streamed as `image/tiff`.

---

## 4. Secret Management & Public Repository Security
- **Strict `.gitignore` Policy:** `.env`, `.env.*`, `*.env`, `*.local` are explicitly ignored by Git.
- **Template Provided:** `.env.example` is committed to the repository with placeholder values (`OPENTOPOGRAPHY_API_KEY=your_opentopography_api_key_here`).
- **Safe API Health Exposure:** The `/health` endpoint exposes a boolean status (`opentopography_configured: true`) without leaking the sensitive API key.

---

## 5. Directory Structure
```
C:\Users\sajjad\Desktop\sez_server\
├── .env                    # Local secrets (API Key) - NEVER COMMITTED
├── .env.example            # Public environment variable template
├── .gitignore              # Ignores .env, .venv, cache, and test rasters
├── .venv\                  # Python 3.13 Virtual Environment
├── D.md                    # Project description and architectural details (this file)
├── CLIENT_GUIDE.md         # Guide for connecting clients (Python, JS, cURL)
├── requirements.txt        # Pinned dependencies
├── models.py               # Pydantic models (Coordinate, DEMPolygonRequest, etc.)
├── dem_engine.py           # Core DEM windowing, OpenTopography & S3 engine
├── main.py                 # FastAPI server application
└── test_server.py          # Pytest automated test suite
```

---

## 6. API Endpoints Reference

### 1. `GET /health`
Returns operational status and capabilities.
- **Response Format:** JSON
```json
{
  "status": "healthy",
  "service": "sez_server",
  "active_provider": "opentopography",
  "default_dem_type": "COP30",
  "opentopography_configured": true,
  "halo_pixels": 4,
  "supported_response_formats": ["image/tiff (COG)", "application/json"]
}
```

### 2. `POST /api/v1/dem/inspect`
Inspects the polygon and halo bounding box, provider, list of queried tiles, raster dimensions, and elevation range without returning raster data.
- **Request Format:** JSON
```json
{
  "nodes": [
    {"lat": 45.830, "lon": 6.860},
    {"lat": 45.833, "lon": 6.860},
    {"lat": 45.833, "lon": 6.864},
    {"lat": 45.830, "lon": 6.864}
  ],
  "dem_type": "COP30",
  "provider": "opentopography"
}
```

### 3. `POST /api/v1/dem/crop`
Performs spatial windowing, adds the 4-pixel halo, and streams back the Cloud Optimized GeoTIFF file.
- **Response Media Type:** `image/tiff`
- **Response Headers:**
  - `Content-Disposition`: `inline; filename="dem_halo_crop.tif"`
  - `X-DEM-Provider`: `opentopography` (or `aws_s3`)
  - `X-DEM-Type`: `COP30`
  - `X-DEM-Width`: Raster width in pixels (including 4-pixel margins).
  - `X-DEM-Height`: Raster height in pixels (including 4-pixel margins).
  - `X-DEM-Min-Elevation`: Minimum elevation in meters.
  - `X-DEM-Max-Elevation`: Maximum elevation in meters.
  - `X-DEM-Halo-Pixels`: Number of halo pixels added (`4`).
  - `X-DEM-Tiles-Queried`: Queried dataset or tiles.

---

## 7. Running & Testing

```powershell
# Run the automated test suite
.\.venv\Scripts\pytest -v test_server.py

# Start the server with Uvicorn (port 8000, auto-reload)
.\.venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
