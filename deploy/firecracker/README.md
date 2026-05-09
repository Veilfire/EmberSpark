# Firecracker Deployment

Run EmberSpark inside a Firecracker microVM with kernel-level isolation.
The web UI binds in LAN mode with a `192.168.0.0/16` allowlist, no TLS.

## Prerequisites

- **Linux** host with `/dev/kvm` accessible
- **Root** (TAP device + iptables require it)
- **Firecracker** binary — download from
  [firecracker-microvm/firecracker](https://github.com/firecracker-microvm/firecracker/releases)
- **debootstrap** and **e2fsprogs** (for building the rootfs)
- A compatible **vmlinux** kernel image (grab one from the Firecracker
  releases page)

## 1. Build the rootfs

The build script creates a minimal Debian rootfs with Spark installed
and a companion ext4 data image for persistent state.

To use this deployment's narrowed config (192.168.0.0/16 only), replace
the heredoc in `build-rootfs.sh` with a copy of the config file before
building:

```bash
# From the project root:
cp deploy/firecracker/spark.yaml /tmp/spark-firecracker.yaml

# Edit build-rootfs.sh lines 127-153: replace the heredoc with:
#   cp /tmp/spark-firecracker.yaml /root/.spark/spark.yaml
# Or simply rebuild and replace the config on the data image afterward.

sudo OUT=~/.spark/firecracker/rootfs.ext4 \
     SPARK_REPO="$(pwd)" \
     spark/daemon/firecracker/build-rootfs.sh
```

## 2. Place the kernel

```bash
# Example: Firecracker v1.7 kernel
curl -fSL -o ~/.spark/firecracker/vmlinux \
  https://github.com/firecracker-microvm/firecracker/releases/download/v1.7.0/vmlinux-5.10.217
```

## 3. Install via the daemon command

```bash
# Writes /etc/systemd/system/spark-firecracker.service + vmconfig.json
sudo spark daemon install
sudo spark daemon start
```

Or launch manually:

```bash
sudo CONFIG_PATH=~/.spark/firecracker/vmconfig.json \
     FORWARD_PORTS=7777:7777 \
     spark/daemon/firecracker/launcher.sh
```

## 4. View credentials

Credentials are printed to the journal on every startup:

```bash
# From the host — attach to the serial console (if configured) or
# SSH into the guest and run:
journalctl -u spark -n 30
```

Look for the banner:

```
============================================================
  Spark web UI — credentials (DISPLAYED ONCE; save them now)
============================================================
  URL:      http://0.0.0.0:7777
  Username: sparrow1234
  Password: tree-song77@Moon
============================================================
```

The web UI is accessible from any machine on your 192.168.x.x network
at `http://<host-ip>:7777`.

## Networking

The launcher creates a TAP device (`spark-tap0`) with a /30 subnet:

| | IP |
|---|---|
| Host (TAP endpoint) | 192.168.241.1 |
| Guest (eth0) | 192.168.241.2 |

Port forwarding via iptables DNAT routes host port 7777 to guest
port 7777. The `allowed_cidrs` in `spark.yaml` includes both
`192.168.0.0/16` (your LAN) and `192.168.241.0/30` (the TAP subnet)
so traffic from both paths is accepted.

## Customization

Edit `deploy/firecracker/spark.yaml` and either:

1. Rebuild the rootfs (includes the new config), or
2. Mount the rootfs image, replace `/root/.spark/spark.yaml`, unmount:
   ```bash
   sudo mount -o loop ~/.spark/firecracker/rootfs.ext4 /mnt
   sudo cp deploy/firecracker/spark.yaml /mnt/root/.spark/spark.yaml
   sudo umount /mnt
   ```

## Teardown

```bash
sudo spark daemon stop
sudo spark daemon uninstall
```
