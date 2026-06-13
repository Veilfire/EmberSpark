"""Prefetch the embedding + reranker weights into HF_HOME at image build time.

Extracted from the Dockerfile (it used to be an inline ``RUN <<'PY'`` heredoc)
so the build is portable: BuildKit-only heredocs and ``RUN --mount`` are not
parsed by classic buildah/podman, which read the heredoc body as Dockerfile
instructions and choke on the ``from ... import`` line.
"""

import os
import sys
import time

from huggingface_hub import snapshot_download

# Must match spark.memory.embeddings.SentenceTransformersProvider default
# and spark.memory.retrieval._get_cross_encoder — if you change either,
# update both here.
MODELS = [
    "BAAI/bge-small-en-v1.5",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
]

# Transient HF disconnects ("Server disconnected without sending a response",
# read timeouts, 5xx) are common during a build. Retry with backoff before
# hard-failing so one network blip doesn't abort the whole image build.
ATTEMPTS = 4

print(f"[hf-prefetch] HF_HOME = {os.environ['HF_HOME']}", flush=True)
for repo in MODELS:
    print(f"[hf-prefetch] {repo}: starting", flush=True)
    started = time.monotonic()
    for attempt in range(1, ATTEMPTS + 1):
        try:
            # ``etag_timeout`` caps the metadata request; the per-file download
            # falls back to ``HF_HUB_DOWNLOAD_TIMEOUT`` (env var) set in the
            # runtime stage of the Dockerfile. snapshot_download resumes
            # partially-downloaded files, so a retry doesn't re-fetch what
            # already landed.
            # max_workers caps how many files download at once. Default is 8,
            # which (with the Xet backend disabled in the Dockerfile env) opens
            # 8 simultaneous connections — enough to choke a home line. 2 keeps
            # it polite while still overlapping a little.
            snapshot_download(
                repo_id=repo,
                etag_timeout=30,
                local_files_only=False,
                max_workers=2,
            )
            break
        except Exception as exc:
            label = f"{type(exc).__name__}: {exc}"
            if attempt == ATTEMPTS:
                # Hard-fail the build so a persistently-flaky HF doesn't bake a
                # broken image. The operator can retry the build.
                print(
                    f"[hf-prefetch] {repo}: FAILED after {ATTEMPTS} attempts — {label}",
                    flush=True,
                )
                sys.exit(1)
            wait = attempt * 5
            print(
                f"[hf-prefetch] {repo}: attempt {attempt}/{ATTEMPTS} failed "
                f"({label}); retrying in {wait}s",
                flush=True,
            )
            time.sleep(wait)
    elapsed = time.monotonic() - started
    print(f"[hf-prefetch] {repo}: done in {elapsed:.1f}s", flush=True)
print("[hf-prefetch] all models cached", flush=True)
