#!/usr/bin/env bash
# Boot the emulator twice with different GPU backends / memory sizes and run
# the patched trainer each time, dumping the native tombstone when it crashes.
set -u
ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native"
EMULATOR="C:/Users/28928/AppData/Local/Android/Sdk/emulator/emulator.exe"
export ANDROID_SDK_HOME="C:/Users/28928"
export ANDROID_USER_HOME="C:/Users/28928/.android"
export MSYS_NO_PATHCONV=1
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"

boot() {
  local gpu="$1" mem="$2"
  taskkill //F //IM qemu-system-x86_64.exe //IM qemu-system-x86_64-headless.exe //IM emulator.exe >/dev/null 2>&1
  sleep 3
  "$EMULATOR" -avd Medium_Phone_API_36.1 -no-window -no-audio -no-boot-anim \
    -gpu "$gpu" -memory "$mem" -no-snapshot-save -no-snapshot-load \
    > emulator_headless.out 2> emulator_headless.err &
  $ADB kill-server >/dev/null 2>&1
  sleep 2
  $ADB start-server >/dev/null 2>&1
  for _ in $(seq 1 45); do
    sleep 8
    S="$($ADB devices 2>&1 | grep emulator | tr -d '\r')"
    if echo "$S" | grep -q "device$"; then
      B="$($ADB shell getprop sys.boot_completed 2>&1 | tr -d '\r')"
      [ "$B" = "1" ] && return 0
    fi
  done
  return 1
}

run() {
  local name="$1"
  echo "===== CASE $name ====="
  $ADB shell "rm -rf /data/local/tmp/tiny_colmap"
  $ADB push testdata/tiny_colmap /data/local/tmp/tiny_colmap >/dev/null
  $ADB push prebuilt/x86_64/libbrushheadless.so /data/local/tmp/libbrushheadless.so >/dev/null
  $ADB shell "chmod 755 /data/local/tmp/libbrushheadless.so"
  $ADB shell "rm -f /data/local/tmp/out.ply /data/local/tmp/stop /data/local/tmp/pause"
  echo "--- meminfo ---"
  $ADB shell "cat /proc/meminfo | head -3"
  $ADB shell "cd /data/local/tmp && RUST_LOG=info ./libbrushheadless.so \
      --path /data/local/tmp/tiny_colmap \
      --export-path /data/local/tmp/out.ply \
      --total-steps 30 --max-resolution 128 --max-splats 50000 \
      --sh-degree 0 --refine-every 10 --growth-stop-iter 25 \
      --export-every 10 --seed 42" 2>&1 | grep -v "INFO  cubecl_wgpu"
  echo "--- ply ---"
  $ADB shell "ls -l /data/local/tmp/out.ply 2>&1; head -c 400 /data/local/tmp/out.ply 2>&1 | tr -d '\0'"
  echo "--- tombstone ---"
  $ADB shell "ls -t /data/tombstones 2>/dev/null | head -2"
  $ADB shell "for f in \$(ls -t /data/tombstones/* 2>/dev/null | head -1); do head -40 \$f; done" 2>&1 | head -45
  echo
}

{
  if boot host 4096; then run "E_hostgpu_ram4096"; else echo "boot failed (host/4096)"; fi
  if boot swiftshader_indirect 4096; then run "F_swiftshader_ram4096"; else echo "boot failed (swiftshader)"; fi
} > "$ROOT/emu_gpu2_test.log" 2>&1

cat "$ROOT/emu_gpu2_test.log"
taskkill //F //IM qemu-system-x86_64.exe //IM emulator.exe >/dev/null 2>&1
