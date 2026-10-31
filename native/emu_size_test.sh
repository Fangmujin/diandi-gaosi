#!/usr/bin/env bash
# One boot, then run the patched trainer on progressively larger inputs to see
# whether the gfxstream crash depends on the buffer sizes involved.
set -u
ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native"
EMULATOR="C:/Users/28928/AppData/Local/Android/Sdk/emulator/emulator.exe"
export ANDROID_SDK_HOME="C:/Users/28928"
export ANDROID_USER_HOME="C:/Users/28928/.android"
export MSYS_NO_PATHCONV=1
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"

taskkill //F //IM qemu-system-x86_64.exe //IM qemu-system-x86_64-headless.exe //IM emulator.exe >/dev/null 2>&1
sleep 3
cd "$ROOT" || exit 1
"$EMULATOR" -avd Medium_Phone_API_36.1 -no-window -no-audio -no-boot-anim \
  -gpu host -no-snapshot-save -no-snapshot-load \
  > emulator_headless.out 2> emulator_headless.err &

$ADB kill-server >/dev/null 2>&1
sleep 2
$ADB start-server >/dev/null 2>&1
for _ in $(seq 1 45); do
  sleep 8
  S="$($ADB devices 2>&1 | grep emulator | tr -d '\r')"
  if echo "$S" | grep -q "device$"; then
    B="$($ADB shell getprop sys.boot_completed 2>&1 | tr -d '\r')"
    [ "$B" = "1" ] && break
  fi
done

$ADB push prebuilt/x86_64/libbrushheadless.so /data/local/tmp/libbrushheadless.so >/dev/null
$ADB shell "chmod 755 /data/local/tmp/libbrushheadless.so"

run_size() {
  local ds="$1" res="$2"
  echo "===== SIZE $ds (max-resolution $res) ====="
  $ADB shell "rm -rf /data/local/tmp/ds"
  $ADB push "testdata/$ds" /data/local/tmp/ds >/dev/null
  $ADB shell "rm -f /data/local/tmp/out.ply /data/local/tmp/stop /data/local/tmp/pause"
  $ADB shell "cd /data/local/tmp && RUST_LOG=info ./libbrushheadless.so \
      --path /data/local/tmp/ds \
      --export-path /data/local/tmp/out.ply \
      --total-steps 30 --max-resolution $res --max-splats 50000 \
      --sh-degree 0 --refine-every 10 --growth-stop-iter 25 \
      --export-every 10 --seed 42" 2>&1 | grep -v "INFO  cubecl_wgpu"
  echo "--- ply ---"
  $ADB shell "ls -l /data/local/tmp/out.ply 2>&1; head -c 400 /data/local/tmp/out.ply 2>&1 | tr -d '\0'"
  echo
}

{
  run_size tiny32 32
  run_size tiny64 64
  run_size tiny_colmap 128
} > "$ROOT/emu_size_test.log" 2>&1

cat "$ROOT/emu_size_test.log"
taskkill //F //IM qemu-system-x86_64.exe //IM emulator.exe >/dev/null 2>&1
