# Daemon Modes

EmberSpark can run three ways as a long-running service. This page summarizes them; for the full reference and installation details, see [Deployment Guide](Deployment-Guide) and [docs/deployment.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/deployment.md).

## TL;DR

| Mode | Best for | Isolation level |
|---|---|---|
| **naked** | Dev, single trusted laptop | EmberSpark sandbox + systemd/launchd hardening |
| **docker** | Servers, repeatable builds | Container + EmberSpark sandbox |
| **firecracker** | Hostile or shared environments | MicroVM + container-like + EmberSpark sandbox |

## How to pick

Start with `naked`. If you need container isolation or repeatable builds, move to `docker`. If you need hardware-backed isolation or have strict multi-tenant requirements, move to `firecracker`. Most operators never leave `naked`.

## CLI

```bash
spark daemon install     # reads spec.daemon from ~/.spark/spark.yaml
spark daemon start
spark daemon status
spark daemon stop
spark daemon uninstall
```

The same commands work for all three modes; the dispatcher picks the backend based on `spec.daemon.mode`.

## Further reading

- [Deployment Guide](Deployment-Guide) — full comparison and operator workflows
- [Installation](Installation) — prerequisites
