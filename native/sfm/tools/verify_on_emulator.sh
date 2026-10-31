#!/bin/bash
# 在 Android 模拟器/真机上跑一次真实的 SfM 验证
#
# 用法: bash tools/verify_on_emulator.sh <abi> <frames_dir> <host_out_dir> [extra args...]
#   abi          : x86_64 | arm64-v8a
#   frames_dir   : 本机存放抽帧图片的目录
#   host_out_dir : 本机目录, 用于把设备上的 COLMAP 产物拉回来
#
# 注意: adb 在本机不稳定, 所以所有 adb 调用都放在同一条命令里,
#       并且用 MSYS_NO_PATHCONV=1 防止 Git-Bash 把 /data/... 转成 Windows 路径。
set -u

ABI="${1:-x86_64}"
FRAMES="$2"
HOST_OUT="$3"
shift 3 || true
EXTRA="$*"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SO="$ROOT/../prebuilt/$ABI/libiii_sfm.so"
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEVICE_DIR=/data/local/tmp/iii_sfm_test

if [ ! -f "$SO" ]; then echo "missing $SO"; exit 1; fi
if [ ! -d "$FRAMES" ]; then echo "missing frames dir $FRAMES"; exit 1; fi
mkdir -p "$HOST_OUT"

"$ADB" start-server
sleep 3
MSYS_NO_PATHCONV=1 "$ADB" wait-for-device

MSYS_NO_PATHCONV=1 "$ADB" shell "rm -rf $DEVICE_DIR; mkdir -p $DEVICE_DIR/frames $DEVICE_DIR/colmap"
MSYS_NO_PATHCONV=1 "$ADB" push "$SO" "$DEVICE_DIR/libiii_sfm.so"
MSYS_NO_PATHCONV=1 "$ADB" push "$FRAMES/." "$DEVICE_DIR/frames"
MSYS_NO_PATHCONV=1 "$ADB" shell "chmod -R 755 $DEVICE_DIR"

echo "--- running on device ---"
MSYS_NO_PATHCONV=1 "$ADB" shell \
  "cd $DEVICE_DIR && ./libiii_sfm.so --images $DEVICE_DIR/frames --out $DEVICE_DIR/colmap $EXTRA 2>&1; echo EXIT=\$?"

echo "--- pulling results ---"
MSYS_NO_PATHCONV=1 "$ADB" pull "$DEVICE_DIR/colmap/." "$HOST_OUT"
ls -la "$HOST_OUT"
