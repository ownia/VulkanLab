#!/bin/sh
set -eu

LAB_DIR=${1:?Usage: sh prepare_dependencies.sh LAB_DIR}
REPO_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

while read -r dependency revision; do
    [ -n "$dependency" ] || continue
    source_dir="$REPO_ROOT/third_party/$dependency"
    if ! git -C "$REPO_ROOT" submodule status -- "third_party/$dependency" | cut -c1 | grep -q '^[ +]$'; then
        printf 'Initialize dependency first: git submodule update --init --recursive -- third_party/%s\n' \
            "$dependency" >&2
        exit 1
    fi
    if [ "$(git -C "$source_dir" rev-parse HEAD)" = "$revision" ]; then
        continue
    fi
    if [ -n "$(git -C "$source_dir" status --porcelain)" ]; then
        printf 'Dependency has local changes; cannot switch revision: %s\n' "$source_dir" >&2
        exit 1
    fi
    if ! git -C "$source_dir" cat-file -e "$revision^{commit}" 2>/dev/null; then
        git -C "$source_dir" fetch origin "$revision"
    fi
    git -C "$source_dir" checkout --detach "$revision"
done < "$LAB_DIR/dependencies.lock"