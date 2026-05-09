#!/bin/sh
# Spark container entrypoint.
#
# On first boot:
#  - ensures the state directory tree exists under /data/spark
#  - ensures the data volume tree exists under /data/spark-volume
#  - generates ~/.spark/spark.yaml if it doesn't exist, wiring
#    data_volume.root at the mounted data volume path
#
# Then delegates to `spark <args>`.

set -eu

STATE_DIR="/data/spark"
DATA_VOLUME_DIR="/data/spark-volume"

# The container runs as uid 1000 with HOME=/data/spark, so ~/.spark/ is
# /data/spark/.spark/ — see Dockerfile.
mkdir -p "${HOME}/.spark"
mkdir -p "${DATA_VOLUME_DIR}/chroma" "${DATA_VOLUME_DIR}/scratch" "${DATA_VOLUME_DIR}/deliverables"
chmod 0700 "${DATA_VOLUME_DIR}/chroma" "${DATA_VOLUME_DIR}/scratch" "${DATA_VOLUME_DIR}/deliverables" 2>/dev/null || true

SPARK_CONFIG="${HOME}/.spark/spark.yaml"

if [ ! -f "${SPARK_CONFIG}" ]; then
  cat > "${SPARK_CONFIG}" <<YAML
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
metadata:
  name: container
spec:
  web:
    enabled: true
    bind:
      mode: lan
      host: 0.0.0.0
      port: 7777
      allowed_cidrs:
        - 10.0.0.0/8
        - 172.16.0.0/12
        - 192.168.0.0/16
    credentials:
      rotate_on_startup: true
  data_volume:
    enabled: true
    root: ${DATA_VOLUME_DIR}
    chroma_subdir: chroma
    scratch_subdir: scratch
    deliverables_subdir: deliverables
    sqlite_on_volume: true
YAML
  chmod 0600 "${SPARK_CONFIG}"
fi

exec python -m spark.cli.main "$@"
