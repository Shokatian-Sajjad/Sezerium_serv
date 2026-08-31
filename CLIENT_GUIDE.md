# Copernicus DEM 30m Client Connection Guide

This guide explains how client-side applications can connect to the **sez_server** FastAPI service, request targeted DEM crops for any 4-node geographic polygon, receive the Cloud Optimized GeoTIFF (COG), and work with the embedded **4-pixel halo** for projection and upscaling/warping.

---

## 1. Server Endpoints Overview

- **Base URL:** `http://localhost:8000` (or your production server address)
- **Interactive Swagger Docs:** `http://localhost:8000/docs`

| Method | Endpoint | Description | Content-Type |
|---|---|---|---|
| `GET` | `/health` | Server health, version, and status | `application/json` |
| `POST` | `/api/v1/dem/inspect` | Returns envelope metadata, tiles queried, dimensions, and min/max elevation | `application/json` |
| `POST` | `/api/v1/dem/crop` | Extracts and returns the DEM elevation GeoTIFF with 4-pixel halo | `image/tiff` |

---

## 2. Request Geometry (4 Polygon Nodes)

The client sends a JSON payload with an array of exactly 4 geographic coordinates in WGS84 (`EPSG:4326`):

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

> **Note:** The server takes the minimum bounding box containing the 4 vertices, aligns it to the DEM pixel grid, and expands it outward by 4 DEM pixels ($\approx 123\,\text{m}$) in all 4 directions.

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
    ]
}

# 1. (Optional) Inspect metadata first
inspect_resp = requests.post(f"{SERVER_URL}/api/v1/dem/inspect", json=payload)
meta = inspect_resp.json()
print("Inspect Metadata:", meta)

# 2. Download the DEM GeoTIFF
response = requests.post(f"{SERVER_URL}/api/v1/dem/crop", json=payload)

if response.status_code == 200:
    # Read headers
    print("Dimensions:", response.headers.get("X-DEM-Width"), "x", response.headers.get("X-DEM-Height"))
    print("Elevation range:", response.headers.get("X-DEM-Min-Elevation"), "to", response.headers.get("X-DEM-Max-Elevation"), "m")

    # Option 1: Save directly to a GeoTIFF file
    with open("terrain_with_halo.tif", "wb") as f:
        f.write(response.content)
    print("Saved to terrain_with_halo.tif")

    # Option 2: Open directly in memory with rasterio
    with rasterio.open(io.BytesIO(response.content)) as src:
        elevation = src.read(1)  # Float32 NumPy array
        profile = src.profile
        print("Loaded elevation array shape:", elevation.shape)
        print("Min elevation:", elevation.min(), "Max elevation:", elevation.max())
else:
    print("Error:", response.status_code, response.text)
```

---

### B. JavaScript / TypeScript (Node.js or Browser `fetch`)

#### Node.js (`fetch` & save file):
```javascript
import fs from 'fs';

const SERVER_URL = 'http://localhost:8000';

const payload = {
  nodes: [
    { lat: 45.830, lon: 6.860 },
    { lat: 45.833, lon: 6.860 },
    { lat: 45.833, lon: 6.864 },
    { lat: 45.830, lon: 6.864 }
  ]
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

#### Browser / React (Fetch as Blob / URL):
```javascript
async function getDEMBlob() {
  const response = await fetch('http://localhost:8000/api/v1/dem/crop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      nodes: [
        { lat: 45.830, lon: 6.860 },
        { lat: 45.833, lon: 6.860 },
        { lat: 45.833, lon: 6.864 },
        { lat: 45.830, lon: 6.864 }
      ]
    })
  });

  const blob = await response.blob();
  // Can be parsed with geotiff.js in browser:
  // const tiff = await GeoTIFF.fromBlob(blob);
  return blob;
}
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

---

## 5. Starting the Server

In the project folder `C:\Users\sajjad\Desktop\sez_server`:

```powershell
# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# Run server with Uvicorn (port 8000, auto-reload)
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
