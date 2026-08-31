"""
main.py
=======
FastAPI server application providing targeted Copernicus DEM 30m extraction.

Endpoints:
- GET  /health: Server health and capability status.
- POST /api/v1/dem/crop: Accepts 4 polygon nodes, returns in-memory COG GeoTIFF (`image/tiff`).
- POST /api/v1/dem/inspect: Accepts 4 polygon nodes, returns spatial metadata and elevation statistics.
"""

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from models import DEMPolygonRequest, DEMMetadataResponse
from dem_engine import extract_dem_raster, export_as_cog_bytes, HALO_PIXELS

app = FastAPI(
    title="sez_server - Copernicus DEM 30m Windowing Service",
    description=(
        "High-performance FastAPI DEM server providing targeted spatial windowing from "
        "AWS Open Data Copernicus DEM (GLO-30) with automatic 4-pixel halo padding and COG output."
    ),
    version="1.0.0",
)

# Enable CORS for cross-origin web/client requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", summary="Health check")
def health_check():
    """
    Returns server operational status and configuration.
    """
    return {
        "status": "healthy",
        "service": "sez_server",
        "dem_dataset": "Copernicus DEM GLO-30 (30m)",
        "halo_pixels": HALO_PIXELS,
        "supported_response_formats": ["image/tiff (COG)", "application/json"],
    }


@app.post(
    "/api/v1/dem/crop",
    summary="Extract DEM GeoTIFF for Polygon with 4-Pixel Halo",
    response_class=Response,
    responses={
        200: {
            "content": {"image/tiff": {}},
            "description": "Cloud Optimized GeoTIFF (Float32) containing elevation data plus 4-pixel halo.",
        },
        400: {"description": "Invalid input geometry."},
        500: {"description": "Raster extraction error."},
    },
)
def crop_dem_geotiff(request: DEMPolygonRequest):
    """
    Extracts the targeted DEM bounding box for the supplied 4 polygon vertices.
    
    Processing steps:
    1. Computes polygon bounding box (min_lon, min_lat, max_lon, max_lat).
    2. Expands bounds outward by 4 DEM pixels (~123m) to include real terrain for warping.
    3. Fetches *only* the required spatial window from AWS Open Data Copernicus DEM GLO-30 via S3 HTTP range requests.
    4. Automatically defaults water/ocean areas with no DEM tiles to 0.0m.
    5. Serializes raster into an in-memory Cloud Optimized GeoTIFF (COG) with Deflate compression.
    6. Streams back `image/tiff` binary payload directly.
    """
    try:
        raster, transform, meta = extract_dem_raster(request.nodes, halo_pixels=HALO_PIXELS)
        cog_bytes = export_as_cog_bytes(raster, transform)

        headers = {
            "Content-Disposition": 'inline; filename="dem_halo_crop.tif"',
            "X-DEM-Width": str(meta["raster_width"]),
            "X-DEM-Height": str(meta["raster_height"]),
            "X-DEM-Min-Elevation": str(meta["min_elevation_m"]),
            "X-DEM-Max-Elevation": str(meta["max_elevation_m"]),
            "X-DEM-Halo-Pixels": str(meta["halo_pixel_margin"]),
            "X-DEM-Tiles-Queried": ",".join(meta["tiles_queried"]),
        }

        return Response(content=cog_bytes, media_type="image/tiff", headers=headers)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error extracting DEM: {str(e)}",
        )


@app.post(
    "/api/v1/dem/inspect",
    response_model=DEMMetadataResponse,
    summary="Inspect DEM bounds and elevation range without downloading full raster",
)
def inspect_dem_envelope(request: DEMPolygonRequest):
    """
    Calculates the bounds, 4-pixel halo extent, tiles queried, and elevation statistics
    for the supplied 4 polygon nodes without returning the binary raster.
    """
    try:
        _, _, meta = extract_dem_raster(request.nodes, halo_pixels=HALO_PIXELS)
        return DEMMetadataResponse(**meta)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error inspecting DEM: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
