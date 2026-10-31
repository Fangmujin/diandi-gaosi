#!/usr/bin/env bash
# Run one bounded chunk of the Android build. Output to log; print compact status.
set -u
cd "C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native/brush" || exit 1
export CARGO_TARGET_DIR=D:/bt-shrink
NDK="C:/Users/28928/AppData/Local/Android/Sdk/ndk/27.2.12479018/toolchains/llvm/prebuilt/windows-x86_64/bin"
export PATH="$NDK;C:/Users/28928/.cargo/bin;$PATH"
export RUSTUP_HOME="C:/Users/28928/.rustup"
export CARGO_HOME="C:/Users/28928/.cargo"
export CARGO_BUILD_JOBS=2
export RUST_MIN_STACK=67108864
export CARGO_PROFILE_RELEASE_OPT_LEVEL=2
export CARGO_PROFILE_RELEASE_DEBUG=0
export CC_aarch64_linux_android="$NDK/aarch64-linux-android24-clang.cmd"
export AR_aarch64_linux_android="$NDK/llvm-ar.exe"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$NDK/aarch64-linux-android24-clang.cmd"

DUR="${1:-480}"
LOG="D:/bt-shrink/chunk_$(date +%H%M%S).log"
timeout "$DUR" "C:/Users/28928/.cargo/bin/cargo.exe" build --release -p brush-headless \
  --bin brush_headless --target aarch64-linux-android > "$LOG" 2>&1
RC=$?
LAST=$(grep "Compiling" "$LOG" | tail -1)
NRUST=$(grep -c "Compiling" "$LOG")
echo "chunk_rc=$RC compiled_lines=$NRUST log=$LOG"
echo "last: $LAST"
if grep -q "error\[" "$LOG" || grep -q "^error" "$LOG"; then
  echo "!!! BUILD ERROR - see $LOG"
  grep -B2 -A8 "^error" "$LOG" | head -40
fi
if [ -f "D:/bt-shrink/aarch64-linux-android/release/brush_headless.exe" ] || [ -f "D:/bt-shrink/aarch64-linux-android/release/brush_headless" ]; then
  echo "FINAL_BINARY_EXISTS"
  ls -la "D:/bt-shrink/aarch64-linux-android/release/brush_headless"* 2>/dev/null
fi
