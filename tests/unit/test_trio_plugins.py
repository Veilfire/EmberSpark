"""Weather + Wikipedia + Maps — provider-abstracted plugins.

Tests focus on:
- provider selection
- input-validation refusal shapes
- secret resolution for paid providers
- happy-path response shape under mocked httpx

These plugins are smaller than the allowlist-based ones (calendar /
imap / slack) — no checkbox grids, no live discovery; just provider
selection + schema.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import json
import pytest

from spark.errors.codes import ErrorCode, SparkError
from spark.plugins.builtins.maps import MapsPlugin, _MapsArgs
from spark.plugins.builtins.weather import WeatherPlugin, _WeatherArgs, _autodetect
from spark.plugins.builtins.wikipedia import WikipediaPlugin, _WikiArgs


def _ctx(secrets, cfg):
    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.secrets = secrets
    ctx.plugin_config = cfg
    return ctx


def _resp(status: int, body: dict | list):
    return httpx.Response(
        status_code=status, content=json.dumps(body).encode()
    )


def _patch_get(responses_by_url: dict[str, httpx.Response]):
    async def fake(self, method, url, *a, **kw):  # noqa: ANN001
        for needle, resp in responses_by_url.items():
            if needle in url:
                return resp
        return _resp(404, {})
    return patch.object(httpx.AsyncClient, "request", new=fake)


# ===========================================================================
# Wikipedia
# ===========================================================================


@pytest.mark.asyncio
async def test_wiki_search_requires_query():
    plugin = WikipediaPlugin()
    ctx = _ctx({}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_WikiArgs(action="search"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_wiki_summary_requires_title():
    plugin = WikipediaPlugin()
    ctx = _ctx({}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_WikiArgs(action="summary"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_wiki_search_happy_path():
    plugin = WikipediaPlugin()
    ctx = _ctx({}, {})
    body = {
        "query": {
            "search": [
                {
                    "title": "Spark (programming language)",
                    "snippet": "<span>Spark</span> is an open-source agent runtime.",
                    "wordcount": 1234,
                }
            ]
        }
    }
    with _patch_get({"api.php": _resp(200, body)}):
        r = await plugin.execute(_WikiArgs(action="search", query="spark"), ctx)
    assert r.ok is True
    assert len(r.hits) == 1
    assert r.hits[0]["title"] == "Spark (programming language)"
    # HTML stripped from snippet
    assert "<span>" not in r.hits[0]["snippet"]


@pytest.mark.asyncio
async def test_wiki_summary_happy_path():
    plugin = WikipediaPlugin()
    ctx = _ctx({}, {})
    body = {
        "title": "Python (programming language)",
        "extract": "Python is a high-level programming language…",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python"}},
    }
    with _patch_get({"page/summary": _resp(200, body)}):
        r = await plugin.execute(
            _WikiArgs(action="summary", title="Python"), ctx
        )
    assert r.ok is True
    assert "Python" in r.extract
    assert r.url and "wikipedia" in r.url


@pytest.mark.asyncio
async def test_wiki_summary_404_graceful():
    plugin = WikipediaPlugin()
    ctx = _ctx({}, {})
    with _patch_get({"page/summary": _resp(404, {})}):
        r = await plugin.execute(
            _WikiArgs(action="summary", title="Fakeoseur"), ctx
        )
    assert r.ok is False
    assert "not found" in (r.error or "")


# ===========================================================================
# Weather
# ===========================================================================


def test_weather_autodetect_us_picks_nws():
    assert _autodetect(40.7, -74.0) == "nws"  # NYC


def test_weather_autodetect_non_us_picks_open_meteo():
    assert _autodetect(51.5, -0.12) == "open_meteo"  # London


@pytest.mark.asyncio
async def test_weather_open_meteo_current():
    plugin = WeatherPlugin()
    ctx = _ctx({}, {"provider": "open_meteo"})
    body = {
        "current": {
            "time": "2026-05-09T12:00",
            "temperature_2m": 18.5,
            "relative_humidity_2m": 60,
            "wind_speed_10m": 12.0,
            "wind_direction_10m": 180,
            "cloud_cover": 40,
            "precipitation": 0,
            "weather_code": 0,
        }
    }
    with _patch_get({"api.open-meteo.com": _resp(200, body)}):
        r = await plugin.execute(
            _WeatherArgs(action="current", latitude=51.5, longitude=-0.12),
            ctx,
        )
    assert r.ok is True
    assert r.provider == "open_meteo"
    assert r.current.temperature == 18.5


@pytest.mark.asyncio
async def test_weather_openweather_requires_key():
    plugin = WeatherPlugin()
    ctx = _ctx({}, {"provider": "openweather"})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _WeatherArgs(action="current", latitude=51.5, longitude=-0.12),
            ctx,
        )
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND
    assert exc.value.detail["secret_name"] == "openweather_key"


@pytest.mark.asyncio
async def test_weather_openweather_401_maps_to_secret_not_found():
    plugin = WeatherPlugin()
    ctx = _ctx(
        {"openweather_key": "bad"}, {"provider": "openweather"}
    )
    with _patch_get({"openweathermap": _resp(401, {})}):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _WeatherArgs(action="current", latitude=51.5, longitude=-0.12),
                ctx,
            )
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND


# ===========================================================================
# Maps
# ===========================================================================


@pytest.mark.asyncio
async def test_maps_geocode_requires_query():
    plugin = MapsPlugin()
    ctx = _ctx({}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_MapsArgs(action="geocode"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_maps_reverse_requires_coords():
    plugin = MapsPlugin()
    ctx = _ctx({}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(_MapsArgs(action="reverse_geocode"), ctx)
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_maps_distance_requires_four_coords():
    plugin = MapsPlugin()
    ctx = _ctx({}, {})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _MapsArgs(
                action="distance", from_latitude=40.0, from_longitude=-74.0
            ),
            ctx,
        )
    assert exc.value.code is ErrorCode.INPUT_SCHEMA_INVALID


@pytest.mark.asyncio
async def test_maps_nominatim_geocode_happy_path():
    plugin = MapsPlugin()
    ctx = _ctx({}, {"provider": "osm_nominatim"})
    body = [
        {
            "display_name": "New York, NY, USA",
            "lat": "40.7128",
            "lon": "-74.0060",
            "address": {"city": "New York", "country": "USA"},
        }
    ]
    with _patch_get({"nominatim": _resp(200, body)}):
        r = await plugin.execute(
            _MapsArgs(action="geocode", query="New York"), ctx
        )
    assert r.ok is True
    assert r.provider == "osm_nominatim"
    assert len(r.hits) == 1
    assert "New York" in r.hits[0].label


@pytest.mark.asyncio
async def test_maps_mapbox_requires_key():
    plugin = MapsPlugin()
    ctx = _ctx({}, {"provider": "mapbox"})
    with pytest.raises(SparkError) as exc:
        await plugin.execute(
            _MapsArgs(action="geocode", query="NYC"), ctx
        )
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND


@pytest.mark.asyncio
async def test_maps_mapbox_401_maps_to_secret_not_found():
    plugin = MapsPlugin()
    ctx = _ctx({"maps_key": "bad"}, {"provider": "mapbox"})
    with _patch_get({"mapbox.com": _resp(401, {})}):
        with pytest.raises(SparkError) as exc:
            await plugin.execute(
                _MapsArgs(action="geocode", query="NYC"), ctx
            )
    assert exc.value.code is ErrorCode.SECRET_NOT_FOUND
