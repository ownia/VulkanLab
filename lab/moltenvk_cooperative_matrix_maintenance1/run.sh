#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
configuration=Release
if [ "${1:-}" = --debug ]; then
    configuration=Debug
    shift
fi
BUILD_DIR=${BUILD_DIR:-"$ROOT/build"}
executable="$BUILD_DIR/$configuration/moltenvk_cmm"
if [ ! -x "$executable" ]; then
    printf 'Build first with: %s/build.sh [--debug]\n' "$ROOT" >&2
    exit 1
fi
VULKANLAB_EXPERIMENTAL_CMM1=${VULKANLAB_EXPERIMENTAL_CMM1:-1}
export VULKANLAB_EXPERIMENTAL_CMM1
exec "$executable" "$@"