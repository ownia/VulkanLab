BUILD_DIR=${BUILD_DIR:-"$ROOT/build"}
case "$BUILD_DIR" in
    /*) ;;
    *) BUILD_DIR="$PWD/$BUILD_DIR" ;;
esac
CONFIGURATION=Release
MESON_BUILDTYPE=release

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [--release|--debug]\n' "$0" >&2
    exit 1
fi
case "${1:-}" in
    ""|release|Release|--release) ;;
    debug|Debug|--debug)
        CONFIGURATION=Debug
        MESON_BUILDTYPE=debug
        ;;
    -h|--help)
        printf 'Usage: %s [--release|--debug]\n' "$0"
        exit 0
        ;;
    *)
        printf 'Unknown configuration: %s\n' "$1" >&2
        exit 1
        ;;
esac

SOURCE_DIR="$BUILD_DIR/src"
MESA_BUILD_DIR="$BUILD_DIR/mesa/$CONFIGURATION/build"
KOSMICKRISP_LIBRARY="$MESA_BUILD_DIR/src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib"
VKPEAK="$BUILD_DIR/vkpeak/$CONFIGURATION/build/vkpeak"
COOPERATIVE_MATRIX_PERF="$BUILD_DIR/vk_cooperative_matrix_perf/$CONFIGURATION/build/vk_cooperative_matrix_perf"

VULKAN_HEADERS_BUILD_DIR="$BUILD_DIR/Vulkan-Headers/$CONFIGURATION/build"
VULKAN_HEADERS_INSTALL_DIR="$VULKAN_HEADERS_BUILD_DIR/install"
VULKAN_LOADER_BUILD_DIR="$BUILD_DIR/Vulkan-Loader/$CONFIGURATION/build"
VULKAN_LIBRARY="$VULKAN_LOADER_BUILD_DIR/loader/libvulkan.dylib"
