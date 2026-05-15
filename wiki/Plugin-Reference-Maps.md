# Plugin: `maps`

Geocoding (address → lat/lon), reverse geocoding (lat/lon →
address), and distance / routing. Provider selector:

- **OSM Nominatim** — free, fair-use rate-limit (default; requires a
  User-Agent header per Nominatim ToS, set in config)
- **Mapbox** — paid (free tier available); needs API key
- **Google** — paid; needs API key

For routing / distance, the Nominatim provider uses **OSRM** public
demo servers (also fair-use). Heavy traffic should run a local OSRM
or upgrade to a paid provider.

| | |
|---|---|
| **Required permissions** | `NET_HTTP`, `SECRETS_READ` |
| **Sensitivity** | `LOW` |
| **Output filtered** | Yes |

## Actions

| Action | Inputs | Returns |
|---|---|---|
| `geocode` | `query` (address) | `hits[]` of `{label, lat, lon, address}` |
| `reverse_geocode` | `latitude`, `longitude` | One hit |
| `distance` | `from_lat/lon` + `to_lat/lon` | `distance_meters`, `duration_seconds` (driving) |

## Bootstrap

- **Nominatim** (default): no setup required. Set a meaningful `user_agent` in plugin config (e.g. `spark-agent/yourname`).
- **Mapbox / Google**: get a key, `spark secrets set maps_key`, pick the provider in plugin config.

## Failure surface

| Refusal | Code |
|---|---|
| 401 / `REQUEST_DENIED` (key rejected) | `SPK_E_SECRET_NOT_FOUND` |
| Missing input fields | `SPK_E_INPUT_SCHEMA_INVALID` |
| Network failure | `SPK_E_URL_DENIED` or `SPK_E_URL_PRIVATE_IP` |

## Source

- Plugin: [`spark/plugins/builtins/maps.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/maps.py)
- Tests: [`tests/unit/test_trio_plugins.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_trio_plugins.py)
