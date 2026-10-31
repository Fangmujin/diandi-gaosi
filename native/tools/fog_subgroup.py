#!/usr/bin/env python3
"""把雾团指数按「到相机的距离」切成 F / not-F 两半，检验结论是否依赖这个切法。

为什么要切
----------
apk-shell 在做查看页取景时提出一个诊断子群：**F = 近相机最近的 30%**（按到相机的
距离取最近的三成）。问题是：我们既有结论里有没有哪条其实**依赖**这个子群口径？
依赖 → 结论是被口径造出来的；不依赖 → F 可以只留作诊断。

本脚本回答的方式不是"算一次看像不像"，而是把切法里所有自由量都扫一遍：
  - **母体**两种：front（视锥内且 depth < dist − r3_p95，雾团指数积分的那个集合）
    和 frustum（视锥内全量）。切哪个母体是口径选择，必须都给。
  - **比例**扫 {0.1, 0.2, 0.3, 0.4, 0.5}：30% 本身是自由参数（第六类坑），
    换一个比例判决不能翻。
  - **对照轴 L**：按**尺度**取最大的 30%（不是按距离）。必须给，否则无法区分
    "正则的作用轴是距离还是尺度" —— 只按距离切的话，任何结论都无法排除
    "真正起作用的是尺度，距离只是碰巧相关"。

还有一个退化陷阱：**母体选 frustum 时 F 会吞掉整个 front 集合**（front 本身就在
最前面，最近的 10% 已经全包了），那一路的"F 占 100%"没有任何信息量。所以两种
母体都打印，但只有 front 母体那一路可解释。

相机距离的混杂：dist 是按**每个产物自己的 p95** 现算的（真机就是这么做的），
所以三批的相机位置不同，"最近 30%"指的物理区域也不同。第 5 个参数可以强制
dist，用于把这个混杂按住。

格位纪律
--------
"稳定"是**格位**的属性，不是配置的属性。本脚本固定一个格位（asp/yaw/pitch），
由命令行给，不在多路之间来回换格。默认 yaw=0（team-lead 指定的最坏格）。

口径与既有两节保持一致：
  - p50  = exp(max(log_scale)) 的中位数（sweep_metrics.py 的 Smax_p50，与 5.6.6 表同）
    另外给 Sgm_p50（三轴几何平均）做交叉检验。
  - 雾团 = Σ α × 占屏比，线性求和；占屏比两种口径：
      典型 = 三轴几何平均的平方（5.6.6 表用的就是这个）
      上界 = 两个最大轴之积
    α = sigmoid(opacity)，σ = exp(scale)（PLY 存 log/logit）。

用法: python fog_subgroup.py <splat.ply> <colmap_dir> [标签] [yaw]
"""
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sfm" / "tools"))
from inspect_colmap import read_points3d  # noqa: E402

PITCH = 18.0
FOV = 45.0
FILL = 0.9
ASP = 0.4497               # apk-shell 实测查看页画布 411x914
NEAR, FAR = 0.1, 5000.0
SAMPLE = 20000             # plyFraming 的等间隔采样上限
FRACS = (0.1, 0.2, 0.3, 0.4, 0.5)


def read_ply(path):
    """按 header 声明累加偏移，不假设 stride；长度自检不过就直接报错。"""
    with open(path, "rb") as f:
        raw = f.read()
    end = raw.find(b"end_header")
    if end < 0:
        raise SystemExit("no end_header: %s" % path)
    data_off = raw.find(b"\n", end) + 1
    head = raw[:end].decode("ascii").split("\n")
    size_of = {"char": 1, "uchar": 1, "int8": 1, "uint8": 1,
               "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
               "int": 4, "uint": 4, "int32": 4, "uint32": 4,
               "float": 4, "float32": 4, "double": 8, "float64": 8}
    props, nv, off, in_v = [], 0, 0, False
    for ln in head:
        s = ln.strip()
        if s.startswith("element"):
            in_v = s.split()[1] == "vertex"
            if in_v:
                nv = int(s.split()[2])
            continue
        if not in_v or not s.startswith("property"):
            continue
        f = s.split()
        props.append((f[2], size_of[f[1]], off))
        off += size_of[f[1]]
    stride = off
    if len(raw) != data_off + nv * stride:
        raise SystemExit("长度对不上: %d != %d + %d*%d —— 别信后面的数"
                         % (len(raw), data_off, nv, stride))
    dtype = np.dtype({"names": [p[0] for p in props],
                      "formats": ["<f4" if p[1] == 4 else "<f8" for p in props],
                      "offsets": [p[2] for p in props],
                      "itemsize": stride})
    return nv, np.frombuffer(raw, dtype=dtype, count=nv, offset=data_off)


def q(xs, p):
    """与 sweep_metrics.py 同口径：nearest-rank，不是插值。"""
    if not len(xs):
        return float("nan")
    ys = np.sort(xs)
    return float(ys[min(len(ys) - 1, max(0, int(round(p * (len(ys) - 1)))))])


def main():
    ply, cdir = sys.argv[1], Path(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else Path(ply).stem
    yaw = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    dist_fix = float(sys.argv[5]) if len(sys.argv) > 5 else None

    nv, d = read_ply(ply)
    P = np.array([v["xyz"] for v in read_points3d(cdir / "points3D.bin").values()])
    cp = P.mean(0)

    XYZ = np.stack([d["x"], d["y"], d["z"]], axis=1).astype(np.float64)
    sc = np.stack([d["scale_%d" % k] for k in range(3)]).astype(np.float64)
    sig = np.exp(sc)                             # PLY 存 log
    Smax = sig.max(axis=0)                       # 与 sweep_metrics 的 Smax_p50 同口径
    Sgm = np.exp(sc.mean(axis=0))                # 三轴几何平均
    op = 1.0 / (1.0 + np.exp(-d["opacity"].astype(np.float64)))

    # ---- plyFraming（viewer.js 口径：分轴 p95，等间隔采样 <=20000）----
    step = max(1, nv // SAMPLE)
    idx = np.arange(0, nv, step)
    d0 = XYZ[idx] - cp
    h95 = q(np.sqrt(d0[:, 0] ** 2 + d0[:, 2] ** 2), 0.95)
    v95 = q(np.abs(d0[:, 1]), 0.95)
    r3q = q(np.sqrt(((XYZ[idx] - cp) ** 2).sum(1)), 0.95)

    tY = math.tan(FOV * math.pi / 360)
    tX = tY * ASP
    dist = dist_fix if dist_fix else max(h95 / (FILL * tX), v95 / (FILL * tY))

    # ---- 固定相机（yaw 由命令行给，pitch 固定 18）----
    y, p = math.radians(yaw), math.radians(PITCH)
    dv = np.array([math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y)])
    C = cp + dist * dv
    up_w = np.array([0.0, 1.0, 0.0])
    rw = np.cross(dv, up_w)
    rw /= np.linalg.norm(rw)
    uc = np.cross(rw, dv)
    V = XYZ - C
    depth = V @ (-dv)
    infr = ((depth > NEAR) & (depth < FAR)
            & (np.abs(V @ rw) <= tX * depth) & (np.abs(V @ uc) <= tY * depth))
    front = infr & (depth < dist - r3q)

    def frac_of(s2):
        return np.minimum(
            np.where(depth > 0, 2 * np.pi * s2 / (4 * tX * tY * np.maximum(depth, 1e-9) ** 2),
                     0.0), 1.0)

    sc_sorted = np.sort(sig, axis=0)
    contrib_u = op * frac_of(sc_sorted[1] * sc_sorted[2])     # 上界：两个最大轴
    contrib_t = op * frac_of(Sgm * Sgm)                       # 典型：三轴几何平均

    tot_u = float(contrib_u[front].sum())
    tot_t = float(contrib_t[front].sum())

    print("== %s ==  splats %d | yaw %.0f° pitch %.0f° asp %.4f | dist %.3f%s  r3_p95 %.3f"
          % (label, nv, yaw, PITCH, ASP, dist,
             " (强制)" if dist_fix else "", r3q))
    print("  视锥内 %d | 主体前 %d | 全量 p50(Smax) %.4f  p50(Sgm) %.4f"
          % (int(infr.sum()), int(front.sum()), q(Smax, .5), q(Sgm, .5)))
    print("  雾团 典型 %.4f / 上界 %.4f" % (tot_t, tot_u))

    # 雾按尺度分桶：检验"中等飞片 vs 巨型片"
    for lo, hi in ((0.0, 0.3), (0.3, 1.0), (1.0, 5.0), (5.0, 1e9)):
        m = front & (Smax >= lo) & (Smax < hi)
        print("    σ∈[%.1f,%.1f) 个 %5d | 雾 典型 %.4f (%.1f%%) / 上界 %.4f (%.1f%%)"
              % (lo, hi, int(m.sum()), float(contrib_t[m].sum()),
                 100.0 * float(contrib_t[m].sum()) / tot_t if tot_t else 0.0,
                 float(contrib_u[m].sum()),
                 100.0 * float(contrib_u[m].sum()) / tot_u if tot_u else 0.0))

    # top-k 占比：回答"是被一个/两个撑起来的，还是一群中等片"
    fi = np.where(front)[0]
    if len(fi):
        for tag, c in (("典型", contrib_t), ("上界", contrib_u)):
            v = np.sort(c[fi])[::-1]
            print("    top-1 %s %.4f (%.1f%%) | top-3 %.4f (%.1f%%) | top-10 %.4f (%.1f%%)"
                  % (tag, v[0], 100.0 * v[0] / tot_t if tag == "典型" else
                     100.0 * v[0] / tot_u,
                     v[:3].sum(),
                     100.0 * v[:3].sum() / (tot_t if tag == "典型" else tot_u),
                     v[:10].sum(),
                     100.0 * v[:10].sum() / (tot_t if tag == "典型" else tot_u)))

    def part(order, tag):
        for fr in FRACS:
            n = int(round(fr * len(order)))
            fm = np.zeros(nv, dtype=bool)
            fm[order[:n]] = True
            A = order[:n]
            B = order[n:]
            print("    %s=%2d%% | 组内 %5d 个 (其中主体前 %d) | "
                  "p50 %.4f / 补 %.4f | p50gm %.4f / 补 %.4f | "
                  "雾 典型 %.4f (%.1f%%) / 补 %.4f | 上界 %.4f (%.1f%%) / 补 %.4f"
                  % (tag, round(fr * 100), len(A), int((fm & front).sum()),
                     q(Smax[A], .5), q(Smax[B], .5),
                     q(Sgm[A], .5), q(Sgm[B], .5),
                     float(contrib_t[fm & front].sum()),
                     100.0 * float(contrib_t[fm & front].sum()) / tot_t if tot_t else 0.0,
                     float(contrib_t[(~fm) & front].sum()),
                     float(contrib_u[fm & front].sum()),
                     100.0 * float(contrib_u[fm & front].sum()) / tot_u if tot_u else 0.0,
                     float(contrib_u[(~fm) & front].sum())))

    for pname, parent in (("front", front), ("frustum", infr)):
        print("  --- 距离轴 F：母体 = %s（%d 个），取最近的 f ---"
              % (pname, int(parent.sum())))
        pi = np.where(parent)[0]
        part(pi[np.argsort(depth[pi])], "F")

    print("  --- 对照轴 L：全量按 σ_max 从大到小，取最大的 f ---")
    part(np.argsort(-Smax), "L")
    return 0


if __name__ == "__main__":
    sys.exit(main())
