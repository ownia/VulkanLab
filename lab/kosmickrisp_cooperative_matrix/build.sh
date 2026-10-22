#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$ROOT/config.sh"

for tool in uv ninja cmake; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'Required build tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done
PYTHON_ENV_DIR="$BUILD_DIR/python"
if [ ! -x "$PYTHON_ENV_DIR/bin/python3.12" ]; then
    uv venv --python 3.12 "$PYTHON_ENV_DIR"
fi
uv pip install --python "$PYTHON_ENV_DIR/bin/python3.12" \
    'meson>=1.9.1' mako packaging pyyaml
VIRTUAL_ENV="$PYTHON_ENV_DIR"
PATH="$PYTHON_ENV_DIR/bin:$PATH"
export VIRTUAL_ENV PATH

if [ -z "${LLVM_PREFIX:-}" ] && ! command -v llvm-config >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        LLVM_PREFIX=$(brew --prefix llvm)
    fi
fi
if [ -n "${LLVM_PREFIX:-}" ]; then
    PATH="$LLVM_PREFIX/bin:$PATH"
    PKG_CONFIG_PATH="$LLVM_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    export PATH PKG_CONFIG_PATH
fi
if ! command -v llvm-config >/dev/null 2>&1; then
    printf 'LLVM not found. Set LLVM_PREFIX to an LLVM installation containing bin/llvm-config.\n' >&2
    exit 1
fi

sh "$ROOT/../../scripts/prepare_dependencies.sh" "$ROOT" "$SOURCE_DIR"
VULKAN_INCLUDE_DIR="$SOURCE_DIR/Vulkan-Headers/include"

for patch in "$ROOT/patch/mesa"/*.patch; do
    if ! git -C "$SOURCE_DIR/mesa" apply --reverse --check "$patch" 2>/dev/null; then
        git -C "$SOURCE_DIR/mesa" apply --check "$patch"
        git -C "$SOURCE_DIR/mesa" apply "$patch"
    fi
done

SPIRV_TOOLS_BUILD_DIR="$BUILD_DIR/SPIRV-Tools/$CONFIGURATION/build"
SPIRV_TOOLS_INSTALL_DIR="$SPIRV_TOOLS_BUILD_DIR/install"
cmake -S "$SOURCE_DIR/SPIRV-Tools" -B "$SPIRV_TOOLS_BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" \
    -DCMAKE_INSTALL_PREFIX="$SPIRV_TOOLS_INSTALL_DIR" \
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DSPIRV-Headers_SOURCE_DIR="$SOURCE_DIR/SPIRV-Headers" \
    -DSPIRV_SKIP_EXECUTABLES=ON \
    -DSPIRV_WERROR=OFF -DSKIP_SPIRV_TOOLS_INSTALL=OFF
cmake --build "$SPIRV_TOOLS_BUILD_DIR" --config "$CONFIGURATION" \
    --parallel "${JOBS:-4}"
cmake --install "$SPIRV_TOOLS_BUILD_DIR" --config "$CONFIGURATION"
PKG_CONFIG_PATH="$SPIRV_TOOLS_INSTALL_DIR/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export PKG_CONFIG_PATH

TRANSLATOR_BUILD_DIR="$BUILD_DIR/SPIRV-LLVM-Translator/$CONFIGURATION/build"
TRANSLATOR_INSTALL_DIR="$BUILD_DIR/SPIRV-LLVM-Translator/$CONFIGURATION/install"
cmake -S "$SOURCE_DIR/SPIRV-LLVM-Translator" -B "$TRANSLATOR_BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" \
    -DCMAKE_INSTALL_PREFIX="$TRANSLATOR_INSTALL_DIR" \
    -DLLVM_DIR="$(llvm-config --cmakedir)" \
    -DLLVM_EXTERNAL_SPIRV_HEADERS_SOURCE_DIR="$SOURCE_DIR/SPIRV-Headers" \
    -DLLVM_SPIRV_INCLUDE_TESTS=OFF -DBUILD_SHARED_LIBS=ON \
    -DLLVM_LINK_LLVM_DYLIB=ON
cmake --build "$TRANSLATOR_BUILD_DIR" --config "$CONFIGURATION" \
    --parallel "${JOBS:-4}"
cmake --install "$TRANSLATOR_BUILD_DIR" --config "$CONFIGURATION"
PKG_CONFIG_PATH="$TRANSLATOR_INSTALL_DIR/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export PKG_CONFIG_PATH

cmake -S "$SOURCE_DIR/Vulkan-Headers" -B "$VULKAN_HEADERS_BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" \
    -DCMAKE_INSTALL_PREFIX="$VULKAN_HEADERS_INSTALL_DIR" \
    -DVULKAN_HEADERS_ENABLE_TESTS=OFF -DVULKAN_HEADERS_ENABLE_INSTALL=ON \
    -DVULKAN_HEADERS_ENABLE_MODULE=OFF
cmake --install "$VULKAN_HEADERS_BUILD_DIR" --config "$CONFIGURATION"

cmake -S "$SOURCE_DIR/Vulkan-Loader" -B "$VULKAN_LOADER_BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" \
    -DVulkanHeaders_DIR="$VULKAN_HEADERS_INSTALL_DIR/share/cmake/VulkanHeaders" \
    -DBUILD_TESTS=OFF -DLOADER_CODEGEN=OFF -DAPPLE_STATIC_LOADER=OFF
cmake --build "$VULKAN_LOADER_BUILD_DIR" --config "$CONFIGURATION" \
    --target vulkan --parallel "${JOBS:-4}"
if [ ! -f "$VULKAN_LIBRARY" ]; then
    printf 'Vulkan loader not found: %s\n' "$VULKAN_LIBRARY" >&2
    exit 1
fi

set -- "$MESA_BUILD_DIR" "$SOURCE_DIR/mesa" \
    --buildtype="$MESON_BUILDTYPE" --prefer-static \
    "-Dc_link_args=-Wl,-rpath,$TRANSLATOR_INSTALL_DIR/lib" \
    "-Dcpp_link_args=-Wl,-rpath,$TRANSLATOR_INSTALL_DIR/lib" \
    -Dvulkan-drivers=kosmickrisp -Dgallium-drivers= -Dplatforms=macos \
    -Dopengl=false -Dgles1=disabled -Dgles2=disabled \
    -Degl=disabled -Dllvm=enabled -Dzstd=disabled
if [ -f "$MESA_BUILD_DIR/meson-private/coredata.dat" ]; then
    meson setup --reconfigure "$@"
else
    meson setup "$@"
fi
meson compile -C "$MESA_BUILD_DIR" -j "${JOBS:-4}"

if [ ! -f "$KOSMICKRISP_LIBRARY" ]; then
    printf 'KosmicKrisp library not found: %s\n' "$KOSMICKRISP_LIBRARY" >&2
    exit 1
fi

for benchmark in vkpeak vk_cooperative_matrix_perf; do
    set --
    if [ "$benchmark" = vkpeak ]; then
        set -- -DNCNN_VULKAN=ON -DNCNN_SIMPLEVK=ON
    fi
    cmake -S "$SOURCE_DIR/$benchmark" -B "$BUILD_DIR/$benchmark/$CONFIGURATION/build" \
        -DCMAKE_BUILD_TYPE="$CONFIGURATION" \
        -DVulkan_LIBRARY="$VULKAN_LIBRARY" \
        -DVulkan_INCLUDE_DIR="$VULKAN_INCLUDE_DIR" "$@"
    cmake --build "$BUILD_DIR/$benchmark/$CONFIGURATION/build" --config "$CONFIGURATION" \
        --target "$benchmark" --parallel "${JOBS:-4}"
done
