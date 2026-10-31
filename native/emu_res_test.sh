#!/usr/bin/env bash
# One boot. Run the patched trainer on the same 128px dataset but with a
# decreasing --max-resolution, so the reduce sizes shrink without changing the
# input files. Also try the reduced autotune settings on the patched build.
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

$ADB shell "rm -rf /data/local/tmp/tiny_colmap"
$ADB push testdata/tiny_colmap /data/local/tmp/tiny_colmap >/dev/null
$ADB push prebuilt/x86_64/libbrushheadless.so /data/local/tmp/libbrushheadless.so >/dev/null
$ADB shell "chmod 755 /data/local/tmp/libbrushheadless.so"

run_case() {
  local name="$1" res="$2"; shift 2
  echo "===== CASE $name (res=$res, env: $*) ====="
  $ADB shell "rm -f /data/local/tmp/out.ply /data/local/tmp/stop /data/local/tmp/pause"
  $ADB shell "cd /data/local/tmp && $* RUST_LOG=info ./libbrushheadless.so \
      --path /data/local/tmp/tiny_colmap \
      --export-path /data/local/tmp/out.ply \
      --total-steps 30 --max-resolution $res --max-splats 50000 \
      --sh-degree 0 --refine-every 10 --growth-stop-iter 25 \
      --export-every 10 --seed 42" 2>&1 | grep -v "INFO  cubecl_wgpu"
  echo "--- ply ---"
  $ADB shell "ls -l /data/local/tmp/out.ply 2>&1; head -c 400 /data/local/tmp/out.ply 2>&1 | tr -d '\0'"
  echo
}

{
  run_case "G_res32" 32
  run_case "H_res64" 64
  run_case "I_res128_maxtasks1_autotune0" 128 "CUBECL_WGPU_MAX_TASKS=1 CUBECL_AUTOTUNE_LEVEL=0"
} > "$ROOT/emu_res_test.log" 2>&1

cat "$ROOT/emu_res_test.log"
taskkill //F //IM qemu-system-x86_64.exe //IM emulator.exe >/dev/null 2>&1
