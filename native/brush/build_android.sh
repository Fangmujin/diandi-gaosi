#!/usr/bin/env bash
# Cross compile brush-headless to Android.
#
# Usage: build_android.sh <abi>          # abi = arm64-v8a | x86_64
# Output binary is renamed to libbrushheadless.so so it can live in the APK's
# jniLibs/<abi>/ and be exec'd through ProcessBuilder.
set -u

ABI="${1:-arm64-v8a}"
case "$ABI" in
  arm64-v8a) TRIPLE="aarch64-linux-android"; CLANG_PREFIX="aarch64-linux-android24-clang" ;;
  x86_64)    TRIPLE="x86_64-linux-android";  CLANG_PREFIX="x86_64-linux-android24-clang" ;;
  *) echo "unknown abi: $ABI" >&2; exit 2 ;;
esac

ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native"
# Intermediate artifacts are ~5G per target; C: runs out of space easily.
# Set CARGO_TARGET_DIR (e.g. D:/bt2) to build on another drive.
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$ROOT/brush/target}"
NDK="C:/Users/28928/AppData/Local/Android/Sdk/ndk/27.2.12479018"
NDK_BIN="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin"

export PATH="$NDK_BIN;C:/Users/28928/.cargo/bin;$PATH"
export RUSTUP_HOME="C:/Users/28928/.rustup"
export CARGO_HOME="C:/Users/28928/.cargo"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"
export RUST_MIN_STACK=67108864
# Keep this at 2: it governs the HOST side (build scripts / proc macros), which is
# x86_64-pc-windows-msvc. Changing it invalidates every build script and forces a
# host relink, which fails because no MSVC linker is installed here.
# Android target size flags are in .cargo/config.toml [target.*.rustflags] and are
# appended after this, so they still win for the aarch64 artifacts.
export CARGO_PROFILE_RELEASE_OPT_LEVEL=2
export CARGO_PROFILE_RELEASE_DEBUG=0

case "$TRIPLE" in
  aarch64-linux-android)
    export CC_aarch64_linux_android="$NDK_BIN/$CLANG_PREFIX.cmd"
    export AR_aarch64_linux_android="$NDK_BIN/llvm-ar.exe"
    export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$NDK_BIN/$CLANG_PREFIX.cmd"
    ;;
  x86_64-linux-android)
    export CC_x86_64_linux_android="$NDK_BIN/$CLANG_PREFIX.cmd"
    export AR_x86_64_linux_android="$NDK_BIN/llvm-ar.exe"
    export CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER="$NDK_BIN/$CLANG_PREFIX.cmd"
    ;;
esac

cd "$ROOT/brush" || exit 1

MODE="${2:-build}"   # build | check

echo "=== brush-headless $MODE $TRIPLE (target=$CARGO_TARGET_DIR) ==="
if [ "$MODE" = "check" ]; then
  "C:/Users/28928/.cargo/bin/cargo.exe" check --release -p brush-headless --bin brush_headless --target "$TRIPLE"
else
  "C:/Users/28928/.cargo/bin/cargo.exe" build --release -p brush-headless --bin brush_headless --target "$TRIPLE" || exit 1

  SRC="$CARGO_TARGET_DIR/$TRIPLE/release/brush_headless.exe"
  [ -f "$SRC" ] || SRC="$CARGO_TARGET_DIR/$TRIPLE/release/brush_headless"
  OUT_DIR="$ROOT/prebuilt/$ABI"
  mkdir -p "$OUT_DIR"
  cp "$SRC" "$OUT_DIR/libbrushheadless.so"
  STRIP="$NDK_BIN/llvm-strip.exe"
  [ -f "$STRIP" ] && "$STRIP" "$OUT_DIR/libbrushheadless.so"
  echo "ARTIFACT: $OUT_DIR/libbrushheadless.so"
  ls -l "$OUT_DIR/libbrushheadless.so"
fi
