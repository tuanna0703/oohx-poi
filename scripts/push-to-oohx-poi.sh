#!/usr/bin/env bash
# Push the oohx-poi/ subtree of the parent oohx-matrix repo up to
# the dedicated tuanna0703/oohx-poi GitHub repo.
#
# Run from anywhere — script auto-resolves the parent repo root and
# does the subtree split there, so you can stay in oohx-poi/ all day.
#
# First call: pass --force to overwrite whatever GitHub initialised the
# repo with (README/.gitignore/LICENSE auto-init).
#
# Usage:
#   ./scripts/push-to-oohx-poi.sh             # subsequent pushes
#   ./scripts/push-to-oohx-poi.sh --force     # first push (or after rebase)

set -euo pipefail

REMOTE_NAME="${REMOTE_NAME:-oohx-poi}"
REMOTE_URL="${REMOTE_URL:-https://github.com/tuanna0703/oohx-poi.git}"
TARGET_BRANCH="${TARGET_BRANCH:-main}"
PREFIX="${PREFIX:-oohx-poi}"

# Resolve the parent repo root from wherever this script was invoked.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"

GIT="git -C $REPO_ROOT"

# Add the remote on first run.
if ! $GIT remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    echo "[push-oohx-poi] adding remote $REMOTE_NAME -> $REMOTE_URL"
    $GIT remote add "$REMOTE_NAME" "$REMOTE_URL"
fi

# Synthesize a branch containing only oohx-poi/ history.
echo "[push-oohx-poi] splitting subtree '$PREFIX' …"
SPLIT_SHA="$($GIT subtree split --prefix="$PREFIX" HEAD)"
echo "[push-oohx-poi] split commit: $SPLIT_SHA"

# Decide push mode.
PUSH_ARGS=("$REMOTE_NAME" "${SPLIT_SHA}:refs/heads/${TARGET_BRANCH}")
if [[ "${1:-}" == "--force" ]]; then
    PUSH_ARGS+=("--force")
    echo "[push-oohx-poi] forcing — overwrites $REMOTE_NAME/$TARGET_BRANCH"
fi

echo "[push-oohx-poi] pushing to $REMOTE_NAME/$TARGET_BRANCH …"
$GIT push "${PUSH_ARGS[@]}"
echo "[push-oohx-poi] done"
