#!/usr/bin/env python3
"""独立复核训练产物 PLY 与 SfM 点云的几何一致性（坐标系 / 朝向 / 尺度）。

为什么要有这个工具
------------------
3DGS 产出的 PLY 里通常混有一小撮远场 floater，会把 min/max bbox 撑大好几倍。
所以**不能靠 bbox 判断训练出来的场景是否和 SfM 在同一坐标系、同一尺度**，
要看两样东西：
  1) splat 质心 vs SfM 点云质心 —— 是否几乎重合（判断坐标系 & 朝向）
  2) 每个 splat 到最近 SfM 点的距离分布 —— p50/p90/p99 是否落在合理量级（判断尺度）

PLY 解析不假设任何属性顺序或固定 stride：按 header 里声明的属性类型累加偏移，
并在读完后用 文件大小 == header + 顶点数*stride 做自检（错了对不上）。

用法: python tools/check_ply.py <splat.ply> <colmap_dir>
"""
import argparse
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_colmap import read_points3d  # noqa: E402

TYPE_SIZE = {
    "char": 1, "uchar": 1, "int8": 1, "uint8": 1, "byte": 1,
    "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
    "int": 4, "uint": 4, "int32": 4, "uint32": 4, "float": 4, "float32": 4,
    "longlong": 8, "ulonglong": 8, "int64": 8, "uint64": 8,
    "double": 8, "float64": 8,
}


def read_ply(path):
    with open(path, "rb") as f:
        header = b""
        while True:
            line = f.readline()
            header += line
            if line.strip() == b"end_header":
                break
            if not line:
                raise SystemExit("%s: 找不到 end_header" % path)
    lines = header.decode("ascii", "replace").splitlines()
    endian = "<"
    nv = 0
    props = []
    for ln in lines:
        p = ln.split()
        if not p:
            continue
        if p[0] == "format":
            # PLY 声明形如 "format binary_little_endian 1.0": 字节序在 p[1]
            endian = "<" if p[1] == "binary_little_endian" else ">"
        elif p[0] == "element" and p[1] == "vertex":
            nv = int(p[2])
        elif p[0] == "property" and p[1] != "list":
            # PLY 标量属性声明形如 "property <type> <name>"
            props.append((p[1], p[2]))  # (type, name)
    offset = {}
    o = 0
    for t, nm in props:
        if t not in TYPE_SIZE:
            raise SystemExit("未知 PLY 属性类型: %s" % t)
        offset[nm] = o
        o += TYPE_SIZE[t]
    stride = o
    for k in ("x", "y", "z"):
        if k not in offset:
            raise SystemExit("PLY 里没有 %s 属性" % k)
    data_off = len(header)
    size = Path(path).stat().st_size
    if size != data_off + nv * stride:
        raise SystemExit("长度自检失败: 文件 %d != header %d + %d*%d = %d"
                         % (size, data_off, nv, stride, data_off + nv * stride))
    xyz_type = dict((n, t) for t, n in props)["x"]
    c = endian + ("f" if TYPE_SIZE[xyz_type] == 4 else "d")
    pts = []
    with open(path, "rb") as f:
        f.read(data_off)
        blob = f.read(nv * stride)
    for i in range(nv):
        b = blob[i * stride:(i + 1) * stride]
        pts.append((struct.unpack_from(c, b, offset["x"])[0],
                    struct.unpack_from(c, b, offset["y"])[0],
                    struct.unpack_from(c, b, offset["z"])[0]))
    return pts, dict(nv=nv, stride=stride, header=data_off, props=[n for _, n in props])


def nearest_dists(S, P, cell=1.0):
    grid = {}
    for p in P:
        k = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)),
             int(math.floor(p[2] / cell)))
        grid.setdefault(k, []).append(p)
    out = []
    for q in S:
        kx, ky, kz = (int(math.floor(q[0] / cell)), int(math.floor(q[1] / cell)),
                      int(math.floor(q[2] / cell)))
        best = float("inf")
        hit = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for p in grid.get((kx + dx, ky + dy, kz + dz), ()):
                        hit = True
                        d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                        if d < best:
                            best = d
        if not hit:  # 落在所有邻接格里都没有点, 全量兜底
            best = min((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
                       for p in P)
        out.append(math.sqrt(best))
    return out


def centroid(V):
    n = len(V)
    return tuple(sum(v[i] for v in V) / n for i in range(3))


def q(a, r):
    return a[min(len(a) - 1, int(len(a) * r))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ply")
    ap.add_argument("colmap_dir")
    args = ap.parse_args()

    S, info = read_ply(args.ply)
    P = [p["xyz"] for p in read_points3d(
        str(Path(args.colmap_dir) / "points3D.bin")).values()]

    print("PLY : %s" % args.ply)
    print("  顶点 %d  stride %d  header %d  属性数 %d  (%s)"
          % (info["nv"], info["stride"], info["header"], len(info["props"]),
             ", ".join(info["props"])))
    print("SfM : %s  (%d 点)" % (args.colmap_dir, len(P)))

    cs, cp = centroid(S), centroid(P)
    print("\n质心")
    print("  splat      : (%.4f, %.4f, %.4f)" % cs)
    print("  SfM 点云   : (%.4f, %.4f, %.4f)" % cp)
    print("  偏差       : %.4f" % math.dist(cs, cp))

    D = sorted(nearest_dists(S, P))
    print("\n每个 splat 到最近 SfM 点的距离")
    print("  p50 %.4f  p75 %.4f  p90 %.4f  p95 %.4f  p99 %.4f  max %.4f"
          % (q(D, .5), q(D, .75), q(D, .9), q(D, .95), q(D, .99), D[-1]))
    for th in (0.1, 0.2, 0.5, 1.0, 2.0):
        n = sum(1 for d in D if d < th)
        print("  < %.1f : %d/%d = %.1f%%" % (th, n, len(D), 100 * n / len(D)))

    R = sorted(math.dist(s, cp) for s in S)
    Rp = sorted(math.dist(p, cp) for p in P)
    print("\n半径分布（以 SfM 质心为中心，两侧同一口径）")
    print("  splat p50 %.4f  p90 %.4f  p95 %.4f  p99 %.4f  max %.4f"
          % (q(R, .5), q(R, .9), q(R, .95), q(R, .99), R[-1]))
    print("  SfM   p50 %.4f  p90 %.4f  p95 %.4f  p99 %.4f  max %.4f"
          % (q(Rp, .5), q(Rp, .9), q(Rp, .95), q(Rp, .99), Rp[-1]))
    for th in (3.0, 5.0):
        a = sum(1 for d in R if d > th)
        b = sum(1 for d in Rp if d > th)
        print("  半径>%.1f : splat %d (%.2f%%)   SfM %d (%.2f%%)"
              % (th, a, 100 * a / len(R), b, 100 * b / len(Rp)))

    xs, ys, zs = zip(*S)
    px, py, pz = zip(*P)
    print("\nbbox（仅作参考，判断尺度请以质心和最近邻为准）")
    print("  splat : x[%.2f,%.2f] y[%.2f,%.2f] z[%.2f,%.2f]  对角线 %.3f"
          % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs),
             math.dist((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))))
    print("  SfM   : x[%.2f,%.2f] y[%.2f,%.2f] z[%.2f,%.2f]  对角线 %.3f"
          % (min(px), max(px), min(py), max(py), min(pz), max(pz),
             math.dist((min(px), min(py), min(pz)), (max(px), max(py), max(pz)))))
    sb = [(min(px), max(px)), (min(py), max(py)), (min(pz), max(pz))]
    inside = sum(1 for s in S if all(sb[i][0] <= s[i] <= sb[i][1] for i in range(3)))
    print("  落在 SfM bbox 内: %d/%d = %.1f%%" % (inside, len(S), 100 * inside / len(S)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
