#!/usr/bin/env python3
"""按 APK 侧 plyFraming() 的口径统计「取景框内」的脏点密度。

为什么要有这个工具
------------------
全局的远场飞点数（radius > 3）随训练步数涨得很快，很容易据此推出
"步数越大画面越脏"。但用户看到的是**取景框内**的东西：取景相机按分位数
框住主体，飞点大多在框外。所以决定观感的是框内密度，不是全局计数。

口径严格对齐 apk-shell 的 app/assets/js/viewer.js::plyFraming()：
  - 原点：SfM 点云质心（不是 splat 自身质心，后者会被尾部拖着走）
  - 水平半径：sqrt(dx^2 + dz^2)，取 p95
  - 竖直半径：|dy|，取 p95
  - 取景框 = 水平 <= h95 且 竖直 <= v95（一个竖直圆柱，不是球）
尺度侧：scale_0/1/2 是 log(scale)，exp() 之后才是世界单位。

用法: python framing_stats.py <splat.ply> <colmap_dir> [标签]
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sfm" / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_colmap import read_points3d  # noqa: E402
from tail_crosstab import read_ply_all  # noqa: E402


def main():
    ply, cdir = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else Path(ply).stem
    nv, cols = read_ply_all(ply)
    P = [p["xyz"] for p in read_points3d(str(Path(cdir) / "points3D.bin")).values()]
    cp = tuple(sum(p[i] for p in P) / len(P) for i in range(3))

    dx = [cols["x"][i] - cp[0] for i in range(nv)]
    dy = [cols["y"][i] - cp[1] for i in range(nv)]
    dz = [cols["z"][i] - cp[2] for i in range(nv)]
    H = sorted(math.hypot(dx[i], dz[i]) for i in range(nv))
    V = sorted(abs(dy[i]) for i in range(nv))
    h95 = H[min(nv - 1, int(nv * 0.95))]
    v95 = V[min(nv - 1, int(nv * 0.95))]

    S = [math.exp(max(cols["scale_0"][i], cols["scale_1"][i], cols["scale_2"][i]))
         for i in range(nv)]
    R = [math.sqrt(dx[i] ** 2 + dy[i] ** 2 + dz[i] ** 2) for i in range(nv)]
    Rs = sorted(R)

    inb = [i for i in range(nv)
           if math.hypot(dx[i], dz[i]) <= h95 and abs(dy[i]) <= v95]
    big = sum(1 for i in inb if S[i] > 1.0)
    far_in = sum(1 for i in inb if R[i] > 3)
    far_all = sum(1 for r in R if r > 3)

    # 取景放大倍率：仅中心的 bbox 对角线 vs 计入高斯球体积后的 bbox 对角线
    def diag(lo, hi):
        return math.dist(lo, hi)

    X, Y, Z = cols["x"], cols["y"], cols["z"]
    d1 = diag((min(X), min(Y), min(Z)), (max(X), max(Y), max(Z)))
    lo = [min(X[i] - S[i] for i in range(nv)),
          min(Y[i] - S[i] for i in range(nv)),
          min(Z[i] - S[i] for i in range(nv))]
    hi = [max(X[i] + S[i] for i in range(nv)),
          max(Y[i] + S[i] for i in range(nv)),
          max(Z[i] + S[i] for i in range(nv))]

    print("== %s ==" % label)
    print("  splats %d   SfM 点 %d" % (nv, len(P)))
    print("  取景框: 水平 p95 %.3f   竖直 p95 %.3f   框内 %d (%.1f%%)"
          % (h95, v95, len(inb), 100 * len(inb) / nv))
    print("  框内 r>3 飞点     : %d / 全局 %d" % (far_in, far_all))
    print("  框内大 splat >1.0 : %d (%.3f%%)" % (big, 100 * big / nv))
    print("  半径 p95 %.4f  p99 %.4f  max %.4f   (p99/p95 %.2fx, max/p99 %.2fx)"
          % (Rs[min(nv - 1, int(nv * .95))], Rs[min(nv - 1, int(nv * .99))],
             Rs[-1], Rs[min(nv - 1, int(nv * .99))] / Rs[min(nv - 1, int(nv * .95))],
             Rs[-1] / Rs[min(nv - 1, int(nv * .99))]))
    print("  取景放大: 仅中心 %.2f -> 含半径 %.2f = %.2fx"
          % (d1, diag(lo, hi), diag(lo, hi) / d1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
