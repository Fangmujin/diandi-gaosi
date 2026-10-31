#!/bin/bash
# 长时间跑: 在设备上用 nohup 后台执行并把 stdout 写进日志文件,
# 本机再轮询日志。这样即使单次 adb shell 连接被重置也不会丢结果。
set -u
ABI="${1:-x86_64}"
FRAMES="$2"
HOST_OUT="$3"
shift 3 || true
EXTRA="$*"

SO_WIN="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native/prebuilt/$ABI/libiii_sfm.so"
FRAMES_WIN="$(cd "$FRAMES" && pwd -W 2>/dev/null || echo "$FRAMES")"
HOST_OUT_WIN="$(cd "$HOST_OUT" 2>/dev/null && pwd -W || echo "$HOST_OUT")"
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEVICE_DIR=/data/local/tmp/iii_sfm_test

export MSYS_NO_PATHCONV=1
mkdir -p "$HOST_OUT"

# adb daemon 每次 bash 调用结束都会被回收, 所以这里必须自己拉起并等设备上線
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
"$ADB" shell "cd $DEVICE_DIR && nohup ./libiii_sfm.so --images $DEVICE_DIR/frames --out $DEVICE_DIR/colmap $EXTRA > $DEVICE_DIR/run.log 2>&1 & echo started"

for i in $(seq 1 200); do
  sleep 15
  line=$("$ADB" shell "tail -n 1 $DEVICE_DIR/run.log 2>/dev/null" 2>/dev/null | tr -d '\r')
  if [ -z "$line" ]; then continue; fi
  case "$line" in
    *'"stage":"done"'*|*'"stage":"error"'*)
      echo "[poll $i] $line"
      break
      ;;
  esac
  if [ $((i % 4)) -eq 0 ]; then echo "[poll $i] $line"; fi
done

echo "--- full log tail ---"
"$ADB" shell "cat $DEVICE_DIR/run.log" 2>/dev/null | tail -20
echo "--- pulling results ---"
"$ADB" pull "$DEVICE_DIR/colmap/." "$HOST_OUT_WIN"
ls -la "$HOST_OUT"
