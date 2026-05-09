#!/usr/bin/env bash
# Bring up a Firecracker microVM for Spark.
#
# Creates a TAP device, enables forwarding + NAT, boots Firecracker with the
# config at $CONFIG_PATH, and forwards the web UI port from the host.
#
# Requirements (host):
#   - Linux with KVM (/dev/kvm accessible)
#   - firecracker binary on PATH (or $FIRECRACKER)
#   - iproute2, iptables
#   - root

set -euo pipefail

CONFIG_PATH="${CONFIG_PATH:?path to VM config JSON}"
TAP="${TAP:-spark-tap0}"
HOST_CIDR="${HOST_CIDR:-192.168.241.1/30}"
GUEST_IP="${GUEST_IP:-192.168.241.2}"
FORWARD_PORTS="${FORWARD_PORTS:-7777:7777}"   # host:guest[,host:guest,...]
FIRECRACKER="${FIRECRACKER:-firecracker}"
SOCK="${SOCK:-/tmp/spark-firecracker.sock}"
UPSTREAM_IFACE="${UPSTREAM_IFACE:-$(ip route show default | awk '/default/ {print $5; exit}')}"

if [ "$(id -u)" -ne 0 ]; then
  echo "launcher.sh must run as root" >&2
  exit 1
fi
if [ ! -r /dev/kvm ]; then
  echo "/dev/kvm not accessible" >&2
  exit 1
fi
if ! command -v "$FIRECRACKER" >/dev/null 2>&1; then
  echo "firecracker binary not found (set FIRECRACKER=...)" >&2
  exit 1
fi

cleanup() {
  set +e
  ip link del "$TAP" 2>/dev/null
  rm -f "$SOCK"
  for fwd in ${FORWARD_PORTS//,/ }; do
    host_port="${fwd%:*}"
    guest_port="${fwd#*:}"
    iptables -t nat -D PREROUTING -p tcp --dport "$host_port" -j DNAT \
      --to-destination "$GUEST_IP:$guest_port" 2>/dev/null
  done
  iptables -D FORWARD -i "$TAP" -o "$UPSTREAM_IFACE" -j ACCEPT 2>/dev/null
  iptables -D FORWARD -o "$TAP" -i "$UPSTREAM_IFACE" \
    -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null
  iptables -t nat -D POSTROUTING -o "$UPSTREAM_IFACE" -j MASQUERADE 2>/dev/null
}
trap cleanup EXIT

# Create TAP and give it an IP in the guest subnet.
ip tuntap add "$TAP" mode tap 2>/dev/null || true
ip addr add "$HOST_CIDR" dev "$TAP" 2>/dev/null || true
ip link set dev "$TAP" up

# Enable forwarding, NAT, and port forwards.
sysctl -w net.ipv4.ip_forward=1 >/dev/null
iptables -t nat -C POSTROUTING -o "$UPSTREAM_IFACE" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -o "$UPSTREAM_IFACE" -j MASQUERADE
iptables -C FORWARD -i "$TAP" -o "$UPSTREAM_IFACE" -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -i "$TAP" -o "$UPSTREAM_IFACE" -j ACCEPT
iptables -C FORWARD -o "$TAP" -i "$UPSTREAM_IFACE" \
  -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -o "$TAP" -i "$UPSTREAM_IFACE" \
  -m state --state RELATED,ESTABLISHED -j ACCEPT

for fwd in ${FORWARD_PORTS//,/ }; do
  host_port="${fwd%:*}"
  guest_port="${fwd#*:}"
  iptables -t nat -A PREROUTING -p tcp --dport "$host_port" -j DNAT \
    --to-destination "$GUEST_IP:$guest_port"
done

rm -f "$SOCK"
exec "$FIRECRACKER" --api-sock "$SOCK" --config-file "$CONFIG_PATH"
