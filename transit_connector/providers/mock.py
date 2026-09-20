"""Offline mock provider with canned Toronto-flavoured data.

Used for tests and local development without a Transitland API key.
Implements the same TransitProvider interface, so the MCP tools exercise
the exact same code paths as with the real provider.
"""
from __future__ import annotations

from .base import TransitProvider, TransitProviderError
from ..models import (
    DeparturesResult,
    Departure,
    Operator,
    RouteInfo,
    ServiceAlert,
    Stop,
)

_TTC = Operator(
    id="o-mock-ttc",
    name="Toronto Transit Commission",
    short_name="TTC",
    website="https://www.ttc.ca",
)

_LINE1 = RouteInfo(
    id="r-mock-line1",
    short_name="1",
    long_name="Yonge-University",
    mode="subway",
    color="FFC72C",
    operator_name="Toronto Transit Commission",
)
_501 = RouteInfo(
    id="r-mock-501",
    short_name="501",
    long_name="Queen",
    mode="tram",
    color="FF0000",
    operator_name="Toronto Transit Commission",
)
_939 = RouteInfo(
    id="r-mock-939",
    short_name="939",
    long_name="Finch Express",
    mode="bus",
    color="0099CC",
    operator_name="Toronto Transit Commission",
)

_STOPS = [
    Stop(
        id="s-mock-union",
        name="Union Station",
        lat=43.6453,
        lon=-79.3806,
        code="U001",
        location_type="station",
        wheelchair_accessible=True,
        timezone="America/Toronto",
        region="Ontario, Canada",
        routes=[_LINE1, _501],
    ),
    Stop(
        id="s-mock-blooryonge",
        name="Bloor-Yonge Station",
        lat=43.6710,
        lon=-79.3868,
        code="BY02",
        location_type="station",
        wheelchair_accessible=True,
        timezone="America/Toronto",
        region="Ontario, Canada",
        routes=[_LINE1],
    ),
    Stop(
        id="s-mock-dundas",
        name="Dundas Station",
        lat=43.6525,
        lon=-79.3794,
        code="D003",
        location_type="station",
        wheelchair_accessible=False,
        timezone="America/Toronto",
        region="Ontario, Canada",
        routes=[_LINE1],
    ),
    Stop(
        id="s-mock-finch",
        name="Finch Station",
        lat=43.7809,
        lon=-79.4142,
        code="F004",
        location_type="station",
        wheelchair_accessible=True,
        timezone="America/Toronto",
        region="Ontario, Canada",
        routes=[_939],
    ),
]


class MockProvider(TransitProvider):
    """Canned data; no network, no API key."""

    name = "mock"

    def _find_stop(self, stop_id: str) -> Stop:
        for stop in _STOPS:
            if stop.id == stop_id:
                return stop
        raise TransitProviderError(f"Mock stop not found: {stop_id}")

    def search_stops(
        self,
        query: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_m: int = 500,
        limit: int = 10,
    ) -> list[Stop]:
        results = list(_STOPS)
        if query:
            q = query.lower()
            results = [s for s in results if q in s.name.lower()]
        # lat/lon accepted but ignored: the mock returns the query matches.
        return results[: max(1, min(limit, 50))]

    def get_departures(
        self, stop_id: str, limit: int = 8, next_seconds: int = 3600
    ) -> DeparturesResult:
        stop = self._find_stop(stop_id)
        departures = [
            Departure(
                route_short_name="1",
                route_long_name="Yonge-University",
                mode="subway",
                headsign="Finch via Union",
                scheduled_local="14:02",
                estimated_local="14:05",
                realtime=True,
                delay_minutes=3.0,
                stop_name=stop.name,
            ),
            Departure(
                route_short_name="1",
                route_long_name="Yonge-University",
                mode="subway",
                headsign="Vaughan Metropolitan Centre",
                scheduled_local="14:06",
                estimated_local="14:06",
                realtime=True,
                delay_minutes=0.0,
                stop_name=stop.name,
            ),
            Departure(
                route_short_name="501",
                route_long_name="Queen",
                mode="tram",
                headsign="Long Branch",
                scheduled_local="14:09",
                estimated_local="",
                realtime=False,
                delay_minutes=None,
                stop_name=stop.name,
            ),
            Departure(
                route_short_name="1",
                route_long_name="Yonge-University",
                mode="subway",
                headsign="Finch via Union",
                scheduled_local="14:12",
                estimated_local="",
                realtime=False,
                delay_minutes=None,
                canceled=True,
                stop_name=stop.name,
            ),
        ]
        return DeparturesResult(
            stop=stop,
            departures=departures[: max(1, min(limit, 50))],
            realtime_available=True,
        )

    def get_routes_serving_stop(self, stop_id: str) -> list[RouteInfo]:
        return list(self._find_stop(stop_id).routes)

    def find_operators(self, query: str, limit: int = 5) -> list[Operator]:
        if query and "toronto" in query.lower():
            return [_TTC]
        return []

    def get_service_alerts(
        self, agency_or_region: str, limit: int = 10
    ) -> tuple[list[Operator], list[ServiceAlert]]:
        operators = self.find_operators(agency_or_region)
        if not operators:
            return [], []
        alerts = [
            ServiceAlert(
                id="TTC: mock-alert-1",
                cause="MAINTENANCE",
                effect="REDUCED_SERVICE",
                severity="WARNING",
                header="Line 1: weekend track work Bloor-Yonge to St Clair",
                description=(
                    "This weekend, Line 1 trains run every 10 minutes between "
                    "Bloor-Yonge and St Clair due to planned track work. "
                    "Shuttle buses operate."
                ),
                url="https://www.ttc.ca",
            )
        ]
        return operators, alerts[: max(1, min(limit, 50))]
