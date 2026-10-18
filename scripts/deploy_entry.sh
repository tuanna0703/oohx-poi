#!/usr/bin/env bash
# Forced command for the CI deploy key. Installed root-owned, outside the
# checkout, so a commit cannot rewrite it:
#
#   install -o root -g root -m 755 scripts/deploy_entry.sh /usr/local/bin/poi-lake-deploy
#
# and in ~poi/.ssh/authorized_keys:
#
#   restrict,command="/usr/local/bin/poi-lake-deploy" ssh-ed25519 AAAA... github-actions
#
# The only thing a caller can choose is the commit, and it must already be on
# origin/main. The deploy logic itself comes from that commit
# (scripts/ci_deploy.sh), so it is reviewed and versioned with the code.
set -euo pipefail

SHA="${SSH_ORIGINAL_COMMAND:-}"
if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
    echo "poi-lake-deploy: expected a 40-character commit sha" >&2
    exit 2
fi

cd "${POI_LAKE_DIR:-/opt/poi-lake}"
git fetch -q origin main
if ! git merge-base --is-ancestor "$SHA" origin/main; then
    echo "poi-lake-deploy: $SHA is not on origin/main" >&2
    exit 2
fi

git show "$SHA:scripts/ci_deploy.sh" | bash -s -- "$SHA"
