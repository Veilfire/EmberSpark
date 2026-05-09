#!/usr/bin/env bash
# Sync ./wiki/*.md to the GitHub wiki repo.
#
# GitHub wikis live in a separate git repo at <owner>/<repo>.wiki.git. This
# script clones that repo to a temp directory, copies everything from ./wiki/
# into it, commits, and pushes.
#
# Requirements:
#   - gh CLI authenticated (`gh auth status`), OR git push access to the wiki repo
#   - The wiki must be enabled in the repo's Settings → General → Features
#   - The wiki must have at least one page (create "Home" manually once)
#
# Usage:
#   scripts/sync-wiki.sh                       # uses `gh` to resolve the repo
#   scripts/sync-wiki.sh --repo owner/spark    # explicit repo
#   scripts/sync-wiki.sh --dry-run             # show what would happen
#
# Safety:
#   - This script will OVERWRITE every .md file in the wiki repo with the
#     matching file from ./wiki/. Files in the wiki that don't exist in ./wiki/
#     are NOT deleted — you'll have to clean those up manually.
#   - The commit message references the Spark commit that produced the content.

set -euo pipefail

SPARK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIKI_SRC="$SPARK_ROOT/wiki"
DRY_RUN=0
REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --repo) REPO="$2"; shift 2 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed -E 's/^#( |$)//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -d "$WIKI_SRC" ]]; then
  echo "error: no wiki directory at $WIKI_SRC" >&2
  exit 1
fi

# Resolve the repo if not provided.
if [[ -z "$REPO" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "error: gh CLI not found. Install it or pass --repo owner/name" >&2
    exit 1
  fi
  REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)
  if [[ -z "$REPO" ]]; then
    echo "error: could not resolve repo from gh. Pass --repo owner/name" >&2
    exit 1
  fi
fi

WIKI_URL="https://github.com/${REPO}.wiki.git"
WIKI_TMP="$(mktemp -d -t spark-wiki-XXXXXX)"

cleanup() { rm -rf "$WIKI_TMP"; }
trap cleanup EXIT

echo "==> cloning $WIKI_URL"
if ! git clone --depth 1 "$WIKI_URL" "$WIKI_TMP" 2>/dev/null; then
  cat >&2 <<MSG
error: failed to clone $WIKI_URL

Possible causes:
  - The wiki hasn't been initialized. Go to https://github.com/${REPO}/wiki
    and click "Create the first page". Then re-run this script.
  - The wiki is disabled in Settings → General → Features.
  - You don't have push access to the repository.
MSG
  exit 1
fi

echo "==> copying $WIKI_SRC/*.md to $WIKI_TMP"
cp "$WIKI_SRC"/*.md "$WIKI_TMP/"

pushd "$WIKI_TMP" >/dev/null
git add -A
if git diff --cached --quiet; then
  echo "==> no changes to push"
  popd >/dev/null
  exit 0
fi

SPARK_SHA=$(cd "$SPARK_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
MSG="Sync wiki from main (${SPARK_SHA})"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "==> [dry-run] would commit with message: $MSG"
  git diff --cached --stat
  popd >/dev/null
  exit 0
fi

git commit -m "$MSG" >/dev/null
echo "==> pushing"
git push origin HEAD:master 2>/dev/null || git push origin HEAD:main
popd >/dev/null

echo "==> done. View at https://github.com/${REPO}/wiki"
