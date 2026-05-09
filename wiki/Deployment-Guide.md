# Deployment Guide

EmberSpark supports three bind modes and three daemon modes. This page is the operator guide to picking the right combination.

For the source-level reference, see [docs/deployment.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/deployment.md).

---

## Bind modes

The web UI bind mode is set in `~/.spark/spark.yaml` under `spec.web.bind`. Three options:

### `loopback` — the default

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: loopback
      host: 127.0.0.1
      port: 7777
```

The kernel rejects any non-loopback source IP at the socket layer. No CIDR middleware needed. This is the safest option and the right choice for a single laptop.

### `lan` — for household / office network access

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: lan
      host: 0.0.0.0
      port: 7777
      allowed_cidrs:
        - 192.168.1.0/24
      trusted_proxies: []
```

Binds to all interfaces but enforces a source-IP allowlist at the middleware layer. Source IPs outside `allowed_cidrs` get a 403.

`allowed_cidrs` is **required** for LAN mode — the validator refuses an empty list. Use specific subnets like `192.168.1.0/24` or `10.0.0.0/8`, never `0.0.0.0/0`.

`trusted_proxies` is only for when you sit EmberSpark behind a reverse proxy (nginx, Caddy) that forwards `X-Forwarded-For`. If the request comes from one of these proxies, the CIDR check reads the leftmost XFF entry (and re-validates it as a real IP to prevent spoofed headers).

### `public` — for internet-accessible deployments

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: public
      host: 203.0.113.10
      port: 443
      allowed_cidrs:
        - 203.0.113.0/24
      tls:
        cert_file: ~/certs/spark.crt
        key_file: ~/certs/spark.key
```

TLS is **mandatory** for public mode. The config validator refuses to load without `cert_file` and `key_file`, and fails if the files don't exist at load time.

`uvicorn` terminates TLS directly. If you'd rather have nginx / Caddy / Traefik handle TLS, use `lan` mode with `trusted_proxies` pointing at the proxy and let the proxy do the internet-facing bit.

Even in public mode, **consider WireGuard or Tailscale first.** A public EmberSpark bind is something you should only do if you really know what you're doing and can't achieve the same thing with an overlay network.

---

## Daemon modes

Bind mode is "how do external clients reach EmberSpark?" Daemon mode is "how does EmberSpark run on the host?" Three options:

### `naked` — venv + systemd / launchd

The simplest. Runs EmberSpark in a Python venv, managed by your OS's service manager.

```yaml
spec:
  daemon:
    mode: naked
    service_name: spark
    restart_on_failure: true
    log_dir: ~/.spark/daemon-logs
```

Install:

```bash
spark daemon install
spark daemon start
spark daemon status
```

On Linux, this writes a systemd user unit with hardening directives:

- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=read-only` (but `ReadWritePaths=~/.spark`)
- `MemoryDenyWriteExecute=true`
- `RestrictSUIDSGID=true`
- `SystemCallArchitectures=native`

On macOS, it writes a launchd plist that keeps EmberSpark alive and logs to the configured directory.

**Isolation: EmberSpark's own sandbox + the service manager's hardening.** No container boundary. Use when you own the box and trust yourself.

### `docker` — container with a state volume

```yaml
spec:
  daemon:
    mode: docker
    image: spark-runtime:latest
    container_name: spark
    memory_mb: 2048
    cpus: 2.0
```

Two compose files exist:

- **[`docker-compose.yaml`](../docker-compose.yaml)** (project root) — ready-to-use quick-start with a pre-baked LAN config at [`deploy/docker/spark.yaml`](../deploy/docker/spark.yaml). Use with `docker compose up`. Credentials print to the console on startup.
- **`spark/daemon/docker/docker-compose.yml`** — parameterized, used by `spark daemon install` for programmatic deployments. Requires env-var overrides.

`spark daemon install` runs `docker compose build` + `docker compose up -d` using the inner compose file. The image:

- Is a multi-stage build on `python:3.12-slim-bookworm`
- Has Bubblewrap + tini + EmberSpark installed
- Runs as a non-root user (UID 1000)
- Has a `/data/spark` volume for state
- Healthcheck against `/api/health`
- Runs with `cap_drop: ALL` plus `security_opt: [seccomp=unconfined, apparmor=unconfined]` so unprivileged user namespaces can be created for the inner Bubblewrap. (`SYS_ADMIN` is not needed and used to actively break things — bwrap detects unexpected ambient caps and aborts with `Unexpected capabilities but not setuid`.)
- Bubblewrap auto-detects nested user-namespace mode via `/run/.containerenv` / `/.dockerenv` and bind-mounts `/proc` + `/dev` instead of trying to mount fresh procfs/devtmpfs (which the kernel refuses inside a nested userns).

**Isolation: container boundary + EmberSpark's sandbox.** Use when you want:

- Repeatable builds
- Easy upgrade (rebuild the image)
- Running on a server that also runs other things
- Not polluting the host with Python packages

Windows hosts can run the container, but the EmberSpark code inside still only works on Linux containers.

### `firecracker` — microVM

```yaml
spec:
  daemon:
    mode: firecracker
    firecracker_binary: /usr/local/bin/firecracker
    rootfs: ~/.spark/firecracker/rootfs.ext4
    kernel: ~/.spark/firecracker/vmlinux
    vcpus: 2
    memory_mib: 1024
    tap_device: spark-tap0
    host_cidr: 192.168.241.1/30
    guest_ip: 192.168.241.2
    forwarded_ports:
      7777: 7777
```

The strongest isolation. Each EmberSpark process lives inside its own microVM with a separate Linux kernel, separate memory, and a separate syscall interface.

Prerequisites:

- Linux host with KVM (`/dev/kvm` accessible)
- Root on the host (for TAP + iptables)
- Firecracker binary on PATH
- A debootstrap'd ext4 rootfs (build script provided at `spark/daemon/firecracker/build-rootfs.sh`)
- A matching `vmlinux` kernel image

Install:

```bash
sudo OUT=~/.spark/firecracker/rootfs.ext4 \
  SPARK_REPO=$(pwd) \
  spark/daemon/firecracker/build-rootfs.sh

sudo spark daemon install
sudo spark daemon start
```

The launcher at `spark/daemon/firecracker/launcher.sh`:

- Creates the TAP device, gives it the host-side IP
- Enables IPv4 forwarding
- Sets up NAT + per-port DNAT rules for `forwarded_ports`
- Execs `firecracker --config-file …`

On exit it cleans up iptables. See [docs/deployment.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/deployment.md) for the full invocation and caveats.

**Use when:**

- You're running untrusted third-party plugins
- You share the host with other workloads you care about
- You want hardware-backed isolation beyond what the OS sandbox provides

**Don't use when:**

- You just want EmberSpark to run on your laptop (use `naked`)
- You're on macOS (Firecracker is Linux-only)
- You're on a host without KVM

---

## Mode comparison

| | naked | docker | firecracker |
|---|---|---|---|
| Platforms | Linux, macOS | Linux, macOS, Windows (host) | Linux only |
| Root needed | no | no | yes |
| Install footprint | venv only | Docker daemon + image | KVM + firecracker + rootfs |
| Startup time | ~1s | ~3s | ~5s |
| Isolation | EmberSpark sandbox only | container + EmberSpark sandbox | microVM + container-like + EmberSpark sandbox |
| Good for | dev, single trusted host | servers, repeatable builds | hostile or shared hosts |

Windows is listed under `docker` because you can *run* the container on a Windows host via Docker Desktop — but the container itself is Linux and the mandatory OS sandbox runs inside the container.

---

## Credentials across daemon modes

Regardless of daemon mode, the web UI credentials are handled the same way:

1. On startup, EmberSpark generates a unique username (`<dictionary word><4 digits>`) and a 16-character password (two words, digits, special, exactly one uppercase).
2. The password is bcrypt-hashed at 13 rounds and written to `~/.spark/web-credentials.json` (mode 0600).
3. The cleartext is **displayed once** to stderr.

**In daemon modes where stderr goes to a log file**, you need to look at the log immediately after starting:

```bash
# naked mode, Linux
journalctl --user -u spark -n 50 | grep -A 10 "DISPLAYED ONCE"

# naked mode, macOS
tail -50 ~/.spark/daemon-logs/spark.stderr.log

# docker mode (root-level docker-compose.yaml)
docker compose logs spark 2>&1 | grep -A 10 "DISPLAYED ONCE"

# docker mode (programmatic via spark daemon install)
docker logs spark 2>&1 | grep -A 10 "DISPLAYED ONCE"

# firecracker mode
sudo journalctl -u spark -n 30   # on the guest, or via serial console
```

Once you've logged in with those credentials, you don't need them again for that session. If you lose them, stop the daemon, run `spark serve --rotate-credentials` interactively to see new credentials, then re-start the daemon (or flip `credentials.rotate_on_startup: true` in spark.yaml and restart the daemon — it'll mint new ones on next boot).

---

## Choosing a combination

| You want... | Bind + daemon |
|---|---|
| Run EmberSpark on my laptop for myself | `loopback` + `naked` |
| Access EmberSpark from my phone on the home WiFi | `lan` + `naked` |
| Run EmberSpark on a home server that my family accesses | `lan` + `docker` |
| EmberSpark on a VPS for a small team on the same VPN | `loopback` (with Tailscale routing) + `docker` |
| EmberSpark on a public VPS | `public` + `docker` with TLS certs (or a reverse proxy) |
| Running third-party plugins I don't fully trust | `loopback` + `firecracker` |
| Shared bare-metal host with strict isolation requirements | `loopback` + `firecracker` |

---

## Further reading

- [docs/deployment.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/deployment.md) — full source reference
- [Daemon Modes](Daemon-Modes) — same material, UI-side perspective
- [Installation](Installation) — prerequisites and system packages
- [Web UI Guide](Web-UI-Guide) — what to do after you've deployed
