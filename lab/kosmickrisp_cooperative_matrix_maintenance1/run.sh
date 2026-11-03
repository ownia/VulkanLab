#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
configuration=--release
case "${1:-}" in
    --debug|--release) configuration=$1; shift ;;
esac
configure() {
    . "$ROOT/../kosmickrisp_cooperative_matrix/config.sh"
}
configure "$configuration"
executable="$BUILD_DIR/$CONFIGURATION/kosmickrisp_cmm"

for argument do
    case "$argument" in
        -h|--help)
            if [ -x "$executable" ] && [ -f "$VULKAN_LIBRARY" ]; then
                exec "$executable" "$@"
            fi
            printf 'Usage: %s [--release|--debug] [options]\n' "$0"
            printf '%s\n' \
                '  --baseline-only Run the KHR-only shader' \
                '  --compare       Require and compare both shaders (default)' \
                '  --query-only    Query maintenance1 support without dispatching shaders' \
                '  --correctness-only Validate results without timed dispatches' \
                '  --matrices      Print input and result matrices' \
                '  --workgroups N  Matrix tiles per timed dispatch (default: 1024)' \
                '  --samples N     Timed samples per shader (default: 25)' \
                '  --help          Show this help'
            exit 0
            ;;
    esac
done

if [ ! -x "$executable" ]; then
    printf 'Smoke executable not found: %s. Run sh %s/build.sh %s first.\n' \
        "$executable" "$ROOT" "$configuration" >&2
    exit 1
fi
for library in "$VULKAN_LIBRARY" "$KOSMICKRISP_LIBRARY"; do
    if [ ! -f "$library" ]; then
        printf 'Library not found: %s. Run build.sh first.\n' "$library" >&2
        exit 1
    fi
done
select_icd() {
    set -- "$MESA_BUILD_DIR"/src/kosmickrisp/vulkan/kosmickrisp_mesa_devenv_icd.*.json
    if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
        printf 'Expected one KosmicKrisp ICD manifest in %s. Run build.sh first.\n' "$MESA_BUILD_DIR" >&2
        exit 1
    fi
    VK_DRIVER_FILES=$1
}
select_icd
export VK_DRIVER_FILES
export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
export MESA_KK_EXPERIMENTAL="${MESA_KK_EXPERIMENTAL-cooperative_matrix,cooperative_matrix_maintenance1}"
DYLD_LIBRARY_PATH="$(dirname -- "$VULKAN_LIBRARY")${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export DYLD_LIBRARY_PATH
exec "$executable" "$@"