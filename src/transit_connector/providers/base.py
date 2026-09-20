"""Provider abstraction for the transit connector.

A provider wraps one upstream transit data source (Transitland v2, a direct
GTFS-realtime feed, a mock for tests, ...) and maps it into the normalized
models in transit_connector.models. The MCP tools only talk to this
interface, so new providers can be added without changing any tool.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import (
    DeparturesResult,
    Operator,
    RouteInfo,
    ServiceAlert,
    Stop,
)


class TransitProviderError(Exception):
    """A user-facing provider failure.

    The message is safe to show to end users (no stack traces, no secrets).
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ConfigurationError(TransitProviderError):
    """The provider is not configured (e.g. missing API key)."""


class TransitProvider(ABC):
    """Interface every transit data provider must implement."""

    name: str = "base"

    @abstractmethod
    def search_stops(
        self,
        query: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        radius_m: int = 500,
        limit: int = 10,
    ) -> list[Stop]:
        """Find stops by name query and/or nearby coordinates.

        At least one of (query) or (lat and lon) should be given.
        """
        raise NotImplementedError

    @abstractmethod
    def get_departures(
        self, stop_id: str, limit: int = 8, next_seconds: int = 3600
    ) -> DeparturesResult:
        """Upcoming departures for one stop, realtime-aware when available."""
        raise NotImplementedError

    @abstractmethod
    def get_routes_serving_stop(self, stop_id: str) -> list[RouteInfo]:
        """Routes that serve a stop. Used by the trip planner."""
        raise NotImplementedError

    @abstractmethod
    def find_operators(self, query: str, limit: int = 5) -> list[Operator]:
        """Find transit agencies/operators matching a name query."""
        raise NotImplementedError

    @abstractmethod
    def get_service_alerts(
        self, agency_or_region: str, limit: int = 10
    ) -> tuple[list[Operator], list[ServiceAlert]]:
        """Active service alerts for an agency (matched operators + alerts)."""
        raise NotImplementedError
