#!/usr/bin/env python3
"""训练产物 PLY 的「位置尾 × 尺度尾」交叉统计。

半径以 **SfM 点云质心** 为原点（与 sfm/tools/check_ply.py 同一口径），
不用 splat 自身质心 —— 后者会随尾部漂移，两份产物之间不可比。

尺度侧：PLY 里 scale_0/1/2 是 **log(scale)**，必须先 exp() 才是世界单位。
       每个 splat 取三轴最大值的 exp()，作为该 splat 的"尺寸"。

用法: python tail_crosstab.py <splat.ply> <colmap_dir> [标签]
"""
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sfm" / "tools"))
from inspect_colmap import read_points3d  # noqa: E402

TYPE_SIZE = {"char": 1, "uchar": 1, "int8": 1, "uint8": 1, "byte": 1,
             "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
             "int": 4, "uint": 4, "int32": 4, "uint32": 4,
             "float": 4, "float32": 4,
             "longlong": 8, "ulonglong": 8, "int64": 8, "uint64": 8,
             "double": 8, "float64": 8}


def read_ply_all(path):
    """返回 (nv, {prop -> list})，只解析 float 属性。"""
    with open(path, "rb") as f:
        header = b""
        while True:
            line = f.readline()
            header += line
            if line.strip() == b"end_header":
                break
            if not line:
                raise SystemExit("%s: 找不到 end_header" % path)
    nv, props, endian = 0, [], "<"
    for ln in header.decode("ascii", "replace").splitlines():
        p = ln.split()
        if not p:
            continue
        if p[0] == "format":
            endian = "<" if p[1] == "binary_little_endian" else ">"
        elif p[0] == "element" and p[1] == "vertex":
            nv = int(p[2])
        elif p[0] == "property" and p[1] != "list":
            props.append((p[1], p[2]))
    off, stride = {}, 0
    for t, nm in props:
        off[nm] = stride
        stride += TYPE_SIZE[t]
    size = Path(path).stat().st_size
    data_off = len(header)
    if size != data_off + nv * stride:
        raise SystemExit("长度自检失败: %d != %d + %d*%d"
                         % (size, data_off, nv, stride))
    with open(path, "rb") as f:
        f.read(data_off)
        blob = f.read(nv * stride)
    cols = {}
    for nm in ("x", "y", "z", "scale_0", "scale_1", "scale_2", "opacity",
               "rot_0", "rot_1", "rot_2", "rot_3",
               "f_dc_0", "f_dc_1", "f_dc_2"):
        if nm not in off:
            continue
        cols[nm] = [struct.unpack_from(endian + "f", blob, i * stride + off[nm])[0]
                    for i in range(nv)]
    return nv, cols


def main():
    ply, cdir = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else Path(ply).stem
    nv, cols = read_ply_all(ply)
    P = [p["xyz"] for p in read_points3d(str(Path(cdir) / "points3D.bin")).values()]
    n = len(P)
    cp = tuple(sum(p[i] for p in P) / n for i in range(3))

    R = [math.dist((cols["x"][i], cols["y"][i], cols["z"][i]), cp)
         for i in range(nv)]
    S = [math.exp(max(cols["scale_0"][i], cols["scale_1"][i], cols["scale_2"][i]))
         for i in range(nv)]
    Rp = sorted(math.dist(p, cp) for p in P)

    print("== %s ==" % label)
    print("  splats %d   SfM 点 %d   质心 (%.3f, %.3f, %.3f)"
          % (nv, n, cp[0], cp[1], cp[2]))
    Rs = sorted(R)
    print("  radius: " + "  ".join(
        "p%.0f %.4f" % (q * 100, Rs[min(nv - 1, int(nv * q))])
        for q in (.5, .9, .95, .99)) + "  max %.4f" % Rs[-1])
    print("  SfM   : " + "  ".join(
        "p%.0f %.4f" % (q * 100, Rp[min(n - 1, int(n * q))])
        for q in (.5, .9, .95, .99)) + "  max %.4f" % Rp[-1])

    print("  位置尾 radius>3 : %d (%.2f%%)   SfM 侧 %d" %
          (sum(1 for r in R if r > 3), 100 * sum(1 for r in R if r > 3) / nv,
           sum(1 for r in Rp if r > 3)))
    print("  位置尾 radius>5 : %d (%.2f%%)   SfM 侧 %d" %
          (sum(1 for r in R if r > 5), 100 * sum(1 for r in R if r > 5) / nv,
           sum(1 for r in Rp if r > 5)))

    ths = (0.6, 0.8, 1.0, 1.2, 1.5)
    print("  尺度尾 exp()>t : " + "  ".join(
        "%.1f:%d" % (t, sum(1 for s in S if s > t)) for t in ths))

    far = [r > 3 for r in R]
    big = [s > 1.0 for s in S]
    fb = sum(1 for i in range(nv) if far[i] and big[i])
    f_ = sum(1 for i in range(nv) if far[i] and not big[i])
    print("  交叉(远>3, 大>1.0): 远且大 %d   远但小 %d   (远场共 %d)"
          % (fb, f_, sum(far)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
