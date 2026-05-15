"""Maps plugin — geocoding + reverse + distance via OSM/Mapbox/Google.

Provider selector mirrors weather + web_search; OSM Nominatim is the
default (free, fair-use rate-limit, requires a UA header).
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity
from spark.errors import ErrorCode, SparkError
from spark.plugins._http_base import (
    build_client,
    classify_connect_error,
    resolve_secret,
)


class MapsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["osm_nominatim", "mapbox", "google"] = Field(
        default="osm_nominatim"
    )
    api_key_secret: str = Field(default="maps_key", max_length=128)
    user_agent: str = Field(
        default="spark-agent/0.1",
        max_length=256,
        description="Required by Nominatim ToS.",
    )
    language: str = Field(default="en", max_length=8)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    verify_ssl: bool = True


class _MapsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["geocode", "reverse_geocode", "distance"] = Field(
        description="'geocode' (address → lat/lon), 'reverse_geocode', 'distance' (two points)."
    )
    query: str | None = Field(default=None, max_length=512)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    from_latitude: float | None = Field(default=None, ge=-90, le=90)
    from_longitude: float | None = Field(default=None, ge=-180, le=180)
    to_latitude: float | None = Field(default=None, ge=-90, le=90)
    to_longitude: float | None = Field(default=None, ge=-180, le=180)
    limit: int = Field(default=5, gt=0, le=20)


class MapsHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    latitude: float
    longitude: float
    address: dict[str, Any] | None = None


class MapsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    provider: str | None = None
    hits: list[MapsHit] | None = None
    distance_meters: float | None = None
    duration_seconds: float | None = None
    error: str | None = None


class MapsPlugin:
    name: ClassVar[str] = "maps"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Geocoding + reverse + distance. OSM Nominatim default (free); "
        "Mapbox or Google as paid alternatives."
    )
    input_schema: ClassVar[type[BaseModel]] = _MapsArgs
    output_schema: ClassVar[type[BaseModel]] = MapsResult
    config_schema: ClassVar[type[BaseModel]] = MapsConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: _MapsArgs, ctx: Any) -> MapsResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        provider = (cfg.get("provider") or "osm_nominatim").strip()

        async with build_client(cfg) as client:
            if provider == "osm_nominatim":
                return await _do_nominatim(client, args, cfg)
            if provider == "mapbox":
                key = resolve_secret(
                    cfg,
                    config_key="api_key_secret",
                    default_secret_name="maps_key",
                    plugin_name="maps",
                    ctx=ctx,
                )
                return await _do_mapbox(client, args, key)
            if provider == "google":
                key = resolve_secret(
                    cfg,
                    config_key="api_key_secret",
                    default_secret_name="maps_key",
                    plugin_name="maps",
                    ctx=ctx,
                )
                return await _do_google(client, args, key)
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                f"maps: unknown provider {provider!r}",
                detail={"plugin": "maps", "provider": provider},
            )


async def _do_nominatim(
    client: httpx.AsyncClient, args: _MapsArgs, cfg: dict[str, Any]
) -> MapsResult:
    headers = {
        "User-Agent": cfg.get("user_agent") or "spark-agent/0.1",
        "Accept": "application/json",
        "Accept-Language": cfg.get("language") or "en",
    }
    if args.action == "geocode":
        if not args.query:
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                "maps: geocode requires query",
                detail={"plugin": "maps"},
            )
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": args.query,
            "format": "jsonv2",
            "limit": str(args.limit),
            "addressdetails": "1",
        }
        try:
            resp = await client.get(url, headers=headers, params=params)
        except httpx.RequestError as exc:
            raise classify_connect_error(exc, url=url, plugin_name="maps") from exc
        if resp.status_code >= 400:
            return MapsResult(action="geocode", ok=False, error=resp.text[:200])
        hits = [
            MapsHit(
                label=r.get("display_name", ""),
                latitude=float(r.get("lat", 0)),
                longitude=float(r.get("lon", 0)),
                address=r.get("address"),
            )
            for r in resp.json()
        ]
        return MapsResult(action="geocode", ok=True, provider="osm_nominatim", hits=hits)

    if args.action == "reverse_geocode":
        if args.latitude is None or args.longitude is None:
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                "maps: reverse_geocode requires latitude + longitude",
                detail={"plugin": "maps"},
            )
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": str(args.latitude),
            "lon": str(args.longitude),
            "format": "jsonv2",
            "addressdetails": "1",
        }
        try:
            resp = await client.get(url, headers=headers, params=params)
        except httpx.RequestError as exc:
            raise classify_connect_error(exc, url=url, plugin_name="maps") from exc
        if resp.status_code >= 400:
            return MapsResult(action="reverse_geocode", ok=False, error=resp.text[:200])
        r = resp.json()
        return MapsResult(
            action="reverse_geocode",
            ok=True,
            provider="osm_nominatim",
            hits=[
                MapsHit(
                    label=r.get("display_name", ""),
                    latitude=float(r.get("lat", 0)),
                    longitude=float(r.get("lon", 0)),
                    address=r.get("address"),
                )
            ],
        )

    if args.action == "distance":
        if (
            args.from_latitude is None
            or args.from_longitude is None
            or args.to_latitude is None
            or args.to_longitude is None
        ):
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                "maps: distance requires from_latitude/longitude + to_latitude/longitude",
                detail={"plugin": "maps"},
            )
        # OSRM public demo server for routing. Free, fair-use rate-limit.
        url = (
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{args.from_longitude},{args.from_latitude};"
            f"{args.to_longitude},{args.to_latitude}"
        )
        try:
            resp = await client.get(url)
        except httpx.RequestError as exc:
            raise classify_connect_error(exc, url=url, plugin_name="maps") from exc
        if resp.status_code >= 400:
            return MapsResult(action="distance", ok=False, error=resp.text[:200])
        routes = resp.json().get("routes") or []
        if not routes:
            return MapsResult(action="distance", ok=False, error="no route found")
        r = routes[0]
        return MapsResult(
            action="distance",
            ok=True,
            provider="osm_nominatim",
            distance_meters=r.get("distance"),
            duration_seconds=r.get("duration"),
        )

    raise SparkError(
        ErrorCode.INPUT_SCHEMA_INVALID,
        f"maps: unknown action {args.action!r}",
        detail={"plugin": "maps"},
    )


async def _do_mapbox(
    client: httpx.AsyncClient, args: _MapsArgs, key: str
) -> MapsResult:
    # Minimal Mapbox impl — geocode + reverse. Distance would need
    # /directions/v5 which is out of scope for a thin wrapper.
    if args.action == "geocode" and args.query:
        url = (
            f"https://api.mapbox.com/geocoding/v5/mapbox.places/{args.query}.json"
            f"?access_token={key}&limit={args.limit}"
        )
    elif args.action == "reverse_geocode" and args.latitude is not None and args.longitude is not None:
        url = (
            f"https://api.mapbox.com/geocoding/v5/mapbox.places/"
            f"{args.longitude},{args.latitude}.json?access_token={key}"
        )
    else:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "maps: mapbox provider supports geocode + reverse_geocode only",
            detail={"plugin": "maps", "action": args.action},
        )
    try:
        resp = await client.get(url)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="maps") from exc
    if resp.status_code == 401:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            "maps: mapbox key rejected",
            detail={"plugin": "maps", "secret_name": "maps_key"},
        )
    if resp.status_code >= 400:
        return MapsResult(action=args.action, ok=False, error=resp.text[:200])
    features = resp.json().get("features") or []
    return MapsResult(
        action=args.action,
        ok=True,
        provider="mapbox",
        hits=[
            MapsHit(
                label=f.get("place_name", ""),
                latitude=f.get("center", [0, 0])[1],
                longitude=f.get("center", [0, 0])[0],
                address=f.get("properties"),
            )
            for f in features
        ],
    )


async def _do_google(
    client: httpx.AsyncClient, args: _MapsArgs, key: str
) -> MapsResult:
    if args.action == "geocode" and args.query:
        url = (
            f"https://maps.googleapis.com/maps/api/geocode/json"
            f"?address={args.query}&key={key}"
        )
    elif args.action == "reverse_geocode" and args.latitude is not None:
        url = (
            f"https://maps.googleapis.com/maps/api/geocode/json"
            f"?latlng={args.latitude},{args.longitude}&key={key}"
        )
    else:
        raise SparkError(
            ErrorCode.INPUT_SCHEMA_INVALID,
            "maps: google provider supports geocode + reverse_geocode only",
            detail={"plugin": "maps", "action": args.action},
        )
    try:
        resp = await client.get(url)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="maps") from exc
    if resp.status_code >= 400:
        return MapsResult(action=args.action, ok=False, error=resp.text[:200])
    data = resp.json()
    if data.get("status") == "REQUEST_DENIED":
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            f"maps: google key rejected: {data.get('error_message') or 'unknown'}",
            detail={"plugin": "maps", "secret_name": "maps_key"},
        )
    return MapsResult(
        action=args.action,
        ok=True,
        provider="google",
        hits=[
            MapsHit(
                label=r.get("formatted_address", ""),
                latitude=r.get("geometry", {}).get("location", {}).get("lat", 0),
                longitude=r.get("geometry", {}).get("location", {}).get("lng", 0),
                address={
                    c["types"][0]: c["long_name"]
                    for c in r.get("address_components", [])
                    if c.get("types")
                },
            )
            for r in data.get("results", [])
        ],
    )
