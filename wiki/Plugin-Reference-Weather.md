# Plugin: `weather`

Current weather, forecast, and active alerts via Open-Meteo (global,
free), NWS (US official, free), or OpenWeather (global, paid). The
`auto` provider picks NWS for US coordinates and Open-Meteo
elsewhere; operators can pin explicitly.

| | |
|---|---|
| **Required permissions** | `NET_HTTP`, `SECRETS_READ` |
| **Sensitivity** | `LOW` |
| **Output filtered** | Yes |

## Actions

| Action | What it returns |
|---|---|
| `current` | Temperature, humidity, wind, cloud cover, precipitation, conditions |
| `forecast` | Hourly (up to 120h) + daily (up to 7d) — temperature, precipitation, conditions |
| `alerts` | Active weather alerts in the area (severity, time window, description) |

## Bootstrap

- **No key needed** for `open_meteo` or `nws` — defaults work out of the box.
- For `openweather`: get a key at openweathermap.org/api → `spark secrets set openweather_key`.
- Plugins page → weather → pick provider + units (metric / imperial).

## Failure surface

| Refusal | Code |
|---|---|
| 401 (openweather key rejected) | `SPK_E_SECRET_NOT_FOUND` |
| Provider unsupported action | `SPK_E_INPUT_SCHEMA_INVALID` |
| Network failure | `SPK_E_URL_DENIED` or `SPK_E_URL_PRIVATE_IP` |

## Source

- Plugin: [`spark/plugins/builtins/weather.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/weather.py)
- Tests: [`tests/unit/test_trio_plugins.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_trio_plugins.py)
