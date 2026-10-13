BUILD_DIR=${BUILD_DIR:-"$ROOT/build"}
case "$BUILD_DIR" in
    /*) ;;
    *) BUILD_DIR="$PWD/$BUILD_DIR" ;;
esac
CONFIGURATION=Release

if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [--release|--debug]\n' "$0" >&2
    exit 1
fi
case "${1:-}" in
    ""|release|Release|--release) ;;
    debug|Debug|--debug) CONFIGURATION=Debug ;;
    -h|--help)
        printf 'Usage: %s [--release|--debug]\n' "$0"
        exit 0
        ;;
    *)
        printf 'Unknown configuration: %s\n' "$1" >&2
        exit 1
        ;;
esac

MOLTENVK_DIR="$BUILD_DIR/src/MoltenVK"
MOLTENVK_LIBRARY="$MOLTENVK_DIR/Package/$CONFIGURATION/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib"