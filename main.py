"""
main.py
=======
FastAPI server application providing targeted Copernicus & Global DEM extraction
with JWT Bearer Authentication, IP-based Rate Limiting, and Cloud-Optimized GeoTIFF streaming.

Endpoints:
- GET  /health: Server health, authentication, active provider, and capability status.
- POST /api/v1/auth/token: Exchanges client API key for a signed JWT Bearer token.
- POST /api/v1/dem/crop: (Protected) Accepts 4 polygon nodes, returns in-memory COG GeoTIFF (`image/tiff`).
- POST /api/v1/dem/inspect: (Protected) Accepts 4 polygon nodes, returns spatial metadata and elevation stats.
"""

import os
from fastapi import FastAPI, HTTPException, Request, Response, status, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

from models import (
    DEMPolygonRequest,
    DEMMetadataResponse,
    TokenRequest,
    TokenResponse,
)
from auth import (
    verify_client_api_key,
    create_access_token,
    get_current_client,
)
from dem_engine import (
    extract_dem_raster,
    export_as_cog_bytes,
    HALO_PIXELS,
    DEFAULT_DEM_PROVIDER,
    DEFAULT_DEM_TYPE,
    OPENTOPOGRAPHY_API_KEY,
)

load_dotenv()

# Rate limiting configuration
RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "120/minute").strip()
RATE_LIMIT_CROP = os.getenv("RATE_LIMIT_CROP", "60/minute").strip()
RATE_LIMIT_AUTH = os.getenv("RATE_LIMIT_AUTH", "10/minute").strip()

limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT_DEFAULT])

app = FastAPI(
    title="sez_server - DEM Windowing & Extraction Service",
    description=(
        "High-performance FastAPI DEM server providing targeted spatial windowing and extraction "
        "via OpenTopography Global DEM API and AWS Open Data Copernicus DEM (GLO-30) with automatic "
        "4-pixel halo padding, JWT Bearer authentication, and IP-based rate limiting."
    ),
    version="1.2.0",
)

app.state.limiter = limiter


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom error response returned when an IP address exceeds request rate limits.
    """
    client_ip = get_remote_address(request)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "Rate limit exceeded",
            "detail": f"Too many requests from IP address {client_ip}. Please reduce request frequency.",
            "limit": str(exc.detail),
        },
        headers={"Retry-After": "60"},
    )


app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

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
    Returns server operational status, active DEM provider, authentication, and rate limiting status.
    """
    return {
        "status": "healthy",
        "service": "sez_server",
        "active_provider": DEFAULT_DEM_PROVIDER,
        "default_dem_type": DEFAULT_DEM_TYPE,
        "opentopography_configured": bool(OPENTOPOGRAPHY_API_KEY),
        "authentication": "JWT Bearer",
        "rate_limiting": {
            "enabled": True,
            "default_limit": RATE_LIMIT_DEFAULT,
            "crop_limit": RATE_LIMIT_CROP,
            "auth_limit": RATE_LIMIT_AUTH,
        },
        "halo_pixels": HALO_PIXELS,
        "supported_response_formats": ["image/tiff (COG)", "application/json"],
    }


@app.post(
    "/api/v1/auth/token",
    response_model=TokenResponse,
    summary="Obtain JWT Bearer Access Token",
    responses={
        200: {"description": "Access token issued successfully."},
        401: {"description": "Invalid client API key."},
        429: {"description": "Too many token requests from this IP."},
    },
)
@limiter.limit(RATE_LIMIT_AUTH)
def login_for_access_token(request: Request, body: TokenRequest):
    """
    Authenticates the client using a pre-shared client API key and returns a signed JWT access token.
    """
    if not verify_client_api_key(body.client_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client API key. Verify CLIENT_API_KEY configuration.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_str, expires_in = create_access_token(
        subject=body.client_id or "sez_client",
        extra_claims={"client_id": body.client_id or "sez_client"},
    )

    return TokenResponse(
        access_token=token_str,
        token_type="bearer",
        expires_in_seconds=expires_in,
    )


@app.post(
    "/api/v1/dem/crop",
    summary="Extract DEM GeoTIFF for Polygon with 4-Pixel Halo (Protected)",
    response_class=Response,
    responses={
        200: {
            "content": {"image/tiff": {}},
            "description": "Cloud Optimized GeoTIFF (Float32) containing elevation data plus 4-pixel halo.",
        },
        400: {"description": "Invalid input geometry."},
        401: {"description": "Missing or invalid JWT Bearer token."},
        429: {"description": "IP rate limit exceeded."},
        500: {"description": "Raster extraction error."},
    },
)
@limiter.limit(RATE_LIMIT_CROP)
def crop_dem_geotiff(
    request: Request,
    body: DEMPolygonRequest,
    current_client: dict = Depends(get_current_client),
):
    """
    Extracts the targeted DEM bounding box for the supplied 4 polygon vertices.
    Requires a valid JWT Bearer token in the `Authorization: Bearer <token>` header.
    """
    try:
        raster, transform, meta = extract_dem_raster(
            nodes=body.nodes,
            dem_type=body.dem_type,
            provider=body.provider,
            halo_pixels=HALO_PIXELS,
        )
        cog_bytes = export_as_cog_bytes(raster, transform)

        headers = {
            "Content-Disposition": 'inline; filename="dem_halo_crop.tif"',
            "X-DEM-Provider": str(meta.get("provider", "unknown")),
            "X-DEM-Type": str(meta.get("dem_type", "COP30")),
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
    summary="Inspect DEM bounds and elevation range without downloading full raster (Protected)",
    responses={
        200: {"description": "Elevation bounds and metadata."},
        401: {"description": "Missing or invalid JWT Bearer token."},
        429: {"description": "IP rate limit exceeded."},
    },
)
@limiter.limit(RATE_LIMIT_DEFAULT)
def inspect_dem_envelope(
    request: Request,
    body: DEMPolygonRequest,
    current_client: dict = Depends(get_current_client),
):
    """
    Calculates bounds, 4-pixel halo extent, provider queried, and elevation statistics
    for the supplied 4 polygon nodes without returning the binary raster.
    Requires a valid JWT Bearer token.
    """
    try:
        _, _, meta = extract_dem_raster(
            nodes=body.nodes,
            dem_type=body.dem_type,
            provider=body.provider,
            halo_pixels=HALO_PIXELS,
        )
        return DEMMetadataResponse(**meta)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error inspecting DEM: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
