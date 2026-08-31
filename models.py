"""
Data models for the sez_server DEM API.
"""

from typing import List
from pydantic import BaseModel, Field, field_validator


class Coordinate(BaseModel):
    """
    Geographic coordinate in WGS84 (EPSG:4326).
    """
    lat: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude in decimal degrees (-90 to +90)"
    )
    lon: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude in decimal degrees (-180 to +180)"
    )


class DEMPolygonRequest(BaseModel):
    """
    Request model carrying the 4 boundary nodes of an area of interest.
    """
    nodes: List[Coordinate] = Field(
        ...,
        description="List of exactly 4 geographic nodes defining the requested area."
    )

    @field_validator("nodes")
    @classmethod
    def validate_nodes_count(cls, v: List[Coordinate]) -> List[Coordinate]:
        if len(v) != 4:
            raise ValueError(f"Exactly 4 nodes must be provided, got {len(v)}.")
        return v


class BoundingBox(BaseModel):
    """
    Geographic bounding envelope.
    """
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class DEMMetadataResponse(BaseModel):
    """
    Metadata describing the extracted DEM window, including halo metrics and elevation bounds.
    """
    requested_envelope: BoundingBox
    halo_envelope: BoundingBox
    halo_pixel_margin: int
    raster_width: int
    raster_height: int
    min_elevation_m: float
    max_elevation_m: float
    tiles_queried: List[str]
    crs: str = "EPSG:4326"
    resolution_deg: float
