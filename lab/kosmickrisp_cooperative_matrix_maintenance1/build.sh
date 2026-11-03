#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$ROOT/../kosmickrisp_cooperative_matrix/config.sh"
JOBS=${JOBS:-8}
case "$JOBS" in
    ''|*[!0-9]*|0) printf 'JOBS must be a positive integer.\n' >&2; exit 2 ;;
esac
set -- "$ROOT"/patch/mesa/*.patch
if [ ! -f "$1" ]; then
    printf 'Maintenance1 Mesa patch missing: %s/patch/mesa/*.patch\n' "$ROOT" >&2
    exit 1
fi

BUILD_DIR="$BUILD_DIR" JOBS="$JOBS" KOSMICKRISP_LAB_DIR="$ROOT" \
    KOSMICKRISP_EXTRA_PATCH_DIR="$ROOT/patch/mesa" KOSMICKRISP_SKIP_BENCHMARKS=1 \
    sh "$ROOT/../kosmickrisp_cooperative_matrix/build.sh" "--$(printf '%s' "$CONFIGURATION" | tr '[:upper:]' '[:lower:]')"

cmake -S "$SOURCE_DIR/glslang" -B "$BUILD_DIR/tools/glslang" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_GLSLANG_BINARIES=ON -DENABLE_SPIRV=ON \
    -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DENABLE_HLSL=OFF
cmake --build "$BUILD_DIR/tools/glslang" --target glslang-standalone --parallel "$JOBS"
cmake -S "$SOURCE_DIR/SPIRV-Tools" -B "$BUILD_DIR/tools/spirv-tools" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DSPIRV-Headers_SOURCE_DIR="$SOURCE_DIR/SPIRV-Headers" \
    -DSPIRV_SKIP_TESTS=ON -DSPIRV_SKIP_EXECUTABLES=OFF
cmake --build "$BUILD_DIR/tools/spirv-tools" --target spirv-val spirv-dis --parallel "$JOBS"
cmake -S "$ROOT" -B "$BUILD_DIR/$CONFIGURATION" -G Ninja \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" -DVulkan_LIBRARY="$VULKAN_LIBRARY" \
    -DVulkan_INCLUDE_DIR="$SOURCE_DIR/Vulkan-Headers/include" \
    -DGLSLANG_VALIDATOR="$BUILD_DIR/tools/glslang/StandAlone/glslang" \
    -DSPIRV_VAL="$BUILD_DIR/tools/spirv-tools/tools/spirv-val"
cmake --build "$BUILD_DIR/$CONFIGURATION" --parallel "$JOBS"

CTS_SOURCE="$SOURCE_DIR/VK-GL-CTS"
CTS_ROOT="$BUILD_DIR/cts"
CTS_BUILD="$CTS_ROOT/$CONFIGURATION"
CTS_VENDORED="$ROOT/../../third_party/VK-GL-CTS"
export GIT_TERMINAL_PROMPT=0
export GIT_ALLOW_PROTOCOL=file:https
mkdir -p "$CTS_ROOT/logs"

for dependency in spirv-tools glslang spirv-headers vulkan-docs amber jsoncpp vulkansc-pcutil vulkan-video-samples video_generator; do
    cached="$CTS_VENDORED/external/$dependency/src"
    private="$CTS_SOURCE/external/$dependency/src"
    if [ ! -e "$private" ] && [ -e "$cached/.git" ]; then
        git clone --no-hardlinks --no-checkout "$cached" "$private"
    fi
done

printf 'Fetching CTS-pinned dependencies in %s (HTTPS, certificate checks enabled).\n' "$CTS_SOURCE"
if python3 "$CTS_SOURCE/external/fetch_sources.py" --protocol https > "$CTS_ROOT/logs/fetch.log" 2>&1; then
    printf 'Dependency log: %s\n' "$CTS_ROOT/logs/fetch.log"
else
    exit_code=$?
    cat "$CTS_ROOT/logs/fetch.log" >&2
    exit "$exit_code"
fi
{
    printf 'VK-GL-CTS %s\n' "$(git -C "$CTS_SOURCE" rev-parse HEAD)"
    for checkout in "$CTS_SOURCE"/external/*/src; do
        if [ -e "$checkout/.git" ]; then
            printf '%s %s\n' "$(basename -- "$(dirname -- "$checkout")")" "$(git -C "$checkout" rev-parse HEAD)"
        fi
    done
} > "$CTS_ROOT/dependencies.lock"

printf 'Configuring macOS deqp-vk (%s).\n' "$CONFIGURATION"
if cmake -S "$CTS_SOURCE" -B "$CTS_BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" -DDEQP_TARGET=osx \
    -DSELECTED_BUILD_TARGETS=deqp-vk -DDEQP_DISABLE_VK_VIDEO_TESTS=ON \
    > "$CTS_ROOT/logs/configure-$CONFIGURATION.log" 2>&1; then
    printf 'Configuration log: %s\n' "$CTS_ROOT/logs/configure-$CONFIGURATION.log"
else
    exit_code=$?
    cat "$CTS_ROOT/logs/configure-$CONFIGURATION.log" >&2
    exit "$exit_code"
fi
printf 'Building deqp-vk with %s jobs; log: %s\n' "$JOBS" "$CTS_ROOT/logs/build-$CONFIGURATION.log"
if cmake --build "$CTS_BUILD" --target deqp-vk --parallel "$JOBS" \
    > "$CTS_ROOT/logs/build-$CONFIGURATION.log" 2>&1; then
    printf 'CTS binary: %s/external/vulkancts/modules/vulkan/deqp-vk\n' "$CTS_BUILD"
else
    exit_code=$?
    tail -n 80 "$CTS_ROOT/logs/build-$CONFIGURATION.log" >&2
    exit "$exit_code"
fi