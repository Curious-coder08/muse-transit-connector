# Privacy Policy — Transit Times connector

**Last updated:** September 20, 2026

Transit Times ("the connector") provides real-time public transit information
inside Muse. This policy explains what data the connector handles.

## What the connector does with your queries

To answer a transit question (e.g. "when's the next bus from Yonge and
Shuter?"), the connector forwards the stop name, coordinates, or agency you
asked about to its transit data provider (Transitland) to fetch departures,
alerts, and routes. Queries are processed in memory to generate the answer
and are **not stored, logged, or sold**.

## What the connector does NOT do

- No accounts, no sign-in, no personal profiles.
- No access to your messages, contacts, location history, or any other Muse
  data beyond the transit question you asked.
- No cookies, no advertising, no cross-site tracking.
- No user credentials are held by the connector.

## Data retention

The connector is stateless: it keeps no record of who asked what. Server
logs may transiently record technical request metadata (timestamps, error
counts) for reliability monitoring, retained no longer than 30 days.

## Third parties

Transit data comes from [Transitland](https://www.transit.land), which
aggregates public GTFS feeds published by transit agencies. Their handling of
API requests is governed by their own policies.

## Changes

If this policy changes materially, the connector's directory listing will be
updated.

## Contact

Questions: open an issue at
<https://github.com/Curious-coder08/muse-transit-connector/issues>.
