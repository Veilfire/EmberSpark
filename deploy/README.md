# deploy/

Pre-baked deployment configs for running EmberSpark on your LAN.
Both target the same setup: LAN bind on `0.0.0.0:7777`, 192.168.0.0/16
allowlist, no TLS, credentials rotated and printed on every startup.

## Docker Compose (recommended)

```bash
# From the project root:
docker compose up          # credentials print to console
```

Config: [`docker/spark.yaml`](docker/spark.yaml) — mounted into the
container at `/etc/spark/spark.yaml` by the root-level
[`docker-compose.yaml`](../docker-compose.yaml).

Edit `docker/spark.yaml` to narrow `allowed_cidrs` to your specific
subnet (e.g. `192.168.1.0/24`).

## Firecracker microVM

Requires Linux with KVM, root, and the Firecracker binary. Multi-step
build process — see [`firecracker/README.md`](firecracker/README.md).

Config: [`firecracker/spark.yaml`](firecracker/spark.yaml) — same as
Docker but with `data_volume.root: /mnt/spark-data` (the ext4 data
image mounted inside the guest) and the TAP subnet in `allowed_cidrs`.

## Viewing credentials

| Mode | Command |
|---|---|
| Docker (foreground) | Printed to console during `docker compose up` |
| Docker (background) | `docker compose logs spark 2>&1 \| grep -A 10 "DISPLAYED ONCE"` |
| Firecracker | `sudo journalctl -u spark -n 30` on the guest |

If you miss the credentials, restart the container — `rotate_on_startup: true`
mints a fresh pair every boot.
