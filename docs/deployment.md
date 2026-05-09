# EmberSpark Deployment Guide

## Quick start — Docker Compose on LAN

The fastest path to a running EmberSpark with a web UI accessible on your
local network:

```bash
docker compose up                 # builds the image + starts — credentials print to console
```

That's it. The root-level [`docker-compose.yaml`](../docker-compose.yaml)
builds the image, mounts a pre-baked LAN config from
[`deploy/docker/spark.yaml`](../deploy/docker/spark.yaml) (192.168.0.0/16
allowlist, no TLS), and runs `spark serve`. Credentials are printed to
the console on startup — look for the banner near the end of the output:

```
============================================================
  Spark web UI — credentials (DISPLAYED ONCE; save them now)
============================================================
  URL:      http://0.0.0.0:7777
  Username: sparrow1234
  Password: tree-song77@Moon
============================================================
```

After the first boot, run in background mode:

```bash
docker compose up -d              # detached
docker compose logs -f spark      # view logs (credentials appear on restart)
```

To narrow the CIDR to your specific subnet, edit
`deploy/docker/spark.yaml` and change `allowed_cidrs`.

For a Firecracker microVM deployment, see
[`deploy/firecracker/README.md`](../deploy/firecracker/README.md).

---

## Configuration reference

Every EmberSpark runtime-wide setting — the web UI, the bind mode, and the
daemonization strategy — lives in a single YAML file: `~/.spark/spark.yaml`.
If the file is absent, EmberSpark still runs (one-shot tasks, CLI flows), but the
web UI stays **disabled**. You must explicitly opt in.

```bash
spark config init                 # writes ~/.spark/spark.yaml (example, web disabled)
${EDITOR:-vi} ~/.spark/spark.yaml # flip web.enabled and pick a bind mode
spark config show                 # print the parsed config as JSON
```

## Web UI bind modes

Three exclusive modes — pick exactly one.

### 1. Loopback (default, safest)

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: loopback
      host: 127.0.0.1
      port: 7777
    credentials:
      rotate_on_startup: true
```

- The kernel rejects non-loopback source IPs at the socket layer; no CIDR middleware needed.
- Good for: your own laptop, a development workstation.

### 2. LAN (RFC1918 allowlist for home / office network)

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: lan
      host: 0.0.0.0          # or a specific LAN IP
      port: 7777
      allowed_cidrs:
        - 192.168.1.0/24     # your home subnet
        - 10.0.0.0/8
      trusted_proxies: []    # optional; only if you sit behind a reverse proxy
    credentials:
      rotate_on_startup: true
```

- The `CidrAllowlistMiddleware` rejects any source IP outside `allowed_cidrs` with a 403.
- `trusted_proxies` is the **only** way `X-Forwarded-For` is honored. If empty, we always use the raw client address.
- Good for: another machine on your home network; a small office LAN.

### 3. Public (requires TLS)

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: public
      host: 203.0.113.10     # your public IP or 0.0.0.0
      port: 7777
      allowed_cidrs:
        - 203.0.113.0/24     # optionally narrow even further
      tls:
        cert_file: ~/certs/spark.crt
        key_file:  ~/certs/spark.key
    credentials:
      rotate_on_startup: true
```

- `tls` is mandatory — the schema validator refuses to load a `public` bind without it.
- `uvicorn` terminates TLS directly. If you want a real reverse proxy, put nginx/Caddy in front and use `mode: lan` with `trusted_proxies` pointing at the proxy address.
- Good for: you really know what you're doing. Otherwise prefer `lan` + Tailscale / WireGuard.

## Credentials

On each startup, EmberSpark:

1. Generates a unique username: `<dictionary_word><4_digits>` (e.g. `sparrow1234`).
2. Generates a 16-character password: two short words + digits + one special + exactly one uppercase letter (e.g. `tree-song77@Moon`).
3. Hashes the password with `bcrypt` (12 rounds) and writes the hash to `~/.spark/web-credentials.json` (mode 0600). The cleartext is never written anywhere.
4. Prints both to `stderr` **once**.

```
============================================================
  Spark web UI — credentials (DISPLAYED ONCE; save them now)
============================================================
  URL:      http://127.0.0.1:7777
  Username: sparrow1234
  Password: tree-song77@Moon
============================================================
```

If you miss it, rotate:

```bash
spark serve --rotate-credentials
```

`rotate_on_startup: true` (default) mints fresh creds on every `spark serve`. Flip it to `false` if you prefer sticky credentials that persist across restarts.

**A headless fallback token** (`~/.spark/web-token`) is kept for scripts and API clients — pass it as the `x-spark-token` header. The token is not displayed; it lives at mode 0600 on disk.

## Daemonization

Add a `spec.daemon` block to the same YAML, then run `spark daemon install`.

### Mode 1: `naked` — venv + systemd / launchd

```yaml
spec:
  daemon:
    mode: naked
    service_name: spark
    restart_on_failure: true
    log_dir: ~/.spark/daemon-logs
```

- Linux: generates `~/.config/systemd/user/spark.service` with hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `MemoryDenyWriteExecute`, filtered syscalls).
- macOS: generates `~/Library/LaunchAgents/dev.veilfire.spark.plist`.
- **Isolation:** only EmberSpark's own sandbox. No process isolation beyond the service manager's hardening.
- **Good when:** you own the box and trust yourself.

Install + start:

```bash
spark daemon install      # writes the unit + enables/loads it
spark daemon start        # systemctl --user start / launchctl start
spark daemon status
spark daemon stop
spark daemon uninstall
```

### Mode 2: `docker` — container with state volume

```yaml
spec:
  daemon:
    mode: docker
    image: spark-runtime:latest
    container_name: spark
    memory_mb: 2048
    cpus: 2.0
```

- Builds a multi-stage image ([spark/daemon/docker/Dockerfile](../spark/daemon/docker/Dockerfile)) on Debian bookworm slim with `bubblewrap`, `tini`, `spark`, and the web dependencies.
- **Two compose files** exist:
  - [`docker-compose.yaml`](../docker-compose.yaml) (project root) — ready-to-use quick-start with a pre-baked LAN config. Use with `docker compose up`.
  - [`spark/daemon/docker/docker-compose.yml`](../spark/daemon/docker/docker-compose.yml) — parameterized, used by `spark daemon install` for programmatic deployments.
- The [entrypoint](../spark/daemon/docker/entrypoint.sh) seeds a default `SparkRuntime` YAML in LAN mode with RFC1918 allowlists on first boot. When the root-level compose file is used, a pre-baked config is mounted via `--config` and the auto-generated one is ignored.
- **Isolation:** container boundaries + EmberSpark's own sandbox.
  - Run with `cap_drop: ALL`. The inner bwrap creates **unprivileged** user namespaces — adding `SYS_ADMIN` (or any other cap) makes bwrap abort with `Unexpected capabilities but not setuid, old file caps config?`.
  - `security_opt: [seccomp=unconfined, apparmor=unconfined]` is still required on default Docker / Podman: the default seccomp profile blocks the `unshare` syscall and AppArmor restricts namespace creation.
  - Inside a nested user namespace (i.e. EmberSpark running in a container), the kernel refuses fresh `/proc` and `/dev` mounts. Bubblewrap auto-detects this via `/run/.containerenv` / `/.dockerenv` and switches to `--ro-bind /proc /proc` + `--dev-bind /dev /dev`. Every other isolation gate (user namespace, UTS, IPC, cgroup, optional net) still applies.
  - For networked plugins, `--ro-bind-try /etc /etc` is added so glibc's resolver finds `/etc/resolv.conf` and OpenSSL finds the system CA bundle.
- **Good when:** you run on a server, want predictable rebuilds, and don't need hardware-level isolation.

Install + start:

```bash
# Quick-start (manual):
docker compose up                 # credentials print to console on first boot
docker compose up -d              # subsequent runs in background
docker compose logs -f spark      # view logs (credentials appear on each restart)

# Programmatic (via daemon command):
spark daemon install      # docker compose build + up -d
spark daemon status       # docker compose ps
spark daemon stop
spark daemon uninstall    # docker compose down --volumes
```

### Mode 3: `firecracker` — microVM

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

- **Prerequisites:** Linux host with KVM (`/dev/kvm` accessible), root privileges for the launcher (TAP + iptables), and the Firecracker binary on PATH. macOS and Windows are **not** supported in this mode.
- Render the rootfs once (needs root + debootstrap):
  ```bash
  sudo OUT=~/.spark/firecracker/rootfs.ext4 \
       SPARK_REPO=$(pwd) \
       spark/daemon/firecracker/build-rootfs.sh
  ```
- Download a matching `vmlinux` kernel image and put it at `kernel:`. See [Firecracker releases](https://github.com/firecracker-microvm/firecracker/releases).
- `spark daemon install` writes `/etc/systemd/system/spark-firecracker.service` + a `vmconfig.json` next to the rootfs. The launcher at [`firecracker/launcher.sh`](../spark/daemon/firecracker/launcher.sh) sets up the TAP device, enables IP forwarding, installs NAT + DNAT rules for the forwarded ports, and execs `firecracker`.
- **Isolation:** kernel-level. Each EmberSpark process lives inside a microVM with its own Linux kernel, separate memory, separate syscall interface. This is the strongest option in this runtime.
- **Good when:** you're running untrusted third-party agents or sharing the host with anything you care about.

```bash
sudo spark daemon install
sudo spark daemon start
sudo spark daemon status
sudo spark daemon stop
sudo spark daemon uninstall
```

## Which mode should I pick?

| | naked | docker | firecracker |
|---|---|---|---|
| Platforms | Linux, macOS | Linux, macOS, Windows* | Linux only |
| Root needed | no | no | yes |
| Install footprint | venv only | Docker daemon | KVM + firecracker + rootfs |
| Startup time | ~1s | ~3s | ~5s |
| Isolation | EmberSpark sandbox only | container + EmberSpark sandbox | microVM + container-like + EmberSpark sandbox |
| Good for | dev, single trusted host | servers, repeatable builds | hostile environments, shared hosts |

\* Windows is out of scope for EmberSpark v1 regardless of daemon mode because the mandatory OS sandbox needs Bubblewrap/Seatbelt.

## Security checklist

- [ ] Running `spark serve` on a laptop you own → **loopback**. Done.
- [ ] Running on a LAN machine for other household devices → **lan** with a specific `192.168.x.0/24` in `allowed_cidrs`. Never `0.0.0.0/0`.
- [ ] Running on a public IP → **public** with `tls.cert_file`/`tls.key_file`. Prefer a real reverse proxy terminating TLS + `mode: lan` with a loopback/internal bind on the EmberSpark side.
- [ ] Credentials rotation on startup is the default. If you flip it off, remember that the stored bcrypt hash is still the source of truth.
- [ ] For the `docker` mode, make sure `spark-state` is on persistent storage or you will lose agent state on container recreate.
- [ ] For the `firecracker` mode, confirm `/dev/kvm` is group-readable by your EmberSpark user, or run the service as root.
