"""imgstats.py - 客观判定一张截图是「扁平 UI」还是「点云渲染画面」。

用途：本项目多次出现"我读不了图却仍然描述图内容"的错误。
这个脚本不依赖人眼目视，只用可复核的像素统计量给结论。

判据（每个都做成有可能失败的对照，不做成恒真的自指指标）：
  1. uniq     唯一颜色数     UI 是纯色块 -> 几百到几千；点云有连续渐变 -> 数万起
  2. edge     相邻平均梯度   点云高频细节多 -> 明显更高
  3. entropy  灰度直方图熵   UI 集中在少数灰阶 -> 低；点云分布铺开 -> 高
  4. darkpix  极暗像素占比   用来识别"黑屏/没渲出来"这种失败形态

用法:
  python imgstats.py <图1> [图2] ...
"""
import sys
import os
import hashlib

import numpy as np
from PIL import Image

REF_UI = 125000  # 已知 UI 截图字节数参考，仅用于报告提示


def stats(path):
    raw = open(path, "rb").read()
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.float64)
    flat = np.asarray(im).astype(np.uint8).reshape(-1, 3)
    key = (flat[:, 0].astype(np.int64) << 16) | (flat[:, 1].astype(np.int64) << 8) | flat[:, 2]
    uniq = int(np.unique(key).size)

    gray = a.mean(axis=2)
    dx = float(np.abs(np.diff(gray, axis=1)).mean())
    dy = float(np.abs(np.diff(gray, axis=0)).mean())

    h, _ = np.histogram(gray, bins=64, range=(0, 255))
    p = h.astype(np.float64) / h.sum()
    p = p[p > 0]
    ent = float(-(p * np.log2(p)).sum())

    npix = float(gray.shape[0] * gray.shape[1])

    return {
        "file": os.path.basename(path),
        "bytes": len(raw),
        "sha12": hashlib.sha256(raw).hexdigest()[:12],
        "wh": "%dx%d" % im.size,
        "uniq": uniq,
        "uniq_d": uniq / npix,
        "std": round(float(gray.std()), 2),
        "edge": round((dx + dy) / 2.0, 3),
        "entropy": round(ent, 3),
        "mean": round(float(gray.mean()), 1),
        "dark%": round(float((gray < 12).mean() * 100), 2),
    }


def verdict(s):
    """给出判定。

    判据经过实测标定（2026-10-15），正例=已知点云渲染图，负例=已知 UI 截图：

      指标        UI 截图范围          点云渲染范围        取阈值
      uniq/px     5.51e-4 ~ 5.95e-4   2.07e-2 ~ 7.18e-2  3.5e-3
                  (阈值/UI = 5.9x    渲染低值/阈值 = 5.9x)
      entropy     0.65 ~ 1.87         5.30 ~ 5.44        3.0
                  (阈值/UI = 1.6x    渲染低值/阈值 = 1.76x)

    刻意废弃 edge（相邻像素梯度）——实测证明它是坏指标：UI 里的文字全是锐利
    边缘，`01-new.png` 的 edge=1.032 反而高于真实渲染图 `view_prod_base.png`
    的 0.640；且它随分辨率线性变化，1080x2400 与 411x914 不可直接比较。
    """
    reasons = []
    ok = True

    if s["dark%"] > 60:
        reasons.append("极暗像素 %.1f%% -> 疑似黑屏" % s["dark%"])
        ok = False
    if s["uniq_d"] < 3.5e-3:
        reasons.append("唯一色密度 %.2g 偏低 -> 更像纯色 UI" % s["uniq_d"])
        ok = False
    if s["entropy"] < 3.0:
        reasons.append("熵 %.3f 过低 -> 灰阶集中" % s["entropy"])
        ok = False

    return ("RENDER-LIKE" if ok else "NOT-RENDERED"), reasons


def main(paths):
    rows = [stats(p) for p in paths]

    print("=" * 118)
    hdr = "%-26s %10s %8s %9s %8s %10s %7s %8s %8s %7s"
    print(hdr % ("file", "bytes", "sha12", "wh", "uniq", "uniq/px", "std", "edge*", "entropy", "dark%"))
    print("-" * 118)
    for s in rows:
        # edge 列为观察项，不参与判定（见 verdict 的废弃说明）
        print("%-26s %10d %8s %9s %8d %10.2e %7.1f %8s %8.3f %7.2f" % (
            s["file"], s["bytes"], s["sha12"], s["wh"],
            s["uniq"], s["uniq_d"], s["std"], ("%.3f*" % s["edge"]), s["entropy"], s["dark%"]))
    print("-" * 118)

    # 两两去重检查：模拟器 ANR 会导致不同文件名但像素完全相同
    print("\n[重复检查] 是否存在不同文件名但内容完全一致（ANR 征兆）:")
    seen = {}
    dup = False
    for s in rows:
        if s["sha12"] in seen:
            print("   !! %s 与 %s 内容完全相同" % (s["file"], seen[s["sha12"]]))
            dup = True
        seen[s["sha12"]] = s["file"]
    if not dup:
        print("   无重复，各图互不相同")

    print("\n[逐图判定]")
    for s in rows:
        v, reasons = verdict(s)
        line = "   %-26s -> %s" % (s["file"], v)
        if reasons:
            line += "  (" + "; ".join(reasons) + ")"
        print(line)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
