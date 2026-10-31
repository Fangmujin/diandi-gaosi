#!/usr/bin/env bash
# Cold boot the emulator with a larger RAM config and run the headless
# trainer once, unchanged, to decide whether the crash is memory related.
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

# No -memory override: hw.ramSize from config.ini (now 4096) applies.
"$EMULATOR" -avd Medium_Phone_API_36.1 -no-window -no-audio -no-boot-anim \
  -gpu host -no-snapshot-save -no-snapshot-load \
  > emulator_headless.out 2> emulator_headless.err &
EMU_PID=$!

$ADB kill-server >/dev/null 2>&1
sleep 2
$ADB start-server >/dev/null 2>&1
echo "booting..."
for _ in $(seq 1 40); do
  sleep 8
  S="$($ADB devices 2>&1 | grep emulator | tr -d '\r')"
  if echo "$S" | grep -q "device$"; then
    B="$($ADB shell getprop sys.boot_completed 2>&1 | tr -d '\r')"
    [ "$B" = "1" ] && { echo "booted: $S"; break; }
  fi
done

{
  echo "===== meminfo ====="
  $ADB shell "cat /proc/meminfo | head -4"
  echo "===== leftover processes ====="
  $ADB shell "ps -A | grep -i brush" || true
  echo
  $ADB push prebuilt/x86_64/libbrushheadless.so /data/local/tmp/libbrushheadless.so
  $ADB shell "rm -rf /data/local/tmp/tiny_colmap"
  $ADB push testdata/tiny_colmap /data/local/tmp/tiny_colmap
  $ADB shell "find /data/local/tmp/tiny_colmap -name cameras.txt"
  $ADB shell "chmod 755 /data/local/tmp/libbrushheadless.so"

  echo "===== RUN (ram 4096, unchanged args) ====="
  $ADB shell "rm -f /data/local/tmp/out.ply /data/local/tmp/stop /data/local/tmp/pause"
  $ADB shell "cd /data/local/tmp && RUST_LOG=info ./libbrushheadless.so \
      --path /data/local/tmp/tiny_colmap \
      --export-path /data/local/tmp/out.ply \
      --total-steps 30 --max-resolution 128 --max-splats 50000 \
      --sh-degree 0 --refine-every 10 --growth-stop-iter 25 \
      --export-every 10 --seed 42" 2>&1 | grep -v "INFO  cubecl_wgpu"
  echo "===== RESULT ====="
  $ADB shell "ls -l /data/local/tmp/out.ply 2>&1; head -c 300 /data/local/tmp/out.ply 2>&1 | tr -d '\0'"
  echo
  echo "===== meminfo after ====="
  $ADB shell "cat /proc/meminfo | head -4"
} > "$ROOT/emu_ram_test.log" 2>&1

cat "$ROOT/emu_ram_test.log"
kill $EMU_PID 2>/dev/null
