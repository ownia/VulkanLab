#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$ROOT/config.sh"
THIRD_PARTY_DIR=$(CDPATH= cd -- "$ROOT/../../third_party" && pwd)
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
        git mailinfo /dev/null "$patch_dir/$(basename -- "$mail").diff" < "$mail" >/dev/null
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
    "$ROOT/patch/moltenvk/2753.patch" \
    "$ROOT/patch/moltenvk/0001-Pre-specialize-cooperative-matrix-shaders-before-MSL.patch"
apply_patches "$SPIRV_CROSS_DIR" \
    "$ROOT/patch/spirv-cross/0001-Fix-MSL-cooperative-matrix-arrays-and-parameter-vali.patch"

cd "$MOLTENVK_DIR"
set -- --macos --spirv-cross-root "$SPIRV_CROSS_DIR"
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
for benchmark in vkpeak vk_cooperative_matrix_perf; do
    set --
    if [ "$benchmark" = vkpeak ]; then
        set -- -DNCNN_VULKAN=ON -DNCNN_SIMPLEVK=ON
    fi
    cmake -S "$THIRD_PARTY_DIR/$benchmark" -B "$BUILD_DIR/$benchmark/$CONFIGURATION" \
        -DCMAKE_BUILD_TYPE="$CONFIGURATION" \
        -DVulkan_LIBRARY="$MOLTENVK_LIBRARY" \
        -DVulkan_INCLUDE_DIR="$VULKAN_INCLUDE_DIR" "$@"
    cmake --build "$BUILD_DIR/$benchmark/$CONFIGURATION" --config "$CONFIGURATION" \
        --target "$benchmark" --parallel "${JOBS:-4}"
done