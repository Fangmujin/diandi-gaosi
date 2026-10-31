#!/usr/bin/env bash
# 真机验证脚本（VIVO X300 Pro / arm64）
#
# 目的：在真机上闭环验证两件事
#   阶段 1：训练引擎能否在真实 Vulkan 驱动下跑完并产出有效 PLY（命令行级，绕开 APK）
#   阶段 2：完整 APK 端到端（导入 → 运行 → 查看 → 图库）
#
# 用法：
#   MSYS_NO_PATHCONV=1 bash tools/device_verify.sh          # 跑全部
#   MSYS_NO_PATHCONV=1 bash tools/device_verify.sh train    # 只跑阶段 1
#   MSYS_NO_PATHCONV=1 bash tools/device_verify.sh app      # 只跑阶段 2
#
# 注意：本机 adb daemon 会随 bash 调用结束被回收，所以长时间任务一律
#       「设备端后台执行 + 本机轮询日志」，不要在一条 adb shell 里干等。

set -u
export MSYS_NO_PATHCONV=1

ADB="C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
NODE="C:/Users/28928/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
NODE="C:/Users/28928/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
ROOT="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi"
PREBUILT="$ROOT/native/prebuilt/arm64-v8a"
DATASET="$ROOT/native/testdata/std_dataset_11"
APK="$ROOT/dist/dian-di-gao-si-v0.1-arm64.apk"
OUT="$ROOT/native/testdata/real_device_out"
DEV="/data/local/tmp/diandi"

mkdir -p "$OUT"

step() { echo; echo "==================== $* ===================="; }

# ---------- 0. 设备检查 ----------
step "0. 设备检查"
"$ADB" start-server >/dev/null 2>&1
"$ADB" devices -l
ABI=$("$ADB" shell getprop ro.product.cpu.abi 2>/dev/null | tr -d '\r')
MODEL=$("$ADB" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
echo "cpu.abi = ${ABI:-<none>}   model = ${MODEL:-<none>}"
if [[ "${ABI:-}" != "arm64-v8a" ]]; then
  echo "!! 期望 arm64-v8a 真机，当前是 '${ABI:-<未连接>}'"
  echo "!! 请确认：已开 USB 调试、数据线已连、手机上点了「允许 USB 调试」"
  [[ "${ABI:-}" == "" ]] && exit 1
fi
"$ADB" shell "grep -i -m1 'MemTotal' /proc/meminfo" 2>/dev/null
"$ADB" shell "uname -a" 2>/dev/null

# ---------- 1. 命令行级训练验证 ----------
if [[ "${1:-all}" == "all" || "${1:-all}" == "train" ]]; then
  step "1. 训练引擎真机验证（命令行级）"

  SO="$PREBUILT/libbrushheadless.so"
  [[ -f "$SO" ]] || { echo "!! 缺 $SO"; exit 1; }
  [[ -d "$DATASET" ]] || { echo "!! 缺 $DATASET"; exit 1; }

  echo "-- push 引擎 + 数据集 --"
  "$ADB" shell "rm -rf $DEV; mkdir -p $DEV"
  "$ADB" push "$SO" "$DEV/libbrushheadless.so"
  "$ADB" push "$DATASET/." "$DEV/ds"
  "$ADB" shell "chmod 755 $DEV/libbrushheadless.so && ls -la $DEV/ && ls $DEV/ds | head"

  # 防旧包闸门：我们已连续四次栽在"拿到的是旧二进制"上（最近一次是拿着 10-14 21:05 的
  # 旧引擎去试 --scale-loss-weight，误判成"CLI 没这个参数"）。
  # 用字节数而非 sha256 判，因为 Android 上 sha256sum 不一定存在（toybox 版本差异）。
  # 三个已知字节数互不相同，足够区分：
  #   70,878,536 = 当前（带 --scale-loss-weight / --aux-loss-time，04:32 产物）
  #   70,867,320 / 70,870,192 = 两个历史旧版，均不含新参数
  EXPECTED_SIZE=70878536
  ACTUAL_SIZE=$("$ADB" shell "stat -c %s $DEV/libbrushheadless.so" 2>/dev/null | tr -d '\r')
  echo "-- 设备端引擎字节数：${ACTUAL_SIZE}（期望 ${EXPECTED_SIZE}）--"
  if [[ "${ACTUAL_SIZE}" != "${EXPECTED_SIZE}" ]]; then
    echo "!! 设备上的引擎不是预期版本，中止。真机出问题时会浪费一整轮排查才知道是旧包。"
    exit 1
  fi
  echo "OK：确认是带尺度/aux 正则参数的当前引擎"

  # 1a. 30 步 smoke：快，用来确认「能不能跑起来」
  step "1a. smoke test：30 步"
  "$ADB" shell "cd $DEV && RUST_LOG=info ./libbrushheadless.so \
      --path $DEV/ds --export-path $DEV/smoke.ply \
      --total-steps 30 --max-resolution 512 --max-splats 200000 \
      --sh-degree 1 --refine-every 10 --growth-stop-iter 25 \
      --export-every 10 --seed 42 2>$DEV/smoke.err; echo EXIT=\$?" 2>&1 | tail -30
  echo "-- smoke.ply header --"
  "$ADB" shell "head -c 300 $DEV/smoke.ply 2>/dev/null | tr -d '\0' | head -20" 2>&1

  # 1b. 完整快档：参数与桌面生产口径逐字对齐，这样真机产出的 PLY 可以直接和
  #     D:\brushsweep\prod_fast_reg.ply（60,338 splats / p50 0.0152 / 雾最坏 0.039）横向比。
  #     别照着二进制默认值跑 —— 那批数已经作废过一次。
  step "1b. 完整训练：快档 6000 步（设备端后台 + 轮询，最多等 50 分钟）"
  "$ADB" shell "cd $DEV && nohup env RUST_LOG=info ./libbrushheadless.so \
      --path $DEV/ds --export-path $DEV/out.ply \
      --total-steps 6000 --max-resolution 960 --max-splats 1500000 \
      --sh-degree 1 --refine-every 100 --growth-stop-iter 4000 \
      --export-every 2000 --seed 42 \
      --scale-loss-weight 1e-5 --aux-loss-time 1.0 \
      >$DEV/train.log 2>$DEV/train.err & echo STARTED"

  for i in $(seq 1 150); do
    sleep 20
    LAST=$("$ADB" shell "tail -1 $DEV/train.log 2>/dev/null" 2>/dev/null | tr -d '\r')
    echo "  [$(($i*20))s] ${LAST:0:160}"
    if "$ADB" shell "grep -q '\"stage\":\"done\"' $DEV/train.log 2>/dev/null" 2>/dev/null; then
      echo "  -> 训练完成"; break
    fi
    if [[ -z "$LAST" && $i -gt 3 ]]; then echo "  !! 无输出，可能已崩"; break; fi
  done

  echo "-- 末条进度 --"
  "$ADB" shell "tail -1 $DEV/train.log" 2>&1
  echo "-- stderr 尾部 --"
  "$ADB" shell "tail -5 $DEV/train.err 2>/dev/null" 2>&1
  echo "-- out.ply header（关键：element vertex 必须 > 0）--"
  "$ADB" shell "ls -la $DEV/out.ply 2>/dev/null; head -c 400 $DEV/out.ply 2>/dev/null | tr -d '\0' | head -20" 2>&1

  echo "-- 拉回本地留档 --"
  "$ADB" pull "$DEV/out.ply" "$OUT/out.ply" 2>&1 | tail -2
  "$ADB" pull "$DEV/train.log" "$OUT/train.log" 2>&1 | tail -2
  ls -la "$OUT"

  # 真机产出 vs 桌面生产口径。桌面基准（同参数、同 seed）：
  #   prod_fast_reg.ply 60,338 splats / p50 0.0152 / 最坏格·典型 0.039 / 5.55 MB
  #   prod_1e5_r2.ply   60,884 splats / p50 0.0155 / 最坏格·典型 0.051 / 5.60 MB
  #
  # ⚠ 三条读数纪律（这次一条条踩出来的，别跳过）：
  #   ① 雾的绝对值跨实现不可比（两套实现量纲不同），只能比同实现、同口径。
  #   ② 「稳定」是**格位属性**，不是档位属性。6 个格位（初始/最坏/中位 × 上界/典型）里
  #      1e-5 与 1e-6 的复现性排序翻了两次。决策只用**最坏格**，依据也只在最坏格成立。
  #      桌面最坏格：1e-5 两跑 0.039 / 0.051；1e-6 两跑 0.089 / 0.929。
  #   ③ **n=2 不是方差估计**。两样本极差本身波动极大（对数正态下单次极差轻松到 3–10×，
  #      真实方差可以很小）。「差 10×」只能读作「这两次跑差了 10×」，
  #      不能读作「这一档方差 10×」。要断言不可复现需 n≥3。
  #
  # 所以真机判定 —— 雾是**触发补跑**的判据，不是判优判劣的判据：
  #   ① splats 落在 6 万 ±10%   → 通过（单调量，单次可作数）
  #   ② 每档至少跑 2 次、最好 3 次，取**中位数**再判；单次结果无论好坏都不能定档
  #   ③ 最坏格·典型 > 0.5（补跑触发线）→ 不作数，补跑取中位数后再判
  #      0.5 只是落在安全区内的一个取值，不是最优值。真正的边界是扫出来的：
  #        ≥ 0.93  判据失效 —— 连已知明显有雾的基线（0.984）都会被判合格，自相矛盾
  #        ≤ 0.051 判决翻转 —— 1e-5 自己会被判不合格
  #      判决在 (0.051, 0.929) 这整段里不变（宽 18×），所以不依赖线的具体取值。
  #
  # ⚠ 别写「离线还有 N 倍余量，再来波动也不会跨」—— 这句话已经算错过一次：
  #    实际余量是 7.65× 和 5.87×，都小于这批数据里已实测到的 10.46× / 10.68× 波动，
  #    0.0511 × 10 = 0.511 > 0.3，会跨线。而且它偷偷把方差断言又请回来了，
  #    而方差断言正是上一轮因 n=2 刚被撤掉的那类。
  #    稳健性只能来自「整段区间判决不变」（可证），不能来自「余量足够大」（不可证）。
  #
  # ⚠ 另外：无正则基线的最坏格只有 n=1，所以"基线 0.984 过线"只是单次观测。
  #    够用来校验线的位置（线必须 < 0.984），不够用来谈基线波动。
  #
  # 还有一条便宜得多的替代判据（native-brush 给的，优先用它）：
  #   **别在真机上验证雾，只验证「splice 被正确钳住」。**
  #   桌面已证明 BC 双翼被完全钳住后 MC 也钳住了（maxσ 4.3–11.5，典型口径 MC 最坏 ≤0.021），
  #   剩下的雾主要是"中等飞片被投到近处"这类稀疏残留。真机只要确认
  #   **maxσ 和 p50 与桌面一致**（都是单调量，单次可作数），钳住即成立，
  #   雾的推断就跟着成立 —— 不用在真机上追雾的绝对值。
  #   真机若 maxσ 明显偏大（> 20），才说明钳住没生效，那时再补跑看雾。
  step "1c. 真机 PLY 量化（与桌面生产口径对比）"
  if [[ -f "$OUT/out.ply" && -s "$OUT/out.ply" ]]; then
    # 这个脚本 PLY 是位置参数，--yaw 只吃一个值，所以按格位分两次跑。
    # 只看两个格位：yaw=0（绕圈最坏角，native-brush 两次复跑都落在 0° 或 10°）
    # 和 yaw=30（查看页默认初始视角）。别用 range 去比，那是拿区间比点。
    echo "-- 最坏格 yaw=0 --"
    "$NODE" "$ROOT/tools/ply_quantile_report.mjs" "$OUT/out.ply" --yaw 0 2>&1 | tail -25
    echo "-- 初始格 yaw=30 --"
    "$NODE" "$ROOT/tools/ply_quantile_report.mjs" "$OUT/out.ply" --yaw 30 2>&1 | tail -25
  else
    echo "!! 没拿到 out.ply，跳过量化"
  fi
fi

# ---------- 2. APK 端到端 ----------
if [[ "${1:-all}" == "all" || "${1:-all}" == "app" ]]; then
  step "2. APK 端到端"
  [[ -f "$APK" ]] || { echo "!! 缺 $APK"; exit 1; }
  echo "-- 安装 --"
  "$ADB" install -r "$APK" 2>&1 | tail -3

  echo "-- 启动 --"
  "$ADB" shell "monkey -p com.diandi.gaosi 1" >/dev/null 2>&1
  sleep 5

  echo "-- 手工操作提示 --"
  cat <<'TXT'
  请在手机上完成：导入素材 → 运行 → 查看 → 图库
  （脚本不自动点，因为导入素材要走系统文件选择器）

  四项观察，缺一不可 —— 前三项是"能不能用"，第四项是"好不好用"：
    1) 能不能装、跑不跑得完、耗时多久（快档 nominalSec 是 1950s，真机超 1.5× 要报）
    2) 最终高斯数（快档预期 6 万左右；桌面 60,338 / 60,884）
    3) 主体前方还有没有雾 —— 进查看页**转满一圈**，重点盯 yaw 0°–30°。
       雾是视角函数，首屏常常不是最坏的那屏，只看默认视角会漏判。
    4) 主体有没有变稀疏 —— 这是新参数唯一的代价（主体尺度降约 50%）
    5) 主体清不清楚 —— 这一项**单独报，不要合并进任何质量分**。
       雾团指数是屏幕覆盖率、p50 是尺寸，两个都不测清晰度。
       降正则有可能让画面观感更好但更糊，必须靠眼睛。

  截图取证用下面这条命令，不要用 PowerShell exec-out（会损坏 png）：
TXT
  echo "    $ADB shell screencap -p /sdcard/shot.png && $ADB pull /sdcard/shot.png <本地路径>"

  echo "-- 应用日志（最近 60 行）--"
  "$ADB" logcat -d -t 60 | grep -iE "diandi|brush|sfm|pipeline|vulkan" | tail -40
fi

step "完成"
