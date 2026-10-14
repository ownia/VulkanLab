#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
. "$ROOT/../moltenvk_cooperative_matrix/config.sh"
sh "$ROOT/../../scripts/prepare_dependencies.sh" "$ROOT"
THIRD_PARTY_DIR=$(CDPATH='' cd -- "$ROOT/../../third_party" && pwd)
VULKAN_INCLUDE_DIR="$THIRD_PARTY_DIR/Vulkan-Headers/include"
SPIRV_CROSS_DIR="$BUILD_DIR/src/SPIRV-Cross"

if [ ! -f "$VULKAN_INCLUDE_DIR/vulkan/vulkan.h" ]; then
    printf 'Vulkan headers not found: %s\n' "$VULKAN_INCLUDE_DIR" >&2
    exit 1
fi

prepare_checkout() {
    source_dir=$1
    checkout_dir=$2
    revision=$(git -C "$source_dir" rev-parse HEAD)
    if [ ! -e "$checkout_dir" ]; then
        mkdir -p "$(dirname -- "$checkout_dir")"
        git clone --no-hardlinks --no-checkout "$source_dir" "$checkout_dir"
        git -C "$checkout_dir" checkout --detach "$revision"
    elif [ "$(git -C "$checkout_dir" rev-parse HEAD)" != "$revision" ]; then
        printf 'Source revision changed; move or remove the private checkout and retry: %s\n' \
            "$checkout_dir" >&2
        exit 1
    fi
}

prepare_checkout "$THIRD_PARTY_DIR/MoltenVK" "$MOLTENVK_DIR"
prepare_checkout "$THIRD_PARTY_DIR/SPIRV-Cross" "$SPIRV_CROSS_DIR"

PATCH_WORK_DIR=$(mktemp -d)
trap 'rm -rf "$PATCH_WORK_DIR"' EXIT
trap 'exit 1' HUP INT TERM

apply_patches() {
    repository=$1
    shift
    patch_dir="$PATCH_WORK_DIR/$(basename -- "$repository")"
    mkdir -p "$patch_dir/mail"
    git mailsplit -o"$patch_dir/mail" "$@" >/dev/null
    for mail in "$patch_dir"/mail/*; do
        patch_file="$patch_dir/$(basename -- "$mail").diff"
        git mailinfo /dev/null "$patch_file" < "$mail" >/dev/null
    done

    set --
    for patch in "$patch_dir"/*.diff; do
        set -- "$patch" "$@"
    done
    if (
        export GIT_INDEX_FILE="$patch_dir/index"
        git -C "$repository" read-tree HEAD || exit 1
        git -C "$repository" add -u || exit 1
        for patch in "$@"; do
            git -C "$repository" apply --cached --reverse "$patch" 2>/dev/null || exit 1
        done
    ); then
        printf 'Patches already applied: %s\n' "$repository"
        return
    fi

    for patch in "$patch_dir"/*.diff; do
        if git -C "$repository" apply --reverse --check "$patch" 2>/dev/null; then
            continue
        fi
        printf 'Applying patch %s to %s\n' "$(basename -- "$patch")" "$repository"
        git -C "$repository" apply "$patch"
    done
}

apply_patches "$MOLTENVK_DIR" \
    "$ROOT/../moltenvk_cooperative_matrix/patch/moltenvk/2753.patch" \
    "$ROOT/../moltenvk_cooperative_matrix/patch/moltenvk/0001-Pre-specialize-cooperative-matrix-shaders-before-MSL.patch" \
    "$ROOT/patch/moltenvk/0001-Add-experimental-cooperative-matrix-maintenance1.patch"
apply_patches "$SPIRV_CROSS_DIR" \
    "$ROOT/../moltenvk_cooperative_matrix/patch/spirv-cross/0001-Fix-MSL-cooperative-matrix-arrays-and-parameter-vali.patch" \
    "$ROOT/patch/spirv-cross/0001-Add-experimental-cooperative-matrix-maintenance1-MSL.patch"

cd "$MOLTENVK_DIR"
set -- --macos --spirv-cross-root "$SPIRV_CROSS_DIR" --v-headers-root "$THIRD_PARTY_DIR/Vulkan-Headers"
if [ "$CONFIGURATION" = Debug ]; then
    set -- "$@" --debug
fi
./fetchDependencies "$@"
xcodebuild build -project MoltenVKPackaging.xcodeproj \
    -scheme "MoltenVK Package (macOS only)" -configuration "$CONFIGURATION"

if [ ! -f "$MOLTENVK_LIBRARY" ]; then
    printf 'MoltenVK library not found: %s\n' "$MOLTENVK_LIBRARY" >&2
    exit 1
fi
cmake -S "$THIRD_PARTY_DIR/glslang" -B "$BUILD_DIR/tools/glslang" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DENABLE_GLSLANG_BINARIES=ON -DENABLE_SPIRV=ON \
    -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DENABLE_HLSL=OFF
cmake --build "$BUILD_DIR/tools/glslang" --target glslang-standalone --parallel "${JOBS:-4}"
cmake -S "$THIRD_PARTY_DIR/SPIRV-Tools" -B "$BUILD_DIR/tools/spirv-tools" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DSPIRV-Headers_SOURCE_DIR="$THIRD_PARTY_DIR/SPIRV-Headers" \
    -DSPIRV_SKIP_TESTS=ON -DSPIRV_SKIP_EXECUTABLES=OFF
cmake --build "$BUILD_DIR/tools/spirv-tools" --target spirv-val spirv-dis --parallel "${JOBS:-4}"
cmake -S "$ROOT" -B "$BUILD_DIR/$CONFIGURATION" -G Ninja \
    -DCMAKE_BUILD_TYPE="$CONFIGURATION" -DVulkan_LIBRARY="$MOLTENVK_LIBRARY" \
    -DVulkan_INCLUDE_DIR="$VULKAN_INCLUDE_DIR" \
    -DGLSLANG_VALIDATOR="$BUILD_DIR/tools/glslang/StandAlone/glslang" \
    -DSPIRV_VAL="$BUILD_DIR/tools/spirv-tools/tools/spirv-val"
cmake --build "$BUILD_DIR/$CONFIGURATION" --parallel "${JOBS:-4}"