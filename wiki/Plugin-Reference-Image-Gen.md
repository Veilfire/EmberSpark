# Plugin Reference: `image_gen`

Provider-agnostic image generation (OpenAI / Stability / Replicate). Generated images land in the data volume's deliverables directory, where the **Downloads** page surfaces them and the notification bell fires a `download_ready` event.

- **Required permissions:** `net.http`, `secrets.read`, `fs.write`
- **Required secrets:** one — the provider API key
- **Sensitivity:** `MODERATE`
- **Network:** required
- **Depends on:** a populated **data volume** (`spec.data_volume.enabled: true` in `~/.spark/spark.yaml`). The plugin refuses to run if `ctx.deliverables_path` is `None`.

---

## Why this plugin exists

Image generation is a common agent workflow: "generate a hero image for my blog post." You *could* do it with `http_client` against the OpenAI API — but you'd then have to:

1. Decode the base64 image payload manually.
2. Pick a filename.
3. Write the bytes to disk via `filesystem` or `markdown_writer`.
4. Surface the file to the user somehow.

`image_gen` does all four in one call. The generated file lands in `deliverables/<subdirectory>/<uuid>.<ext>`, a notification fires, and the user sees it in Downloads.

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `openai` \| `stability` \| `replicate` | `openai` | Which provider's image API to call. |
| `api_key_secret` | string | `image_gen_key` | Keyring secret for the provider API key. |
| `default_model` | string | `dall-e-3` | Per-call can override. |
| `default_size` | `512x512` \| `1024x1024` \| `1792x1024` \| `1024x1792` | `1024x1024` | |
| `max_prompt_chars` | int | `4000` | |
| `max_images_per_call` | int | `4` | |
| `output_format` | `png` \| `webp` \| `jpeg` | `png` | File extension for the output. |
| `connect_timeout_seconds` | float | `10.0` | |
| `read_timeout_seconds` | float | `60.0` | Image generation is slow; the default is generous. |
| `subdirectory` | string | `generated` | Subdirectory inside `deliverables_path` where images are written. |

---

## What the model sends per call

```json
{
  "prompt": "A minimalist line drawing of a fox on a tree stump at dawn",
  "n": 1,
  "size": "1024x1024"
}
```

Returns:

```json
{
  "provider": "openai",
  "model": "dall-e-3",
  "prompt": "A minimalist line drawing of a fox on a tree stump at dawn",
  "image_count": 1,
  "images": [
    {
      "path": "/data/spark-volume/deliverables/generated/7a3b...c2.png",
      "filename": "7a3b...c2.png",
      "size_bytes": 482_311,
      "provider_id": null
    }
  ]
}
```

---

## Operator workflow

**Store the API key:**

```bash
spark secrets set image_gen_key     # prompts for value (no echo)
```

**Pick a provider:**

- **OpenAI** — DALL-E 3 is the default. High quality, `b64_json` response, works out of the box.
- **Stability** — SDXL 1.0. Cheaper, more control over parameters, `base64` response.
- **Replicate** — *not supported in v1*. Replicate uses async predictions that require polling, which this plugin does not implement. Use OpenAI or Stability.

**Enable the data volume.** `image_gen` is the first plugin that needs the data volume to be enabled. If `spec.data_volume.enabled: false` in your `SparkRuntime` config, the plugin refuses to run.

**Typical config:**

```json
{
  "provider": "openai",
  "api_key_secret": "openai_key",
  "default_model": "dall-e-3",
  "default_size": "1024x1024",
  "max_images_per_call": 2,
  "subdirectory": "generated"
}
```

**Pair with the notification bell.** Every image written to `deliverables/` fires a `DOWNLOAD_READY` notification. Operators see it in the bell badge and can click through to the Downloads page to grab the file.

---

## Common pitfalls

- **Data volume disabled** — `ctx.deliverables_path` is `None`; the plugin raises `PermissionError` with a clear message.
- **OpenAI URL-mode response** — DALL-E can return either `b64_json` or a URL. This plugin only supports `b64_json` to avoid a second network hop to a non-allowlisted CDN. The plugin explicitly asks for `response_format: b64_json`.
- **Replicate** — refused with a clear error. Use a different provider or wait for async polling support.
- **Prompt too long** — refused before the HTTP call.

---

## Further reading

- [Downloads (Web UI Guide)](Web-UI-Guide#downloads) — where generated images surface
- [Notification bell](Web-UI-Guide) — fires `download_ready` for each new file
- [Data volume (Deployment Guide)](Deployment-Guide) — enabling the deliverables directory
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
