"""生成一个可见的标准 3DGS PLY，用于 viewer 验证。
约定：scale_* 存对数值（exp 后才是实际尺度），opacity 存 sigmoid 前的值。"""
import struct
import random
import os
import math

N = 3000
random.seed(7)

header = (
    "ply\n"
    "format binary_little_endian 1.0\n"
    "element vertex %d\n"
    "property float x\n"
    "property float y\n"
    "property float z\n"
    "property float nx\n"
    "property float ny\n"
    "property float nz\n"
    "property float f_dc_0\n"
    "property float f_dc_1\n"
    "property float f_dc_2\n"
    "property float opacity\n"
    "property float scale_0\n"
    "property float scale_1\n"
    "property float scale_2\n"
    "property float rot_0\n"
    "property float rot_1\n"
    "property float rot_2\n"
    "property float rot_3\n"
    "end_header\n" % N
).encode("ascii")

SH_C0 = 0.28209479177387814       # 0.5 + SH_C0 * f_dc = 颜色
SCALE = math.log(0.035)           # exp 后实际尺度 0.035
OPACITY = math.log(0.92 / 0.08)   # sigmoid 后 ≈ 0.92

data = []
for _ in range(N):
    # 均匀分布在半径 1 的球壳上，再加一点厚度，保证相机一眼能看到实体
    u = random.uniform(-1.0, 1.0)
    theta = random.uniform(0.0, 2.0 * math.pi)
    r = 1.0 + random.uniform(-0.04, 0.04)
    s = math.sqrt(max(0.0, 1.0 - u * u))
    x, y, z = r * s * math.cos(theta), r * s * math.sin(theta), r * u
    nx, ny, nz = x / r, y / r, z / r
    # 按高度做青绿→暖黄渐变，呼应 accent 但不越界到彩色噪点
    t = (u + 1.0) / 2.0
    c0, c1, c2 = 0.25 + 0.45 * t, 0.80 - 0.15 * t, 0.62 - 0.18 * t
    data += [
        x, y, z, nx, ny, nz,
        (c0 - 0.5) / SH_C0, (c1 - 0.5) / SH_C0, (c2 - 0.5) / SH_C0,
        OPACITY, SCALE, SCALE, SCALE,
        1.0, 0.0, 0.0, 0.0,
    ]

out = "C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi/build/demo.ply"
with open(out, "wb") as f:
    f.write(header)
    f.write(struct.pack("<%df" % len(data), *data))
print("wrote", out, "vertices=%d bytes=%d" % (N, os.path.getsize(out)))
