"""Weather plugin — current + forecast + alerts.

Provider abstraction:

- ``open_meteo`` — global, free, no key (default for non-US)
- ``nws`` — US-only, free, no key, official US data
- ``openweather`` — global, paid (free tier), needs key

The plugin's ``provider="auto"`` mode picks ``nws`` for US lat/lon
ranges and ``open_meteo`` everywhere else. Operators with a paid
OpenWeather key can flip provider explicitly.
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


class WeatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["auto", "open_meteo", "nws", "openweather"] = Field(
        default="auto"
    )
    api_key_secret: str = Field(default="openweather_key", max_length=128)
    default_units: Literal["metric", "imperial"] = Field(default="metric")
    user_agent: str = Field(
        default="spark-agent/0.1",
        max_length=256,
        description="Required by NWS ToS.",
    )
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    verify_ssl: bool = True


class _WeatherArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["current", "forecast", "alerts"] = Field(
        description=(
            "'current' (now), 'forecast' (hourly up to 120h / daily "
            "up to 7d), 'alerts' (active weather alerts in area)."
        ),
    )
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    hours: int = Field(default=24, gt=0, le=120)
    days: int = Field(default=3, gt=0, le=7)
    units: Literal["metric", "imperial"] | None = None


class WeatherCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature: float
    feels_like: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    conditions: str | None = None
    cloud_cover: float | None = None
    precipitation: float | None = None
    timestamp: str | None = None


class WeatherForecastEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: str
    temperature: float | None = None
    precipitation: float | None = None
    conditions: str | None = None


class WeatherAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    severity: str | None = None
    description: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None


class WeatherResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    ok: bool
    provider: str | None = None
    units: str | None = None
    current: WeatherCurrent | None = None
    hourly: list[WeatherForecastEntry] | None = None
    daily: list[WeatherForecastEntry] | None = None
    alerts: list[WeatherAlert] | None = None
    error: str | None = None


class WeatherPlugin:
    name: ClassVar[str] = "weather"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Weather data from Open-Meteo, NWS (US), or OpenWeather. "
        "Provider auto-selected from coordinates by default."
    )
    input_schema: ClassVar[type[BaseModel]] = _WeatherArgs
    output_schema: ClassVar[type[BaseModel]] = WeatherResult
    config_schema: ClassVar[type[BaseModel]] = WeatherConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.NET_HTTP, Permission.SECRETS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.LOW
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = True

    async def execute(self, args: _WeatherArgs, ctx: Any) -> WeatherResult:
        cfg = getattr(ctx, "plugin_config", {}) or {}
        provider = (cfg.get("provider") or "auto").strip()
        if provider == "auto":
            provider = _autodetect(args.latitude, args.longitude)
        units = (args.units or cfg.get("default_units") or "metric").strip()

        async with build_client(cfg) as client:
            if provider == "open_meteo":
                return await _do_open_meteo(client, args, units)
            if provider == "nws":
                return await _do_nws(client, args, units, cfg)
            if provider == "openweather":
                key = resolve_secret(
                    cfg,
                    config_key="api_key_secret",
                    default_secret_name="openweather_key",
                    plugin_name="weather",
                    ctx=ctx,
                )
                return await _do_openweather(client, args, units, key)
            raise SparkError(
                ErrorCode.INPUT_SCHEMA_INVALID,
                f"weather: unknown provider {provider!r}",
                detail={"plugin": "weather", "provider": provider},
            )


def _autodetect(lat: float, lon: float) -> str:
    """Pick nws for US coords (rough box), open_meteo otherwise."""
    if 24.0 <= lat <= 50.0 and -125.0 <= lon <= -66.0:
        return "nws"
    return "open_meteo"


async def _do_open_meteo(
    client: httpx.AsyncClient, args: _WeatherArgs, units: str
) -> WeatherResult:
    url = "https://api.open-meteo.com/v1/forecast"
    params: dict[str, Any] = {
        "latitude": args.latitude,
        "longitude": args.longitude,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,cloud_cover,precipitation,weather_code",
        "temperature_unit": "fahrenheit" if units == "imperial" else "celsius",
        "wind_speed_unit": "mph" if units == "imperial" else "kmh",
        "timezone": "auto",
    }
    if args.action == "forecast":
        params["hourly"] = "temperature_2m,precipitation,weather_code"
        params["forecast_hours"] = args.hours
        params["daily"] = "temperature_2m_max,temperature_2m_min,precipitation_sum"
        params["forecast_days"] = args.days
    try:
        resp = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="weather") from exc
    if resp.status_code >= 400:
        return WeatherResult(action=args.action, ok=False, error=resp.text[:200])
    data = resp.json()
    cur = data.get("current", {})
    result = WeatherResult(
        action=args.action,
        ok=True,
        provider="open_meteo",
        units=units,
        current=WeatherCurrent(
            temperature=float(cur.get("temperature_2m", 0)),
            humidity=cur.get("relative_humidity_2m"),
            wind_speed=cur.get("wind_speed_10m"),
            wind_direction=cur.get("wind_direction_10m"),
            cloud_cover=cur.get("cloud_cover"),
            precipitation=cur.get("precipitation"),
            timestamp=cur.get("time"),
        ),
    )
    if args.action == "forecast":
        h = data.get("hourly") or {}
        times = h.get("time", []) or []
        temps = h.get("temperature_2m", []) or []
        precs = h.get("precipitation", []) or []
        result.hourly = [
            WeatherForecastEntry(
                timestamp=str(times[i]),
                temperature=temps[i] if i < len(temps) else None,
                precipitation=precs[i] if i < len(precs) else None,
            )
            for i in range(len(times))
        ]
        d = data.get("daily") or {}
        d_times = d.get("time", []) or []
        d_max = d.get("temperature_2m_max", []) or []
        d_prec = d.get("precipitation_sum", []) or []
        result.daily = [
            WeatherForecastEntry(
                timestamp=str(d_times[i]),
                temperature=d_max[i] if i < len(d_max) else None,
                precipitation=d_prec[i] if i < len(d_prec) else None,
            )
            for i in range(len(d_times))
        ]
    return result


async def _do_nws(
    client: httpx.AsyncClient, args: _WeatherArgs, units: str, cfg: dict[str, Any]
) -> WeatherResult:
    """NWS requires a two-step: /points → /forecast (or /alerts/active)."""
    ua = cfg.get("user_agent") or "spark-agent/0.1"
    headers = {"User-Agent": ua, "Accept": "application/geo+json"}
    points_url = f"https://api.weather.gov/points/{args.latitude},{args.longitude}"
    try:
        pt = await client.get(points_url, headers=headers)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=points_url, plugin_name="weather") from exc
    if pt.status_code >= 400:
        return WeatherResult(action=args.action, ok=False, error=pt.text[:200])
    props = pt.json().get("properties") or {}

    if args.action == "alerts":
        url = f"https://api.weather.gov/alerts/active?point={args.latitude},{args.longitude}"
        a = await client.get(url, headers=headers)
        if a.status_code >= 400:
            return WeatherResult(action=args.action, ok=False, error=a.text[:200])
        alerts = []
        for feat in a.json().get("features", []) or []:
            p = feat.get("properties") or {}
            alerts.append(
                WeatherAlert(
                    title=p.get("headline") or p.get("event") or "alert",
                    severity=p.get("severity"),
                    description=p.get("description"),
                    starts_at=p.get("onset"),
                    ends_at=p.get("ends"),
                )
            )
        return WeatherResult(
            action="alerts", ok=True, provider="nws", units=units, alerts=alerts
        )

    fc_url = props.get("forecastHourly" if args.action == "forecast" else "forecast")
    if not fc_url:
        return WeatherResult(action=args.action, ok=False, error="no forecast URL")
    fc = await client.get(fc_url, headers=headers)
    if fc.status_code >= 400:
        return WeatherResult(action=args.action, ok=False, error=fc.text[:200])
    periods = (fc.json().get("properties") or {}).get("periods") or []
    if not periods:
        return WeatherResult(action=args.action, ok=False, error="no periods")
    first = periods[0]
    result = WeatherResult(
        action=args.action,
        ok=True,
        provider="nws",
        units=units,
        current=WeatherCurrent(
            temperature=float(first.get("temperature", 0)),
            wind_speed=_parse_speed(first.get("windSpeed")),
            wind_direction=_dir_to_deg(first.get("windDirection")),
            conditions=first.get("shortForecast"),
            timestamp=first.get("startTime"),
        ),
    )
    if args.action == "forecast":
        hourly = [
            WeatherForecastEntry(
                timestamp=p.get("startTime", ""),
                temperature=p.get("temperature"),
                conditions=p.get("shortForecast"),
            )
            for p in periods[: args.hours]
        ]
        result.hourly = hourly
    return result


def _parse_speed(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.split()[0])
    except (ValueError, IndexError):
        return None


def _dir_to_deg(d: str | None) -> float | None:
    if not d:
        return None
    table = {
        "N": 0,
        "NNE": 22.5,
        "NE": 45,
        "ENE": 67.5,
        "E": 90,
        "ESE": 112.5,
        "SE": 135,
        "SSE": 157.5,
        "S": 180,
        "SSW": 202.5,
        "SW": 225,
        "WSW": 247.5,
        "W": 270,
        "WNW": 292.5,
        "NW": 315,
        "NNW": 337.5,
    }
    return table.get(d.upper())


async def _do_openweather(
    client: httpx.AsyncClient,
    args: _WeatherArgs,
    units: str,
    key: str,
) -> WeatherResult:
    if args.action == "alerts":
        url = "https://api.openweathermap.org/data/3.0/onecall"
    else:
        url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": args.latitude,
        "lon": args.longitude,
        "appid": key,
        "units": units,
    }
    try:
        resp = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise classify_connect_error(exc, url=url, plugin_name="weather") from exc
    if resp.status_code == 401:
        raise SparkError(
            ErrorCode.SECRET_NOT_FOUND,
            "weather: openweather key rejected",
            detail={"plugin": "weather", "secret_name": "openweather_key"},
        )
    if resp.status_code >= 400:
        return WeatherResult(action=args.action, ok=False, error=resp.text[:200])
    data = resp.json()
    main = data.get("main") or {}
    wind = data.get("wind") or {}
    return WeatherResult(
        action=args.action,
        ok=True,
        provider="openweather",
        units=units,
        current=WeatherCurrent(
            temperature=float(main.get("temp", 0)),
            feels_like=main.get("feels_like"),
            humidity=main.get("humidity"),
            wind_speed=wind.get("speed"),
            wind_direction=wind.get("deg"),
            cloud_cover=(data.get("clouds") or {}).get("all"),
            conditions=(data.get("weather") or [{}])[0].get("description"),
            timestamp=data.get("dt"),
        ),
    )
