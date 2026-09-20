"""Normalized, provider-agnostic data models for the transit connector.

Every TransitProvider implementation maps its upstream data into these
models, so the MCP tools never depend on a specific API's JSON shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RouteInfo:
    id: str
    short_name: str = ""
    long_name: str = ""
    mode: str = ""  # e.g. "bus", "subway", "rail", "ferry", "tram"
    color: str = ""
    operator_name: str = ""

    @property
    def display_name(self) -> str:
        name = self.short_name or self.long_name or self.id
        if self.long_name and self.short_name and self.short_name != self.long_name:
            name = f"{self.short_name} ({self.long_name})"
        return name


@dataclass
class Stop:
    id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    code: str = ""
    location_type: str = "stop"  # stop | station | entrance | ...
    wheelchair_accessible: bool | None = None
    timezone: str = ""
    parent_station_name: str = ""
    region: str = ""  # city / state / country hint when known
    routes: list[RouteInfo] = field(default_factory=list)


@dataclass
class Departure:
    route_short_name: str = ""
    route_long_name: str = ""
    mode: str = ""
    headsign: str = ""
    scheduled_local: str = ""  # wall-clock time at the stop, e.g. "14:05"
    estimated_local: str = ""  # realtime prediction, "" when unknown
    realtime: bool = False
    delay_minutes: float | None = None  # + late, - early, None unknown
    canceled: bool = False
    stop_name: str = ""

    @property
    def route_display(self) -> str:
        name = self.route_short_name or self.route_long_name or "?"
        if self.route_long_name and self.route_short_name and \
                self.route_short_name != self.route_long_name:
            name = f"{self.route_short_name} ({self.route_long_name})"
        return name


@dataclass
class DeparturesResult:
    stop: Stop
    departures: list[Departure] = field(default_factory=list)
    realtime_available: bool = False  # whether the feed publishes GTFS-RT at all


@dataclass
class ServiceAlert:
    id: str = ""
    cause: str = ""
    effect: str = ""
    severity: str = ""  # INFO | WARNING | SEVERE | UNKNOWN_SEVERITY
    header: str = ""
    description: str = ""
    url: str = ""


@dataclass
class Operator:
    id: str
    name: str
    short_name: str = ""
    website: str = ""


@dataclass
class TripLeg:
    instruction: str  # human-readable, e.g. "Board 501 Queen at Union Station"
    detail: str = ""  # extra context, e.g. headsign / direction


@dataclass
class TripOption:
    summary: str
    direct: bool
    legs: list[TripLeg] = field(default_factory=list)


@dataclass
class TripPlan:
    origin_label: str
    destination_label: str
    options: list[TripOption] = field(default_factory=list)
    note: str = ""  # caveats, e.g. "no direct route found"
