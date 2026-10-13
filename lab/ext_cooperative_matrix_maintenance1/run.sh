#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD_DIR=${BUILD_DIR:-"$ROOT/build"}
case "$BUILD_DIR" in
    /*) ;;
    *) BUILD_DIR="$PWD/$BUILD_DIR" ;;
esac
LAVAPIPE_ICD=${LAVAPIPE_ICD:-"$BUILD_DIR/mesa/src/gallium/targets/lavapipe/lvp_devenv_icd.$(uname -m).json"}

if test -z "${VK_DRIVER_FILES:-}"; then
    if ! test -f "$LAVAPIPE_ICD"; then
        printf '%s\n' 'Lavapipe ICD not found. Run sh build.sh or set LAVAPIPE_ICD or VK_DRIVER_FILES.' >&2
        exit 1
    fi
    VK_DRIVER_FILES="$LAVAPIPE_ICD"
    export VK_DRIVER_FILES
fi
LP_NATIVE_VECTOR_WIDTH=${LP_NATIVE_VECTOR_WIDTH:-256}
export LP_NATIVE_VECTOR_WIDTH

if ! test -x "$BUILD_DIR/ext_cmm"; then
    printf '%s\n' 'Demo executable not found. Run sh build.sh first.' >&2
    exit 1
fi
exec "$BUILD_DIR/ext_cmm" "$@"