"""Transitland v2 REST API provider.

Docs: https://www.transit.land/documentation/rest-api/
Base URL: https://transit.land/api/v2/rest

The free tier needs an API key (sign up at transit.land), passed either as
the ``TRANSITLAND_API_KEY`` environment variable or to the constructor.
The key is sent as an ``apikey`` HTTP header and is never logged or
returned to callers. (Note: Transitland's ``meta.next`` pagination URLs
embed the key in plaintext, so this provider ignores them entirely.)
"""
from __future__ import annotations

import os
import time

import httpx

from ..models import (
    DeparturesResult,
    Departure,
    Operator,
    RouteInfo,
    ServiceAlert,
    Stop,
)
from .base import ConfigurationError, TransitProvider, TransitProviderError

BASE_URL = "https://transit.land/api/v2/rest"
API_KEY_ENV = "TRANSITLAND_API_KEY"
_TIMEOUT_S = 15.0

# GTFS route_type -> human-readable mode
_ROUTE_TYPE_MODES = {
    0: "tram",
    1: "subway",
    2: "rail",
    3: "bus",
    4: "ferry",
    5: "cable tram",
    6: "aerial lift",
    7: "funicular",
    11: "trolleybus",
    12: "monorail",
}

_LOCATION_TYPE_LABELS = {
    0: "stop",
    1: "station",
    2: "entrance",
    3: "node",
    4: "boarding area",
}


def _pick_translation(items: list | None) -> str:
    """Pick English text from a GTFS-RT TranslatedString array."""
    if not items:
        return ""
    for item in items:
        if isinstance(item, dict) and item.get("language") in ("en", "en-US", None):
            text = item.get("text")
            if text:
                return str(text)
    first = items[0]
    if isinstance(first, dict):
        return str(first.get("text") or "")
    return str(first or "")


def _wallclock(value: str | None) -> str:
    """Trim an ISO datetime to a HH:MM wall-clock string."""
    if not value:
        return ""
    text = str(value).strip()
    # Handles "2026-09-20T14:05:00-04:00" and "14:05:00"
    if "T" in text:
        text = text.split("T", 1)[1]
    return text[:5]


class TransitlandProvider(TransitProvider):
    name = "transitland"

    def __init__(self, api_key: str | None = None, timeout_s: float = _TIMEOUT_S) -> None:
        self._api_key = api_key or os.environ.get(API_KEY_ENV, "").strip()
        self._timeout_s = timeout_s
        self._client: httpx.Client | None = None

    # -- internals ------------------------------------------------------

    def _require_key(self) -> str:
        if not self._api_key:
            raise ConfigurationError(
                "Transit data is not configured: set the TRANSITLAND_API_KEY "
                "environment variable to a free API key from transit.land."
            )
        return self._api_key

    def _client_or_raise(self) -> httpx.Client:
        key = self._require_key()
        if self._client is None:
            self._client = httpx.Client(
                base_url=BASE_URL,
                headers={"apikey": key, "Accept": "application/json"},
                timeout=self._timeout_s,
            )
        return self._client

    def _get(self, path: str, params: dict) -> dict:
        """GET with one retry on rate-limit / transient upstream failures."""
        client = self._client_or_raise()
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = client.get(path, params=params)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempts < 2:
                    time.sleep(1)
                    continue
                raise TransitProviderError(
                    "Transit data is temporarily unavailable "
                    f"(network error: {type(exc).__name__}). Please try again shortly.",
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise TransitProviderError(
                    "Transit data is temporarily unavailable. Please try again shortly.",
                    retryable=True,
                ) from exc

            if resp.status_code == 401:
                raise ConfigurationError(
                    "The transit API key was rejected (401). Check that "
                    "TRANSITLAND_API_KEY is valid."
                )
            if resp.status_code == 404:
                raise TransitProviderError(
                    "That stop or agency was not found in the transit data.",
                    retryable=False,
                )
            if resp.status_code == 429 and attempts < 2:
                time.sleep(2)
                continue
            if resp.status_code == 429:
                raise TransitProviderError(
                    "The transit data service is rate-limiting requests right now. "
                    "Please wait a minute and try again.",
                    retryable=True,
                )
            if resp.status_code >= 500 and attempts < 2:
                time.sleep(2)
                continue
            if resp.status_code >= 400:
                raise TransitProviderError(
                    f"Transit data is temporarily unavailable (upstream error "
                    f"{resp.status_code}). Please try again shortly.",
                    retryable=True,
                )
            try:
                data = resp.json()
            except ValueError as exc:
                raise TransitProviderError(
                    "Transit data came back in an unexpected format. "
                    "Please try again shortly.",
                    retryable=True,
                ) from exc
            return data if isinstance(data, dict) else {}

    # -- mapping helpers -----------------------------------------------

    @staticmethod
    def _map_route(raw: dict | None) -> RouteInfo:
        raw = raw or {}
        route_type = raw.get("route_type")
        return RouteInfo(
            id=str(raw.get("onestop_id") or raw.get("id") or ""),
            short_name=str(raw.get("route_short_name") or ""),
            long_name=str(raw.get("route_long_name") or ""),
            mode=_ROUTE_TYPE_MODES.get(route_type, str(raw.get("route_type") or "")),
            color=str(raw.get("route_color") or ""),
            operator_name=str(raw.get("operator_name") or ""),
        )

    @staticmethod
    def _map_stop(raw: dict) -> Stop:
        coords: list | None = None
        geometry = raw.get("geometry") or {}
        if isinstance(geometry, dict):
            coords = geometry.get("coordinates")
        lat = lon = None
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            try:
                lon, lat = float(coords[0]), float(coords[1])
            except (TypeError, ValueError):
                lat = lon = None
        place = raw.get("place") or {}
        region_bits = [
            place.get("adm1_name") or place.get("adm1_isostring"),
            place.get("adm0_name") or place.get("adm0_isostring"),
        ]
        parent = raw.get("parent") or {}
        wheelchair = raw.get("wheelchair_boarding")
        return Stop(
            id=str(raw.get("onestop_id") or raw.get("id") or ""),
            name=str(raw.get("stop_name") or "Unnamed stop"),
            lat=lat,
            lon=lon,
            code=str(raw.get("stop_code") or ""),
            location_type=_LOCATION_TYPE_LABELS.get(raw.get("location_type"), "stop"),
            wheelchair_accessible=(
                True if wheelchair == 1 else False if wheelchair == 2 else None
            ),
            timezone=str(raw.get("stop_timezone") or ""),
            parent_station_name=str(parent.get("stop_name") or ""),
            region=", ".join(b for b in region_bits if b),
            routes=[TransitlandProvider._map_route(r) for r in raw.get("routes") or []],
        )

    @staticmethod
    def _map_departure(raw: dict, stop_name: str) -> Departure:
        trip = raw.get("trip") or {}
        route = trip.get("route") or {}
        # Transitland reports arrival and departure blocks; prefer "departure".
        block = raw.get("departure") or raw.get("arrival") or {}
        scheduled = block.get("scheduled") or ""
        estimated = block.get("estimated") or ""
        realtime = bool(block.get("realtime"))
        delay_s = block.get("delay")
        delay_minutes: float | None = None
        if isinstance(delay_s, (int, float)):
            delay_minutes = round(delay_s / 60.0, 1)
        rel = str(raw.get("schedule_relationship") or raw.get("scheduleRelationship") or "")
        route_type = route.get("route_type")
        return Departure(
            route_short_name=str(route.get("route_short_name") or ""),
            route_long_name=str(route.get("route_long_name") or ""),
            mode=_ROUTE_TYPE_MODES.get(route_type, ""),
            headsign=str(trip.get("trip_headsign") or ""),
            scheduled_local=_wallclock(scheduled),
            estimated_local=_wallclock(estimated) if estimated else "",
            realtime=realtime,
            delay_minutes=delay_minutes,
            canceled=rel.upper() == "CANCELED",
            stop_name=stop_name,
        )

    @staticmethod
    def _map_alert(raw: dict) -> ServiceAlert:
        return ServiceAlert(
            id=str(raw.get("id") or ""),
            cause=str(raw.get("cause") or ""),
            effect=str(raw.get("effect") or ""),
            severity=str(raw.get("severity_level") or ""),
            header=_pick_translation(raw.get("header_text")),
            description=_pick_translation(raw.get("description_text")),
            url=_pick_translation(raw.get("url")),
        )

    # -- provider API ---------------------------------------------------

    def search_stops(
        self,
        query: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_m: int = 500,
        limit: int = 10,
    ) -> list[Stop]:
        if not query and (lat is None or lon is None):
            raise TransitProviderError(
                "search_stops needs either a name query or coordinates (lat and lon)."
            )
        params: dict = {"limit": max(1, min(limit, 50))}
        if query:
            params["search"] = query
        if lat is not None and lon is not None:
            params["lat"] = lat
            params["lon"] = lon
            # Transitland requires all three of lat/lon/radius, max 10 km.
            params["radius"] = max(50, min(int(radius_m), 10000))
        data = self._get("/stops", params)
        return [self._map_stop(s) for s in data.get("stops", []) if isinstance(s, dict)]

    def get_departures(
        self, stop_id: str, limit: int = 8, next_seconds: int = 3600
    ) -> DeparturesResult:
        if not stop_id or not stop_id.strip():
            raise TransitProviderError("get_departures needs a stop_id.")
        params = {
            "limit": max(1, min(limit, 50)),
            "next": max(60, min(int(next_seconds), 24 * 3600)),
        }
        data = self._get(f"/stops/{stop_id.strip()}/departures", params)
        stops = data.get("stops", [])
        if not stops:
            raise TransitProviderError(
                "No departure data came back for that stop. The stop ID may be "
                "invalid, or the feed may be outside its service window."
            )
        entry = stops[0]
        stop = self._map_stop(entry)
        departures = [
            self._map_departure(d, stop.name)
            for d in entry.get("departures", [])
            if isinstance(d, dict)
        ]
        realtime_available = any(d.realtime for d in departures)
        return DeparturesResult(
            stop=stop, departures=departures, realtime_available=realtime_available
        )

    def get_routes_serving_stop(self, stop_id: str) -> list[RouteInfo]:
        if not stop_id or not stop_id.strip():
            raise TransitProviderError("get_routes_serving_stop needs a stop_id.")
        data = self._get(f"/stops/{stop_id.strip()}", {"include_routes": "true"})
        stops = data.get("stops", [])
        if not stops:
            return []
        return [self._map_route(r) for r in stops[0].get("routes", []) if isinstance(r, dict)]

    def find_operators(self, query: str, limit: int = 5) -> list[Operator]:
        if not query or not query.strip():
            raise TransitProviderError("find_operators needs a name query.")
        data = self._get("/operators", {"search": query.strip(), "limit": max(1, min(limit, 20))})
        operators = []
        for raw in data.get("operators", []):
            if not isinstance(raw, dict):
                continue
            operators.append(
                Operator(
                    id=str(raw.get("onestop_id") or raw.get("id") or ""),
                    name=str(raw.get("name") or "Unnamed operator"),
                    short_name=str(raw.get("short_name") or ""),
                    website=str(raw.get("website") or ""),
                )
            )
        return operators

    def get_service_alerts(
        self, agency_or_region: str, limit: int = 10
    ) -> tuple[list[Operator], list[ServiceAlert]]:
        if not agency_or_region or not agency_or_region.strip():
            raise TransitProviderError("get_service_alerts needs an agency or region name.")
        operators = self.find_operators(agency_or_region.strip(), limit=3)
        if not operators:
            return [], []
        alerts: list[ServiceAlert] = []
        seen: set[str] = set()
        for op in operators:
            try:
                data = self._get(
                    f"/operators/{op.id}",
                    {"include_alerts": "true", "active": "true"},
                )
            except TransitProviderError:
                continue  # one bad operator shouldn't sink the others
            entries = data.get("operators", []) or []
            entry = entries[0] if entries else {}
            for raw in entry.get("alerts", []) or []:
                if not isinstance(raw, dict):
                    continue
                alert = self._map_alert(raw)
                key = alert.id or (alert.header, alert.description)
                if key in seen:
                    continue
                seen.add(key)
                alert.id = f"{op.short_name or op.name}: {alert.id}" if alert.id else (
                    op.short_name or op.name
                )
                alerts.append(alert)
                if len(alerts) >= max(1, min(limit, 50)):
                    break
            if len(alerts) >= max(1, min(limit, 50)):
                break
        return operators, alerts
