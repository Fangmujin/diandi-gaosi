"""把真实 3DGS PLY 截断到前 N 个点，用于模拟器上的查看页回归（原文件 28MB 在回环上加载过慢）。

用法：python build/truncate_ply.py <in.ply> <out.ply> [N]
"""
import os
import re
import struct
import sys

TYPES = {
    "char": 1, "uchar": 1, "int8": 1, "uint8": 1,
    "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
    "int": 4, "uint": 4, "int32": 4, "uint32": 4, "float": 4, "float32": 4,
    "double": 8, "float64": 8,
}

src = sys.argv[1]
dst = sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 20000

with open(src, "rb") as f:
    blob = f.read(1 << 20)  # header 一定在头 1MB 内

m = re.search(rb"end_header\s*\n", blob)
if not m:
    raise SystemExit("no end_header")
head = blob[:m.end()].decode("ascii", "replace")
data_off = m.end()

if b"binary_big_endian" in head.encode("ascii"):
    raise SystemExit("big endian not supported")

count = int(re.search(r"element\s+vertex\s+(\d+)", head).group(1))
stride = 0
inside_vertex = False
for line in head.splitlines():
    line = line.strip()
    if line.startswith("element"):
        inside_vertex = line.split()[1] == "vertex"
        continue
    if line.startswith("property") and inside_vertex:
        stride += TYPES[line.split()[1]]
if stride == 0:
    raise SystemExit("cannot compute stride")

N = min(N, count)
print("src=%s splats=%d stride=%dB -> keep %d" % (src, count, stride, N))

# 只重写 vertex 数量，其余 header 原样保留
new_head = re.sub(r"(element\s+vertex\s+)(\d+)", lambda mm: mm.group(1) + str(N), head, count=1)

with open(src, "rb") as fi, open(dst, "wb") as fo:
    fi.seek(data_off)
    fo.write(new_head.encode("ascii"))
    remaining = N * stride
    while remaining > 0:
        chunk = fi.read(min(1 << 20, remaining))
        if not chunk:
            break
        fo.write(chunk)
        remaining -= len(chunk)

print("wrote %s  %d bytes" % (dst, os.path.getsize(dst)))
