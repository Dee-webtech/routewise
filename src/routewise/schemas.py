"""Request and response models (Pydantic v2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90, description="Latitude in degrees")
    lng: float = Field(ge=-180, le=180, description="Longitude in degrees")


class Stop(LatLng):
    id: str | int = Field(description="Your identifier for this stop (unique per request)")

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str | int) -> str | int:
        if isinstance(value, str) and not 1 <= len(value) <= 64:
            raise ValueError("stop id must be 1 to 64 characters")
        return value


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depot: LatLng
    stops: list[Stop] = Field(min_length=1)
    num_vehicles: int = Field(default=1, ge=1)
    distance_mode: Literal["haversine", "graph"] = "haversine"
    graph_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_consistency(self) -> OptimizeRequest:
        seen: set[str] = set()
        duplicates: list[str] = []
        for stop in self.stops:
            key = str(stop.id)
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        if duplicates:
            raise ValueError(f"duplicate stop ids: {', '.join(duplicates[:5])}")
        if self.distance_mode == "graph" and not self.graph_id:
            raise ValueError("graph_id is required when distance_mode is 'graph'")
        if self.distance_mode == "haversine" and self.graph_id:
            raise ValueError("graph_id is only valid when distance_mode is 'graph'")
        return self

    def check_limits(self, max_stops: int, max_vehicles: int) -> None:
        """Raise ValueError if the request exceeds the configured limits."""
        if len(self.stops) > max_stops:
            raise ValueError(f"too many stops: {len(self.stops)} (the limit is {max_stops})")
        if self.num_vehicles > max_vehicles:
            raise ValueError(
                f"too many vehicles: {self.num_vehicles} (the limit is {max_vehicles})"
            )


class RouteStop(BaseModel):
    sequence: int
    id: str | int
    lat: float
    lng: float


class RouteOut(BaseModel):
    vehicle: int
    distance_km: float
    stops: list[RouteStop]


class OptimizeResult(BaseModel):
    distance_mode: str
    accelerator: str
    depot: LatLng
    num_vehicles_used: int
    total_distance_km: float
    baseline_distance_km: float
    # Positive = shorter than your original order. Negative means your original
    # order was already better than the heuristic's result; it is reported, not hidden.
    saved_km: float
    saved_percent: float
    routes: list[RouteOut]


class JobOut(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str
    updated_at: str
    result: OptimizeResult | None = None
    error: str | None = None


class GraphUpload(BaseModel):
    """Road graph document. Node/edge entries are validated by routewise.graph.Graph."""

    name: str | None = Field(default=None, max_length=100)
    nodes: list[dict[str, Any]] = Field(min_length=1)
    edges: list[dict[str, Any]]


class GraphOut(BaseModel):
    id: str
    name: str | None
    node_count: int
    edge_count: int
    created_at: str


class PathOut(BaseModel):
    from_node: int
    to_node: int
    algo: str
    cost_km: float
    path: list[int]
    nodes_expanded: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
