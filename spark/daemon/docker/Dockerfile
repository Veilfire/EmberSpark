    # syntax=docker/dockerfile:1.7

# Multi-stage build for a lean Spark image.
# Stage 1: build frontend (Node).
# Stage 2: build Python wheels.
# Stage 3: runtime with bwrap installed + frontend assets.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=20

# -----------------------------------------------------------------------------
# Stage 1: Frontend build
# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-slim AS frontend

WORKDIR /frontend
COPY spark/web/frontend/package.json spark/web/frontend/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install
COPY spark/web/frontend/ ./
# Skip tsc type-checking in Docker (CI catches type errors); just bundle.
RUN npx vite build --outDir ../static

# -----------------------------------------------------------------------------
# Stage 2: Python wheels
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copy only the metadata first to maximize layer cache.
COPY pyproject.toml README.md /build/
COPY spark /build/spark

RUN pip install --upgrade pip \
 && pip wheel --wheel-dir /wheels \
      '.[openai,anthropic,openrouter,ollama,web]'

# -----------------------------------------------------------------------------
# Stage 3: Runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="spark-runtime" \
      org.opencontainers.image.description="Spark — agent runtime for bounded autonomy" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SPARK_STATE_DIR=/data/spark \
    HOME=/data/spark \
    # Pin the HF cache to an image-baked path (not on the data volume)
    # so the pre-fetched embedding + cross-encoder weights survive
    # `docker compose down -v` and no first-run download is needed.
    HF_HOME=/opt/spark/hf-cache \
    HF_HUB_DISABLE_TELEMETRY=1 \
    # Cap each HF download. Without this, a stalled anonymous request
    # to hf.co can hang the build indefinitely (no progress bar in
    # build context to even tell). 120s per-file is generous for the
    # ~50MB safetensors we pull.
    HF_HUB_DOWNLOAD_TIMEOUT=120

# bubblewrap for the OS sandbox (userns-unprivileged); curl for healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends bubblewrap curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. Note: user namespaces in the container allow bwrap to work
# unprivileged as long as the container runs with sensible defaults (no
# --privileged needed on modern kernels).
RUN groupadd --system --gid 1000 spark \
 && useradd --system --uid 1000 --gid spark --home /data/spark --shell /usr/sbin/nologin spark \
 && mkdir -p /data/spark /data/spark-volume /opt/spark/hf-cache \
 && chown -R spark:spark /data /opt/spark

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels spark-runtime[openai,anthropic,openrouter,ollama,web] \
 && rm -rf /wheels

# Pre-download the embedding bi-encoder + cross-encoder reranker so the
# first run doesn't pay the cold-start cost (or require network access).
# ~55MB total; first-query latency drops from ~5s to near-instant.
# Weights live under HF_HOME which is baked into the image, so
# `docker compose down -v` doesn't wipe them.
#
# This RUN is the one that used to "freeze" the build:
#   - ``python -u`` forces unbuffered stdout so progress prints land
#     immediately (no waiting for the 4 KB stdio buffer to flush).
#   - ``snapshot_download`` exposes the actual HF download progress and
#     respects ``HF_HUB_DOWNLOAD_TIMEOUT`` set above; the
#     ``SentenceTransformer()`` constructor we used previously hides
#     both behind a non-TTY ``tqdm`` that silently no-ops in build
#     context, leaving you staring at a "Warning: …" line for minutes.
#   - The ``--mount=type=cache`` lines reuse downloaded weights across
#     builds, so a rebuild after a cache-bust upstream takes seconds
#     instead of redownloading 55MB. Cache survives ``buildx prune``.
RUN --mount=type=cache,target=/opt/spark/hf-cache,uid=1000,gid=1000 \
    --mount=type=cache,target=/root/.cache/huggingface \
    python -u <<'PY'
import os, sys, time
from huggingface_hub import snapshot_download

# Must match spark.memory.embeddings.SentenceTransformersProvider default
# and spark.memory.retrieval._get_cross_encoder — if you change either,
# update both here.
MODELS = [
    "BAAI/bge-small-en-v1.5",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
]

print(f"[hf-prefetch] HF_HOME = {os.environ['HF_HOME']}", flush=True)
for repo in MODELS:
    print(f"[hf-prefetch] {repo}: starting", flush=True)
    started = time.monotonic()
    try:
        # ``etag_timeout`` caps the metadata request; the per-file
        # download falls back to ``HF_HUB_DOWNLOAD_TIMEOUT`` (env var)
        # which is set in the runtime stage above.
        snapshot_download(
            repo_id=repo,
            etag_timeout=30,
            local_files_only=False,
        )
    except Exception as exc:
        # Hard-fail the build so a flaky HF doesn't bake a broken
        # image. The operator can retry; the cache mount means a
        # successful prior fetch is reused.
        print(f"[hf-prefetch] {repo}: FAILED {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)
    elapsed = time.monotonic() - started
    print(f"[hf-prefetch] {repo}: done in {elapsed:.1f}s", flush=True)
print("[hf-prefetch] all models cached", flush=True)
PY
RUN chown -R spark:spark /opt/spark/hf-cache

# Copy built frontend assets into the package's static directory.
COPY --from=frontend /static /usr/local/lib/python3.12/site-packages/spark/web/static

# Bundle agent templates so the web UI + CLI can discover them.
# templates_root() walks up from spark/templates/loader.py → examples/templates/
COPY examples/templates /usr/local/lib/python3.12/site-packages/examples/templates

COPY spark/daemon/docker/entrypoint.sh /usr/local/bin/spark-entrypoint
RUN chmod 0755 /usr/local/bin/spark-entrypoint

USER spark
WORKDIR /data/spark
VOLUME ["/data/spark", "/data/spark-volume"]
EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:7777/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/spark-entrypoint"]
CMD ["serve"]
