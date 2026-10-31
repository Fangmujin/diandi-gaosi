#!/bin/bash
# 把 SfM 产出的 COLMAP 数据集喂给 brush 训练器, 在设备上跑一遍引擎级串联。
#
# 用法: bash tools/run_brush.sh <dataset_dir> <steps> [max_resolution]
#
# 注意: 本机 adb daemon 每次 bash 调用结束就被回收, 所以全流程必须在一次调用内完成。
#       训练器是 PIE (libbrushheadless.so), 必须 exec; 日志同时收 stdout(JSONL) 和 stderr(RUST_LOG)。
set -u
DS="${1:-}"
STEPS="${2:-30}"
MAXRES="${3:-128}"
[ -n "$DS" ] || { echo "usage: run_brush.sh <dataset_dir> <steps> [max_resolution]"; exit 1; }

DS_WIN="$(cd "$DS" && pwd -W 2>/dev/null || echo "$DS")"
NAME="$(basename "$DS")"
HOST_OUT_WIN="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native/testdata/brush_out_$NAME"
mkdir -p "C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native/testdata/brush_out_$NAME"

BRUSH="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native/prebuilt/x86_64/libbrushheadless.so"
ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
DEVICE_DIR=/data/local/tmp/brush_test

export MSYS_NO_PATHCONV=1

"$ADB" start-server >/dev/null 2>&1
for i in $(seq 1 40); do
  if "$ADB" devices 2>/dev/null | grep -qw "device$"; then break; fi
  sleep 3
done
"$ADB" devices

echo "===== meminfo before ====="
"$ADB" shell "cat /proc/meminfo | head -5; ulimit -s"

"$ADB" shell "rm -rf $DEVICE_DIR; mkdir -p $DEVICE_DIR"
"$ADB" push "$BRUSH" "$DEVICE_DIR/libbrushheadless.so"
"$ADB" push "$DS_WIN" "$DEVICE_DIR/ds"
"$ADB" shell "chmod -R 755 $DEVICE_DIR"
"$ADB" shell "rm -f $DEVICE_DIR/stop $DEVICE_DIR/pause"

echo "===== dataset on device ====="
"$ADB" shell "ls $DEVICE_DIR/ds; ls $DEVICE_DIR/ds/sparse/0; ls $DEVICE_DIR/ds/images | head -3; ls $DEVICE_DIR/ds/images | wc -l"

"$ADB" shell "cd $DEVICE_DIR && RUST_LOG=info \
  nohup sh -c './libbrushheadless.so --path $DEVICE_DIR/ds \
      --export-path $DEVICE_DIR/out_$NAME.ply \
      --total-steps $STEPS --max-resolution $MAXRES --max-splats 50000 \
      --sh-degree 0 --refine-every 10 --growth-stop-iter 25 \
      --export-every 10 --seed 42 > $DEVICE_DIR/run.log 2>&1; echo EXIT=\$? >> $DEVICE_DIR/run.log' \
  > /dev/null 2>&1 & echo started"

for i in $(seq 1 120); do
  sleep 10
  line=$("$ADB" shell "tail -n 1 $DEVICE_DIR/run.log 2>/dev/null" 2>/dev/null | tr -d '\r')
  case "$line" in
    EXIT=*) echo "[poll $i] $line"; break ;;
  esac
  if [ $((i % 3)) -eq 0 ]; then echo "[poll $i] $line"; fi
done

echo "===== full log ====="
"$ADB" shell "cat $DEVICE_DIR/run.log" 2>/dev/null

echo "===== meminfo after / tombstones ====="
"$ADB" shell "cat /proc/meminfo | head -5"
"$ADB" shell "ls -lt /data/tombstones/ 2>/dev/null | head -6"

echo "===== ply ====="
"$ADB" shell "ls -l $DEVICE_DIR/*.ply 2>&1"
"$ADB" shell "head -c 300 $DEVICE_DIR/out_$NAME.ply 2>&1 | tr -d '\0'"

echo "===== pulling ====="
"$ADB" pull "$DEVICE_DIR/out_$NAME.ply" "$HOST_OUT_WIN" 2>&1
"$ADB" pull "$DEVICE_DIR/run.log" "$HOST_OUT_WIN" 2>&1
ls -la "$HOST_OUT_WIN"
