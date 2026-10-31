#!/usr/bin/env bash
# Boot the emulator with the requested -gpu mode and run the headless trainer
# smoke test in one session (adb's device state goes stale between shells).
set -u

GPU_MODE="${1:-swiftshader_indirect}"
ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native"
EMULATOR="C:/Users/28928/AppData/Local/Android/Sdk/emulator/emulator.exe"
DATASET="$ROOT/testdata/tiny_colmap"

export ANDROID_SDK_HOME="C:/Users/28928"
export ANDROID_USER_HOME="C:/Users/28928/.android"
export MSYS_NO_PATHCONV=1
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"

taskkill //F //IM qemu-system-x86_64-headle.exe //IM emulator.exe >/dev/null 2>&1
sleep 3

cd "$ROOT" || exit 1
"$EMULATOR" -avd Medium_Phone_API_36.1 -no-window -no-audio -no-boot-anim \
  -gpu "$GPU_MODE" -memory 3072 -no-snapshot-save \
  > emulator_headless.out 2> emulator_headless.err &
EMU_PID=$!

echo "booting emulator (gpu=$GPU_MODE)..."
$ADB kill-server >/dev/null 2>&1
sleep 2
$ADB start-server >/dev/null 2>&1
for _ in $(seq 1 40); do
  sleep 8
  S="$($ADB devices 2>&1 | grep emulator | tr -d '\r')"
  if echo "$S" | grep -q "device$"; then
    B="$($ADB shell getprop sys.boot_completed 2>&1 | tr -d '\r')"
    [ "$B" = "1" ] && { echo "booted: $S"; break; }
  fi
  echo "  waiting: $S"
done

echo "=== vulkan driver ==="
$ADB shell "ls /vendor/lib64/hw/ 2>&1 | head -20"
echo "=== adapter ==="
$ADB shell "cd /data/local/tmp && RUST_LOG=info ./libbrushheadless.so --path /data/local/tmp/tiny_colmap --export-path /data/local/tmp/out.ply --total-steps 30 --max-resolution 128 --max-splats 50000 --sh-degree 0 --refine-every 10 --growth-stop-iter 25 --export-every 10 --seed 42" 2>&1 \
  | grep -v "INFO  cubecl_wgpu"
echo "RUN_EXIT=$?"

echo "=== result ==="
$ADB shell "ls -l /data/local/tmp/out.ply 2>&1; head -c 32 /data/local/tmp/out.ply 2>&1"
mkdir -p "$ROOT/out"
$ADB pull /data/local/tmp/out.ply "$ROOT/out/out.ply" 2>&1 | tail -2
ls -l "$ROOT/out/out.ply" 2>&1

kill $EMU_PID 2>/dev/null
