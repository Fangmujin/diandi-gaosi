#!/usr/bin/env python3
"""尺度无关的远场占比 + 分位表：给跨数据集比较用的一把尺子。

为什么
------
`r > 3` 是绝对世界单位。单目 SfM 的尺度是任意的，同一个 3 在不同数据集里相当于
p95 的 2.02x / 1.88x / 1.55x / 1.17x（实测），抓到的不是同一批点，占比不可比。
改成 `r > k * p90`（p90 从数据里来）后，阈值跟着尺度走，跨数据集才有意义。

**k 仍然是自由参数** —— 所以脚本把 k=1.5 / 2 / 3 全打出来。
能确定的只有**排序**，百分比数值本身没有绝对含义。

另外一个口径分歧也在这里一并量化：apk-shell 的 `ply_quantile_report.mjs` 用
**splat 自身质心**（必须与 plyFraming() 一致），我这边一直用 **SfM 点云质心**
（不会被尾部拖着走）。两者都算，差值就是口径差。

用法: python far_fraction.py <ply>:<colmap_dir>[:标签] ...
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_colmap import read_points3d  # noqa: E402
from occlusion_stats import read_ply, quant  # noqa: E402

KS = (1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)


def tail_shape(R, p90):
    """尾部是不是一个**可分离**的独立population？

    如果远场是一簇和主体分开的点（占比 s、集中在某个半径上），那么"半径 > k*p90 的占比"
    随 k 增大应该是**阶跃**：k*p90 小于那簇半径时约等于 s，超过后突然掉到 ~0。
    如果是连续单峰的长尾，占比会平滑衰减。
    这个区别决定 `s > 1-q` 那条失效规则能不能用 —— 规则要有定义良好的 s 才成立。
    """
    pos = R[R > 0]
    lo = math.log2(quant(pos, 0.5))
    hi = math.log2(R.max())
    nb = 12
    edges = [lo + (hi - lo) * i / nb for i in range(nb + 1)]
    hist = []
    for i in range(nb):
        a, b = 2 ** edges[i], 2 ** edges[i + 1]
        hist.append(float(((pos >= a) & (pos < b)).mean()))
    return hist, edges


def one(ply, cdir, label):
    nv, d = read_ply(ply)
    X = d["x"].astype(np.float64)
    Y = d["y"].astype(np.float64)
    Z = d["z"].astype(np.float64)
    P = np.array([p["xyz"] for p in read_points3d(str(Path(cdir) / "points3D.bin")).values()])
    cp_sfm = P.mean(axis=0)
    cp_self = np.array([X.mean(), Y.mean(), Z.mean()])

    out = []
    for tag, cp in (("SfM", cp_sfm), ("self", cp_self)):
        R = np.sqrt((X - cp[0]) ** 2 + (Y - cp[1]) ** 2 + (Z - cp[2]) ** 2)
        p90, p95 = quant(R, 0.90), quant(R, 0.95)
        fr = ["%.2f" % (100.0 * (R > k * p90).mean()) for k in KS]
        out.append((tag, nv, p90, p95, p95 / p90 if p90 else float("nan"),
                    quant(R, 0.99) / p95 if p95 else float("nan"),
                    float(R.max()), fr, tail_shape(R, p90)))
    print("== %s ==" % label)
    for tag, n, p90, p95, r1, r2, mx, fr, (hist, edges) in out:
        print("  [%s质心] n=%d p90 %.4f p95 %.4f p95/p90 %.3f p99/p95 %.3f max %.4f"
              % (tag, n, p90, p95, r1, r2, mx))
        print("           远场占比%(r>k*p90) " + "  ".join(
            "k%.2f:%s" % (k, f) for k, f in zip(KS, fr)))
        if tag == "SfM":
            print("           log2(半径)直方图（占全部点的小数，p50→max，等分 %d 格）:" % len(hist))
            print("           " + " ".join("%.4f" % h for h in hist))
            print("           格边界半径: " + " ".join("%.2f" % (2 ** e) for e in edges[::3]))
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for spec in sys.argv[1:]:
        parts = spec.split(":")
        ply, cdir = parts[0], parts[1]
        label = parts[2] if len(parts) > 2 else Path(ply).stem
        one(ply, cdir, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
