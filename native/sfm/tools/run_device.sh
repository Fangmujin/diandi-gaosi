#!/bin/bash
# 在已连接的 Android 设备上跑一次真实 SfM
# 本机 adb 极不稳定(daemon 会被回收), 所以整个流程必须在一次 bash 调用内完成,
# 并且自己 start-server + 轮询等待设备上线。
set -u
ABI="${1:-x86_64}"
FRAMES="$2"
HOST_OUT="$3"
shift 3 || true
EXTRA="$*"

ROOT="$(cd "$(dirname "$0")" && cd .. && pwd)"
SO_WIN="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native/prebuilt/$ABI/libiii_sfm.so"
FRAMES_WIN="$(cd "$FRAMES" && pwd -W 2>/dev/null || echo "$FRAMES")"
HOST_OUT_WIN="$(cd "$HOST_OUT" 2>/dev/null && pwd -W || echo "$HOST_OUT")"
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEVICE_DIR=/data/local/tmp/iii_sfm_test

export MSYS_NO_PATHCONV=1

[ -f "$SO_WIN" ] || { echo "missing $SO_WIN"; exit 1; }
[ -d "$FRAMES" ] || { echo "missing frames dir"; exit 1; }
mkdir -p "$HOST_OUT"

"$ADB" start-server >/dev/null 2>&1
for i in $(seq 1 40); do
  if "$ADB" devices 2>/dev/null | grep -qw "device$"; then break; fi
  sleep 3
done
"$ADB" devices

"$ADB" shell "rm -rf $DEVICE_DIR; mkdir -p $DEVICE_DIR/frames $DEVICE_DIR/colmap"
"$ADB" push "$SO_WIN" "$DEVICE_DIR/libiii_sfm.so"
"$ADB" push "$FRAMES_WIN/." "$DEVICE_DIR/frames"
"$ADB" shell "chmod -R 755 $DEVICE_DIR"
echo "--- running on device ---"
"$ADB" shell "cd $DEVICE_DIR && ./libiii_sfm.so --images $DEVICE_DIR/frames --out $DEVICE_DIR/colmap $EXTRA 2>&1; echo EXIT=\$?"
echo "--- pulling results ---"
"$ADB" pull "$DEVICE_DIR/colmap/." "$HOST_OUT_WIN"
ls -la "$HOST_OUT"
