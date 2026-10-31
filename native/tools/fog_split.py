#!/usr/bin/env python3
"""把"相机与主体之间的雾"拆成「有 SfM 点支撑」和「没有支撑（floater）」两部分。

为什么必须拆
------------
我自己的 5.6.4（训练相机口径）前景 mean alpha = 0.160，native-sfm 的雾团指数
（查看页取景相机口径）= 0.499。两个数差 3 倍，但**它们不是同一个东西**：
  - 训练相机贴在物体旁边（dist ~2），"前景"= d < 0.8·d_mid，里面主要是**物体近侧的
    真实表面**（桌子近缘、笔记本近侧），是真几何。
  - 查看页相机站得远（dist ~12），"主体前"= depth < dist − r3_p95，那是一片
    离主体 2 个单位以上的空腔，里面**几乎只有飞点**。
所以"哪个更可信"这个问题问错了：两个都对，量的不是同一个东西。
真正要回答的是"0.499 里有多少是飞点"——这就是本脚本。

怎么拆
------
真实几何有 SfM 点支撑，floater 没有。对每个"主体前"的 splat，查它到最近 SfM 点的
距离 d_nn：d_nn < R 判为真实几何，否则判为 floater。

R 的取法（避免第六类伪指标：换一个合理的 R 结论不能翻）
------------------------------------------------------
R 不能是拍脑袋的常数。这里用 SfM 点云自身的采样间距：
  R1 = SfM 点两两最近邻距离的中位数   —— 有点密度决定，与场景尺度无关
  R2 = 2·R1                          —— 宽松一档
  R3 = 4·R1                          —— 再宽松一档
三个都打，看结论翻不翻。

用法: python fog_split.py <splat.ply> <colmap_dir> [标签]
"""
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sfm" / "tools"))
sys.path.insert(0, str(ROOT / "testdata" / "steptest"))
from inspect_colmap import read_points3d  # noqa: E402
from tail_crosstab import read_ply_all    # noqa: E402

YAW = 30.0
PITCH = 18.0
FOV = 45.0
FILL = 0.9
ASPECTS = (0.4497,)     # apk-shell 实测：查看页画布 411x914
NEAR, FAR = 0.1, 5000.0


def nearest_dist(A, B, chunk=512, skip_self=False):
    """A 每行到 B 的最近距离（分块，避免 300k x 7.6k 一次爆内存）。

    skip_self: A 和 B 是同一个集合时，最近的那个是自己（距离 0），必须跳过去取第二个，
    否则 R1 恒等于 0，整个拆分退化成"全部无支撑"。
    """
    out = np.empty(len(A), dtype=np.float32)
    for i in range(0, len(A), chunk):
        d = A[i:i + chunk]
        m = ((d[:, None, :] - B[None, :, :]) ** 2).sum(-1)
        if skip_self:
            v = np.sqrt(np.sort(m, axis=1)[:, 1])
        else:
            v = np.sqrt(m.min(1))
        out[i:i + chunk] = v
    return out


def main():
    ply, cdir = sys.argv[1], Path(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else Path(ply).stem

    nv, cols = read_ply_all(ply)
    XYZ = np.stack([np.asarray(cols[k]) for k in "xyz"], axis=1)
    sc = np.stack([np.exp(np.asarray(cols["scale_%d" % k])) for k in range(3)], axis=1)
    op = 1.0 / (1.0 + np.exp(-np.asarray(cols["opacity"])))
    P = np.array([v["xyz"] for v in read_points3d(cdir / "points3D.bin").values()])
    cp = P.mean(0)

    # plyFraming 的分位数口径（水平/竖直分轴，等间隔采样 <=20000）
    step = max(1, nv // 20000)
    S = XYZ[::step]
    d0 = S - cp
    h95 = float(np.percentile(np.sqrt(d0[:, 0] ** 2 + d0[:, 2] ** 2), 95))
    v95 = float(np.percentile(np.abs(d0[:, 1]), 95))
    R3 = np.sqrt(((XYZ - cp) ** 2).sum(1))
    r3q = float(np.percentile(R3, 95))

    # 支撑半径：SfM 点云自身的采样间距
    nn_sfm = nearest_dist(P, P, chunk=256, skip_self=True)
    R1 = float(np.median(nn_sfm))
    d_nn = nearest_dist(XYZ, P)

    S_sorted = np.sort(sc, axis=1)
    S2 = S_sorted[:, 2] * S_sorted[:, 1]                  # 两个最大轴：上界
    S_gm2 = np.exp(np.log(sc).mean(1)) ** 2               # 三轴几何平均平方：典型

    print("== %s ==  splats %d  SfM 点 %d" % (label, nv, len(P)))
    print("  h95 %.3f v95 %.3f  r3_p95 %.3f | SfM 点最近邻中位 %.4f -> R1 %.4f"
          % (h95, v95, r3q, R1, R1))
    print("  splat 到最近 SfM 点: p50 %.4f p90 %.4f p95 %.4f"
          % tuple(np.percentile(d_nn, [50, 90, 95])))

    tY0 = math.tan(FOV * math.pi / 360)
    up_w = np.array([0.0, 1.0, 0.0])

    for asp in ASPECTS:
        tX, tY = tY0 * asp, tY0
        dist = max(h95 / (FILL * tX), v95 / (FILL * tY))
        yaw, pitch = math.radians(YAW), math.radians(PITCH)
        dv = np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                       math.cos(pitch) * math.cos(yaw)])
        C = cp + dist * dv
        rw = np.cross(dv, up_w)
        rw /= np.linalg.norm(rw)
        uc = np.cross(rw, dv)
        V = XYZ - C
        dk = V @ (-dv)
        infr = ((dk > NEAR) & (dk < FAR)
                & (np.abs(V @ rw) <= tX * dk) & (np.abs(V @ uc) <= tY * dk))
        front = infr & (dk < dist - r3q)

        def frac(s2):
            return np.minimum(
                np.where(dk > 0, 2 * np.pi * s2 / (4 * tX * tY * np.maximum(dk, 1e-9) ** 2), 0.0),
                1.0)

        contrib_u = op * frac(S2)          # 上界
        contrib_t = op * frac(S_gm2)       # 典型
        print("  [asp %.4f] dist %.3f | 视锥内 %d | 主体前 %d"
              % (asp, dist, int(infr.sum()), int(front.sum())))
        tot_u = float(contrib_u[front].sum())
        tot_t = float(contrib_t[front].sum())
        print("    雾团指数 上界 %.4f / 典型 %.4f" % (tot_u, tot_t))
        # R 的两族取值，交叉验证结论翻不翻（第六类检验）
        grid = [("R1 x%d" % m, R1 * m) for m in (1, 2, 4)]
        grid += [("d_nn p%02d" % p, float(np.percentile(d_nn, p))) for p in (50, 90, 95)]
        for name, Rm in grid:
            sup = front & (d_nn < Rm)
            uns = front & (d_nn >= Rm)
            print("    %-10s R=%.4f | 有支撑 %5d 贡献 上界 %.4f / 典型 %.4f | "
                  "无支撑 %5d 贡献 上界 %.4f / 典型 %.4f | 无支撑占雾 %.1f%% (典型)"
                  % (name, Rm, int(sup.sum()), float(contrib_u[sup].sum()),
                     float(contrib_t[sup].sum()), int(uns.sum()),
                     float(contrib_u[uns].sum()), float(contrib_t[uns].sum()),
                     100.0 * float(contrib_t[uns].sum()) / tot_t if tot_t else 0.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
