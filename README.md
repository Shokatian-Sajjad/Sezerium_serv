# Sezerium_serv

Server side for Sezerium — a high-performance elevation server built with **FastAPI**, **Python 3.13**, **NumPy 2.0+**, and **Rasterio**.

It extracts targeted elevation bounding boxes from the AWS Open Data **Copernicus DEM GLO-30 (30m)** dataset using **HTTP spatial windowing** (fetching only the required byte ranges rather than downloading full gigabyte tiles), pads the perimeter with a **4-pixel real terrain halo** for client-side warping/reprojection, and streams the result directly as a **Cloud Optimized GeoTIFF (COG)**.

---

## Features

- **Direct AWS S3 Streaming:** Queries Copernicus DEM tiles (`copernicus-dem-30m`) via GDAL/Rasterio HTTP range requests with zero unnecessary data transfer.
- **4-Pixel Halo:** Automatically adds a 4-pixel border with real surrounding terrain around the target envelope to prevent edge distortion during reprojection and spline upscaling.
- **Multi-Tile Seamless Mosaicing:** Automatically stitches multiple 1° × 1° tiles when an area crosses degree lines.
- **Ocean / Sea Defaulting:** Non-existent DEM tiles over open ocean are caught via cached pre-flight HEAD requests and cleanly filled with `0.0m` elevation.
- **Cloud Optimized GeoTIFF:** Returns single-band Float32 GeoTIFFs compressed with Deflate and floating-point predictor (`predictor=3`).

---

## Quick Setup Guide

### 1. Clone the Repository
```bash
git clone https://github.com/Shokatian-Sajjad/Sezerium_serv.git
cd Sezerium_serv
```

### 2. Create and Activate Virtual Environment
Requires **Python 3.13**:

- **Windows (PowerShell):**
  ```powershell
  py -3.13 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```
- **Linux / macOS:**
  ```bash
  python3.13 -m venv .venv
  source .venv/bin/activate
  ```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run Automated Tests
```bash
pytest -v test_server.py
```

### 5. Start the Server
```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The interactive Swagger API documentation will be available at:
👉 **`http://localhost:8000/docs`**

---

## API Endpoints

### 1. `GET /health`
Returns server status, configuration, and DEM dataset info.

### 2. `POST /api/v1/dem/inspect`
Inspects an area and returns metadata (bounding box, halo metrics, dimensions, elevation min/max, tiles queried) without transferring raster bytes.

**Request:**
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

### 3. `POST /api/v1/dem/crop`
Accepts the same 4-node polygon payload and streams back the **GeoTIFF (`image/tiff`)** with the 4-pixel halo.

---

## Client Integration

Detailed connection guides and code examples for **Python**, **JavaScript / Node.js**, **Browser / React**, and **cURL** are available in [CLIENT_GUIDE.md](./CLIENT_GUIDE.md).

For full architectural and algorithm details, see [D.md](./D.md).
