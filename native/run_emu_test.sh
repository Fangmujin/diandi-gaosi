#!/usr/bin/env bash
# Smoke test the x86_64 Android build inside the emulator:
# push the binary + a tiny synthetic COLMAP dataset, train a few steps, and
# check that a ply actually comes out.
set -u

ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native"
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
SO="$ROOT/prebuilt/x86_64/libbrushheadless.so"
DATA="${1:-/tmp/tiny_colmap}"
STEPS="${STEPS:-30}"

# adb looks for its keys under $ANDROID_SDK_HOME; without this the server may
# generate a fresh key and the emulator stays stuck in "authorizing".
export ANDROID_SDK_HOME="C:/Users/28928"
export ANDROID_USER_HOME="C:/Users/28928/.android"
# Stop Git Bash from rewriting /data/... into a Windows path for adb.
export MSYS_NO_PATHCONV=1

[ -f "$SO" ] || { echo "missing $SO" >&2; exit 1; }
[ -d "$DATA" ] || { echo "missing dataset $DATA" >&2; exit 1; }

DEVICE=""
for _ in 1 2 3 4 5 6; do
  DEVICE="$($ADB devices 2>&1 | grep emulator | tr -d '\r')"
  case "$DEVICE" in *"	device"*|*" device"*) break ;; esac
  sleep 5
done
case "$DEVICE" in
  *device*) ;;
  *) echo "no device: $DEVICE" >&2; exit 1 ;;
esac

echo "=== pushing ==="
$ADB push "$SO" /data/local/tmp/libbrushheadless.so
$ADB push "$DATA" /data/local/tmp/tiny_colmap
$ADB shell "chmod 755 /data/local/tmp/libbrushheadless.so; ls -l /data/local/tmp/libbrushheadless.so"

echo "=== running ==="
$ADB shell "cd /data/local/tmp && \
  RUST_LOG=info BRUSH_MAX_CACHE_MB=64 \
  ./libbrushheadless.so \
    --path /data/local/tmp/tiny_colmap \
    --export-path /data/local/tmp/out.ply \
    --total-steps $STEPS \
    --max-resolution 128 \
    --max-splats 50000 \
    --sh-degree 0 \
    --refine-every 10 \
    --growth-stop-iter 25 \
    --export-every 10 \
    --seed 42"
echo "RUN_EXIT=$?"

echo "=== result ==="
$ADB shell "ls -l /data/local/tmp/out.ply; head -c 16 /data/local/tmp/out.ply"
mkdir -p "$ROOT/out"
$ADB pull /data/local/tmp/out.ply "$ROOT/out/out.ply"
ls -l "$ROOT/out/out.ply"
