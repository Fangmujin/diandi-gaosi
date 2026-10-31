#!/usr/bin/env bash
# scale 正则扫描：ds_44 @ 6000 步（快档实际档位），只改 --scale-loss-weight。
#
# 为什么放在这里：证明 brush-headless 的 CLI 现在能透传 TrainConfig 的正则参数
# （原来 HeadlessArgs 只透传了 7 个字段，scale/opac 正则拿不到）。
# 中间产物全部写 D:（C 盘只剩 ~5 GB）。
#
# 用法: bash native/tools/sweep_scale_reg.sh [额外的一组 "标签 参数"，可重复]
set -u

REPO=/c/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/native
EXE=/d/brush-target/release/brush_headless.exe
DATA=$REPO/testdata/ds_44
OUT=/d/brushsweep
STEPS=6000

mkdir -p "$OUT"

run_one() {
  local tag=$1; shift
  local ply=$OUT/$tag.ply
  echo "=== $tag  $* ==="
  "$EXE" --path "$DATA" --export-path "$ply" --total-steps "$STEPS" \
         --export-every 6000 --seed 42 "$@" \
         > "$OUT/$tag.stdout.log" 2> "$OUT/$tag.stderr.log"
  echo "EXIT=$? size=$(stat -c %s "$ply" 2>/dev/null)"
  tail -1 "$OUT/$tag.stdout.log"
}

# 基线 1e-8 已有（ds44_s6000.ply，旧二进制但默认值完全一致，直接复用，省 110 s）
run_one slw_1e-6 --scale-loss-weight 1e-6
run_one slw_1e-5 --scale-loss-weight 1e-5
run_one slw_1e-4 --scale-loss-weight 1e-4
run_one slw_1e-3 --scale-loss-weight 1e-3

echo "ALL DONE"
