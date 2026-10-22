#!/bin/sh
set -eu

LAB_DIR=${1:?Usage: sh prepare_dependencies.sh LAB_DIR [SOURCE_DIR]}
SOURCE_DIR=${2:-}
REPO_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

while read -r dependency revision || [ -n "${dependency:-}" ]; do
    [ -n "$dependency" ] || continue
    source_dir="$REPO_ROOT/third_party/$dependency"
    if ! git -C "$REPO_ROOT" submodule status -- "third_party/$dependency" | cut -c1 | grep -q '^[ +]$'; then
        printf 'Initialize dependency first: git submodule update --init --recursive -- third_party/%s\n' \
            "$dependency" >&2
        exit 1
    fi
    if [ -n "$SOURCE_DIR" ]; then
        checkout_dir="$SOURCE_DIR/$dependency"
        if [ ! -e "$checkout_dir" ]; then
            mkdir -p "$SOURCE_DIR"
            git clone --no-hardlinks "$source_dir" "$checkout_dir"
        elif [ "$(git -C "$checkout_dir" rev-parse HEAD)" != "$revision" ]; then
            printf 'Dependency revision changed; move or remove the private checkout and retry: %s\n' \
                "$checkout_dir" >&2
            exit 1
        fi
        source_dir="$checkout_dir"
    fi
    if [ "$(git -C "$source_dir" rev-parse HEAD)" != "$revision" ]; then
        if [ -n "$(git -C "$source_dir" status --porcelain)" ]; then
            printf 'Dependency has local changes; cannot switch revision: %s\n' "$source_dir" >&2
            exit 1
        fi
        if ! git -C "$source_dir" cat-file -e "$revision^{commit}" 2>/dev/null; then
            git -C "$source_dir" fetch origin "$revision"
        fi
        git -C "$source_dir" checkout --detach "$revision"
    fi
    if [ -n "$SOURCE_DIR" ] && [ -f "$source_dir/.gitmodules" ]; then
        git -C "$source_dir" submodule update --init --recursive
    fi
done < "$LAB_DIR/dependencies.lock"
