# Copernicus & Global DEM Client Connection Guide

This guide explains how client-side applications can connect to the **sez_server** FastAPI service, request targeted DEM crops for any 4-node geographic polygon, receive the Cloud Optimized GeoTIFF (COG), and work with the embedded **4-pixel halo** for projection and upscaling/warping.

---

## 1. Server Endpoints Overview

- **Base URL:** `http://localhost:8000` (or your production server address)
- **Interactive Swagger Docs:** `http://localhost:8000/docs`

| Method | Endpoint | Description | Content-Type |
|---|---|---|---|
| `GET` | `/health` | Server health, active provider, and status | `application/json` |
| `POST` | `/api/v1/dem/inspect` | Returns envelope metadata, dataset queried, dimensions, and min/max elevation | `application/json` |
| `POST` | `/api/v1/dem/crop` | Extracts and returns the DEM elevation GeoTIFF with 4-pixel halo | `image/tiff` |

---

## 2. Request Geometry & Parameters

The client sends a JSON payload with an array of exactly 4 geographic coordinates in WGS84 (`EPSG:4326`) and optional provider/dataset settings:

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

### Supported `dem_type` values (OpenTopography):
- `COP30` — Copernicus Global DSM 30m (Default)
- `COP90` — Copernicus Global DSM 90m
- `SRTMGL1` — NASA Shuttle Radar Topography Mission 30m
- `AW3D30` — ALOS World 3D 30m
- `NASADEM` — NASADEM Global DEM 30m

---

## 3. Understanding the 4-Pixel Halo on the Client Side

### Why the 4-Pixel Halo Exists:
When reprojecting (e.g. from WGS84 to UTM/WebMercator) or upscaling raster images using bicubic or bilinear spline filters, standard convolution kernels require 2 to 4 neighboring pixels outside the boundary. Without a halo, the edge pixels would suffer from severe edge distortion or nodata clamping artifacts.

### How to use it in your client:
1. **Warping / Projection Step:**
   - Pass the full GeoTIFF (including the 4-pixel border) into your warping / projection pipeline (e.g., `rasterio.warp.reproject`, GDAL warp, OpenCV, or WebGL shader).
2. **Crop the Halo (Optional Post-Processing):**
   - If you only want the strictly requested inner area after warping, slice the 4-pixel perimeter:
     ```python
     # In Python (NumPy array):
     inner_raster = raster[4:-4, 4:-4]
     ```

---

## 4. Client Code Examples

### A. Python Client (Direct to NumPy array & file save)

```python
import requests
import io
import rasterio

SERVER_URL = "http://localhost:8000"

payload = {
    "nodes": [
        {"lat": 45.830, "lon": 6.860},
        {"lat": 45.833, "lon": 6.860},
        {"lat": 45.833, "lon": 6.864},
        {"lat": 45.830, "lon": 6.864}
    ],
    "dem_type": "COP30",
    "provider": "opentopography"
}

# 1. (Optional) Inspect metadata first
inspect_resp = requests.post(f"{SERVER_URL}/api/v1/dem/inspect", json=payload)
meta = inspect_resp.json()
print("Inspect Metadata:", meta)

# 2. Download the DEM GeoTIFF
response = requests.post(f"{SERVER_URL}/api/v1/dem/crop", json=payload)

if response.status_code == 200:
    # Read headers
    print("Provider:", response.headers.get("X-DEM-Provider"))
    print("Dimensions:", response.headers.get("X-DEM-Width"), "x", response.headers.get("X-DEM-Height"))
    print("Elevation range:", response.headers.get("X-DEM-Min-Elevation"), "to", response.headers.get("X-DEM-Max-Elevation"), "m")

    # Save to a GeoTIFF file
    with open("terrain_with_halo.tif", "wb") as f:
        f.write(response.content)
    print("Saved to terrain_with_halo.tif")

    # Or open directly in memory with rasterio
    with rasterio.open(io.BytesIO(response.content)) as src:
        elevation = src.read(1)  # Float32 NumPy array
        print("Loaded elevation array shape:", elevation.shape)
        print("Min elevation:", elevation.min(), "Max elevation:", elevation.max())
else:
    print("Error:", response.status_code, response.text)
```

---

### B. JavaScript / TypeScript (Node.js or Browser `fetch`)

#### Node.js:
```javascript
import fs from 'fs';

const SERVER_URL = 'http://localhost:8000';

const payload = {
  nodes: [
    { lat: 45.830, lon: 6.860 },
    { lat: 45.833, lon: 6.860 },
    { lat: 45.833, lon: 6.864 },
    { lat: 45.830, lon: 6.864 }
  ],
  dem_type: 'COP30'
};

async function fetchDEM() {
  const response = await fetch(`${SERVER_URL}/api/v1/dem/crop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  const arrayBuffer = await response.arrayBuffer();
  fs.writeFileSync('terrain_with_halo.tif', Buffer.from(arrayBuffer));
  console.log('Saved DEM TIFF file successfully!');
}

fetchDEM();
```

---

### C. cURL (Command Line)

#### Inspect Metadata:
```bash
curl -X POST "http://localhost:8000/api/v1/dem/inspect" \
     -H "Content-Type: application/json" \
     -d "{\"nodes\": [{\"lat\": 45.83, \"lon\": 6.86}, {\"lat\": 45.833, \"lon\": 6.86}, {\"lat\": 45.833, \"lon\": 6.864}, {\"lat\": 45.83, \"lon\": 6.864}]}"
```

#### Download GeoTIFF directly:
```bash
curl -X POST "http://localhost:8000/api/v1/dem/crop" \
     -H "Content-Type: application/json" \
     -d "{\"nodes\": [{\"lat\": 45.83, \"lon\": 6.86}, {\"lat\": 45.833, \"lon\": 6.86}, {\"lat\": 45.833, \"lon\": 6.864}, {\"lat\": 45.83, \"lon\": 6.864}]}" \
     --output dem_crop.tif
```
