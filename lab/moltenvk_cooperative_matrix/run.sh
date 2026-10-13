#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$ROOT/config.sh"
VKPEAK="$BUILD_DIR/vkpeak/$CONFIGURATION/vkpeak"
COOPERATIVE_MATRIX_PERF="$BUILD_DIR/vk_cooperative_matrix_perf/$CONFIGURATION/vk_cooperative_matrix_perf"

if [ ! -f "$MOLTENVK_LIBRARY" ]; then
    printf 'MoltenVK library not found: %s. Run build.sh first.\n' "$MOLTENVK_LIBRARY" >&2
    exit 1
fi
for executable in "$VKPEAK" "$COOPERATIVE_MATRIX_PERF"; do
    if [ ! -x "$executable" ]; then
        printf 'Benchmark not found: %s. Run build.sh first.\n' "$executable" >&2
        exit 1
    fi
done

# Measure FP16 matrix throughput on device 0 using the patched MoltenVK driver.
NCNN_VULKAN_DRIVER="$MOLTENVK_LIBRARY" "$VKPEAK" 0 fp16-matrix

# Test shared-memory cooperative matrices at dimension 256 with correctness checking.
cd "$ROOT/../../third_party/vk_cooperative_matrix_perf"
MVK_CONFIG_LOG_LEVEL=1 "$COOPERATIVE_MATRIX_PERF" \
    --shmem --dim=256 --repeat=1 --correctness