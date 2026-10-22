#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$ROOT/config.sh"

if [ ! -f "$KOSMICKRISP_LIBRARY" ]; then
    printf 'KosmicKrisp library not found: %s. Run build.sh first.\n' "$KOSMICKRISP_LIBRARY" >&2
    exit 1
fi
if [ ! -f "$VULKAN_LIBRARY" ]; then
    printf 'Vulkan loader not found: %s. Run build.sh first.\n' "$VULKAN_LIBRARY" >&2
    exit 1
fi
for executable in "$VKPEAK" "$COOPERATIVE_MATRIX_PERF"; do
    if [ ! -x "$executable" ]; then
        printf 'Benchmark not found: %s. Run build.sh first.\n' "$executable" >&2
        exit 1
    fi
done

set -- "$MESA_BUILD_DIR"/src/kosmickrisp/vulkan/kosmickrisp_mesa_devenv_icd.*.json
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    printf 'Expected one KosmicKrisp ICD manifest in %s. Run build.sh first.\n' "$MESA_BUILD_DIR" >&2
    exit 1
fi
export VK_DRIVER_FILES="$1"
export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
export NCNN_VULKAN_DRIVER="$VULKAN_LIBRARY"
export MESA_KK_EXPERIMENTAL="${MESA_KK_EXPERIMENTAL-cooperative_matrix}"

# Measure FP16 matrix throughput on device 0 using the patched KosmicKrisp driver.
"$VKPEAK" 0 fp16-matrix

# Test shared-memory cooperative matrices at dimension 256 with correctness checking.
# TODO: Skip tiles exceeding the device's shared-memory limit; the unmodified
# benchmark can fail when creating pipelines for these tiles.
cd "$SOURCE_DIR/vk_cooperative_matrix_perf"
"$COOPERATIVE_MATRIX_PERF" --shmem --dim=256 --repeat=1 --correctness
