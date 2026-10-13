#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD_DIR=${BUILD_DIR:-"$ROOT/build"}
case "$BUILD_DIR" in
    /*) ;;
    *) BUILD_DIR="$PWD/$BUILD_DIR" ;;
esac
TOOLS_BUILD_DIR="$BUILD_DIR/tools"
THIRD_PARTY_DIR=$(CDPATH= cd -- "$ROOT/../../third_party" && pwd)
MESA_DIR=${MESA_DIR:-"$THIRD_PARTY_DIR/mesa"}
MESA_BUILD_DIR="$BUILD_DIR/mesa"
JOBS=${JOBS:-4}
VULKAN_HEADERS_DIR=${VULKAN_HEADERS_DIR:-"$THIRD_PARTY_DIR/Vulkan-Headers/include"}

if test -z "${GLSLANG_VALIDATOR:-}"; then
    cmake -S "$THIRD_PARTY_DIR/glslang" -B "$TOOLS_BUILD_DIR/glslang" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DENABLE_GLSLANG_BINARIES=ON -DENABLE_SPIRV=ON \
        -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DENABLE_HLSL=OFF
    cmake --build "$TOOLS_BUILD_DIR/glslang" --target glslang-standalone --parallel "$JOBS"
    GLSLANG_VALIDATOR="$TOOLS_BUILD_DIR/glslang/StandAlone/glslang"
fi

if test -z "${SPIRV_VAL:-}"; then
    cmake -S "$THIRD_PARTY_DIR/SPIRV-Tools" -B "$TOOLS_BUILD_DIR/spirv-tools" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DSPIRV-Headers_SOURCE_DIR="$THIRD_PARTY_DIR/SPIRV-Headers" \
        -DSPIRV_SKIP_TESTS=ON -DSPIRV_SKIP_EXECUTABLES=OFF
    cmake --build "$TOOLS_BUILD_DIR/spirv-tools" --target spirv-val --parallel "$JOBS"
    SPIRV_VAL="$TOOLS_BUILD_DIR/spirv-tools/tools/spirv-val"
fi

if test -z "${LAVAPIPE_ICD:-}"; then
    PATH="$(dirname -- "$GLSLANG_VALIDATOR"):$(dirname -- "$SPIRV_VAL"):$PATH"
    export PATH
    meson setup --reconfigure "$MESA_BUILD_DIR" "$MESA_DIR" \
        --buildtype=release \
        -Dvulkan-drivers=swrast -Dgallium-drivers=[] \
        -Dllvm=enabled -Dshared-llvm=enabled \
        -Dplatforms=[] -Dopengl=false -Dgles1=disabled -Dgles2=disabled \
        -Degl=disabled -Dglx=disabled -Dgbm=disabled -Dbuild-tests=false
    meson compile -C "$MESA_BUILD_DIR" --jobs "$JOBS"
    LAVAPIPE_ICD="$MESA_BUILD_DIR/src/gallium/targets/lavapipe/lvp_devenv_icd.$(uname -m).json"
fi
if ! test -f "$LAVAPIPE_ICD"; then
    printf 'Lavapipe ICD not found: %s\n' "$LAVAPIPE_ICD" >&2
    exit 1
fi

cmake -S "$ROOT" -B "$BUILD_DIR" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DVULKAN_HEADERS_DIR="$VULKAN_HEADERS_DIR" \
    -DGLSLANG_VALIDATOR="$GLSLANG_VALIDATOR" \
    -DSPIRV_VAL="$SPIRV_VAL"
cmake --build "$BUILD_DIR" --target ext_cmm --parallel "$JOBS"