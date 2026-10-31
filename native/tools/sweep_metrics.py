#!/usr/bin/env python3
"""扫描实验的三个判据量：splat 总数 / exp(scale) 的 p50（主体典型尺度）/ 尾部。

为什么要单独一个脚本：team-lead 要的第三列是"正则有没有打到主体"，判据是
主体典型尺度（p50）不能跟着塌。正则压的是 sum(exp(scale))，理论上对大 splat
的梯度远大于小 splat（L1 型，梯度与尺度无关但总量按尺度线性累加），但必须实测。

两个尺度口径都出：
  S_max = exp(max(log_scale))   与 tail_crosstab.py / check_ply.py 同口径（"大不大"）
  S_gm  = exp(mean(log_scale))  三轴几何平均，抗细长片
只报一个数就会变成无法交叉检验的伪指标，这是本项目已经栽过六次的坑。

用法: python sweep_metrics.py <a.ply> [<b.ply> ...]
"""
import math
import struct
import sys

SIZE = {"char": 1, "uchar": 1, "int8": 1, "uint8": 1,
        "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
        "int": 4, "uint": 4, "int32": 4, "uint32": 4,
        "float": 4, "float32": 4, "double": 8, "float64": 8}
FMT = {1: "b", 2: "h", 4: "f", 8: "d"}


def read(path):
    with open(path, "rb") as f:
        raw = f.read()
    end = raw.find(b"end_header")
    nl = raw.find(b"\n", end)
    data = raw[nl + 1:]
    props, nv, off, in_v = [], 0, 0, False
    for ln in raw[:end].decode("ascii").split("\n"):
        s = ln.strip()
        if s.startswith("element"):
            in_v = s.split()[1] == "vertex"
            if in_v:
                nv = int(s.split()[2])
            continue
        if not in_v or not s.startswith("property"):
            continue
        p = s.split()
        props.append((p[2], SIZE[p[1]], off))
        off += SIZE[p[1]]
    stride = off
    if len(data) != nv * stride:
        raise SystemExit("size mismatch %s: %d != %d*%d" % (path, len(data), nv, stride))
    idx = {n: (o, sz) for n, o, sz in [(n, o, sz) for n, sz, o in props]}
    sc = []
    need = ["scale_0", "scale_1", "scale_2"]
    if not all(k in idx for k in need):
        raise SystemExit("no scale props in %s" % path)
    o0, s0 = idx["scale_0"]
    for i in range(nv):
        base = i * stride
        v = struct.unpack_from("<fff", data, base + o0) if s0 == 4 else None
        if v is None:
            raise SystemExit("scale_0 is not float32")
        sc.append(v)
    return nv, sc


def q(xs, p):
    if not xs:
        return float("nan")
    ys = sorted(xs)
    k = min(len(ys) - 1, max(0, int(round(p * (len(ys) - 1)))))
    return ys[k]


def main():
    print("%-22s %9s %9s %9s %9s %9s %9s"
          % ("ply", "splats", "Smax_p50", "Smax_p90", "Smax_p99", "Smax_max", "Sgm_p50"))
    for path in sys.argv[1:]:
        nv, sc = read(path)
        smax = [math.exp(max(a, b, c)) for a, b, c in sc]
        sgm = [math.exp((a + b + c) / 3.0) for a, b, c in sc]
        name = path.replace("\\", "/").split("/")[-1]
        print("%-22s %9d %9.4f %9.4f %9.4f %9.4f %9.4f"
              % (name, nv, q(smax, .5), q(smax, .9), q(smax, .99), max(smax), q(sgm, .5)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
