#!/usr/bin/env bash
# Build a minimal ext4 rootfs for Spark in Firecracker plus a companion
# data image that holds the persistent Chroma + scratch + deliverables
# directories.
#
# Requirements (host):
#   - root privileges (for mounting the image)
#   - debootstrap
#   - e2fsprogs (mkfs.ext4)
#   - python 3.12+
#
# Outputs:
#   $OUT        (default ~/.spark/firecracker/rootfs.ext4)
#   $DATA_OUT   (default ~/.spark/firecracker/data.ext4)

set -euo pipefail

OUT="${OUT:-$HOME/.spark/firecracker/rootfs.ext4}"
DATA_OUT="${DATA_OUT:-$HOME/.spark/firecracker/data.ext4}"
SIZE_MB="${SIZE_MB:-2048}"
DATA_SIZE_MB="${DATA_SIZE_MB:-5120}"
SUITE="${SUITE:-bookworm}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
SPARK_REPO="${SPARK_REPO:-$(cd "$(dirname "$0")/../../.." && pwd)}"

if [ "$(id -u)" -ne 0 ]; then
  echo "build-rootfs.sh must run as root (debootstrap needs it)" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")" "$(dirname "$DATA_OUT")"
IMG="$OUT"
MNT=$(mktemp -d)
trap 'umount "$MNT" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true' EXIT

echo "==> creating sparse image $IMG (${SIZE_MB} MiB)"
truncate -s "${SIZE_MB}M" "$IMG"
mkfs.ext4 -F -L spark-rootfs "$IMG"

# Data image — empty ext4 that the guest mounts at /mnt/spark-data.
if [ ! -f "$DATA_OUT" ]; then
  echo "==> creating data image $DATA_OUT (${DATA_SIZE_MB} MiB)"
  truncate -s "${DATA_SIZE_MB}M" "$DATA_OUT"
  mkfs.ext4 -F -L spark-data "$DATA_OUT"
else
  echo "==> data image $DATA_OUT already exists; preserving contents"
fi

echo "==> mounting image"
mount -o loop "$IMG" "$MNT"

echo "==> debootstrap $SUITE → $MNT"
debootstrap --variant=minbase --include=python3,python3-pip,python3-venv,ca-certificates,bubblewrap,curl,iproute2,systemd-sysv \
  "$SUITE" "$MNT" "$MIRROR"

echo "==> copying spark source into /opt/spark"
mkdir -p "$MNT/opt/spark"
cp -a "$SPARK_REPO/pyproject.toml" "$SPARK_REPO/spark" "$MNT/opt/spark/"

echo "==> installing spark inside chroot"
cat > "$MNT/tmp/setup.sh" <<'INNER'
#!/bin/sh
set -e
export DEBIAN_FRONTEND=noninteractive
python3 -m venv /opt/spark/venv
/opt/spark/venv/bin/pip install --upgrade pip
/opt/spark/venv/bin/pip install '/opt/spark[openai,anthropic,openrouter,ollama,web]'

# Network config: the Firecracker launcher sets up a TAP; we give the guest a
# static address via systemd-networkd.
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/20-eth0.network <<NET
[Match]
Name=eth0

[Network]
Address=192.168.241.2/30
Gateway=192.168.241.1
NET
systemctl enable systemd-networkd

# Mount the data image (attached as /dev/vdb by the launcher) at
# /mnt/spark-data via systemd. Chroma + SQLite + scratch + deliverables
# live here, so the data survives rootfs rebuilds.
mkdir -p /mnt/spark-data
cat > /etc/systemd/system/mnt-spark\\x2ddata.mount <<MNT
[Unit]
Description=Mount Spark data volume
Before=spark.service

[Mount]
What=/dev/vdb
Where=/mnt/spark-data
Type=ext4
Options=defaults

[Install]
WantedBy=multi-user.target
MNT
systemctl enable mnt-spark\\x2ddata.mount

# Spark as a system service.
cat > /etc/systemd/system/spark.service <<SVC
[Unit]
Description=Spark runtime
After=network-online.target mnt-spark\\x2ddata.mount
Wants=network-online.target mnt-spark\\x2ddata.mount
Requires=mnt-spark\\x2ddata.mount

[Service]
Type=simple
Environment=HOME=/root
ExecStart=/opt/spark/venv/bin/python -m spark.cli.main serve --config /root/.spark/spark.yaml
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
systemctl enable spark

# Provide a default SparkRuntime YAML — LAN mode binding to the Firecracker NIC,
# with the data volume pointing at the mounted block device.
mkdir -p /root/.spark
cat > /root/.spark/spark.yaml <<YAML
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
metadata:
  name: firecracker
spec:
  web:
    enabled: true
    bind:
      mode: lan
      host: 0.0.0.0
      port: 7777
      allowed_cidrs:
        - 192.168.241.0/30
        - 10.0.0.0/8
        - 172.16.0.0/12
        - 192.168.0.0/16
    credentials:
      rotate_on_startup: true
  data_volume:
    enabled: true
    root: /mnt/spark-data
    chroma_subdir: chroma
    scratch_subdir: scratch
    deliverables_subdir: deliverables
    sqlite_on_volume: true
YAML
chmod 0600 /root/.spark/spark.yaml
INNER
chmod +x "$MNT/tmp/setup.sh"
chroot "$MNT" /tmp/setup.sh
rm "$MNT/tmp/setup.sh"

echo "==> done: $IMG"
