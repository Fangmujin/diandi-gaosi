#!/usr/bin/env python3
"""从**查看页的合成相机**出图 —— 把"雾团指数 0.5"变成一张能看的图。

为什么必须另写一个渲染器
------------------------
render_compare.py 用的是训练相机（贴在物体旁边，dist ~2），而 floaters 的问题是
**竖屏查看页站在 dist ~10-12 处才会暴露**：那时相机远、巨型片落在相机与主体之间。
从训练视角渲染根本看不到它，所以"画出来看"这一步必须按查看页的相机来。

相机参数严格照 viewer.js 重建（与 native-sfm 的 occlusion_stats.py 同一套）：
  home yaw=30 / pitch=18，fitDistance(fill=0.9)，fov=45（竖直），
  画布 411x914（apk-shell 实测 asp=0.4497），dist = max(h95/(fill*tX), v95/(fill*tY))
  h95/v95 = 分轴 p95（viewer.js::plyFraming），原点 = SfM 点云质心
渲染公式与 render_compare.py 完全一致（cov3d/cov2d/SH DC/远到近合成）。

用法: python viewrender.py <splat.ply> <colmap_dir> [输出png] [yaw] [pitch] [only]

only: all(默认) / front / rest
  front = 视锥内且 depth < dist − r3_p95（雾团指数积分的那个集合，occlusion_stats 同口径）
  rest  = 其余全部（主体 + 主体后）
  为什么要拆：雾团指数只积分 front，而渲染把所有 splat 都画上去。当"图像发白但
  指数很低"时，必须能回答"这层白是 front 的雾还是 rest 画的"——否则指数和图像
  打架时没法裁决。两个都渲一遍，占屏数字直接对比。
"""
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sfm" / "tools"))
sys.path.insert(0, str(ROOT / "testdata" / "steptest"))
from inspect_colmap import read_points3d  # noqa: E402
from render_compare import write_png      # noqa: E402
from occlusion_index import COV_BLUR, load_ply, splat2d  # noqa: E402
from tail_crosstab import read_ply_all    # noqa: E402

C0 = 0.28209479177387814
FOV = 45.0
FILL = 0.9
W, H = 411, 914          # apk-shell 实测查看页画布
ASP = W / H


def render(mu, c2, d, rgb, alpha, inside, W, H):
    ys, xs = np.mgrid[0:H, 0:W]
    canvas = np.zeros((H, W, 3))
    trans = np.ones((H, W))          # 剩余透过率，用来直接量"被糊掉多少"
    order = np.argsort(-d)           # 远 -> 近
    for i in order:
        if not inside[i] or d[i] <= 0 or alpha[i] < 0.004:
            continue
        s = c2[i]
        det = max(s[0, 0] * s[1, 1] - s[0, 1] ** 2, 1e-12)
        inv = np.array([[s[1, 1], -s[0, 1]], [-s[0, 1], s[0, 0]]]) / det
        # inv 的特征值是 1/σ²，所以 σ_max = 1/sqrt(min(inv))。窗口半宽取 3σ_max。
        hw = min(int(3.0 / math.sqrt(max(min(inv[0, 0], inv[1, 1]), 1e-12))) + 2, 600)
        x0, x1 = max(int(mu[i, 0]) - hw, 0), min(int(mu[i, 0]) + hw + 1, W)
        y0, y1 = max(int(mu[i, 1]) - hw, 0), min(int(mu[i, 1]) + hw + 1, H)
        if x0 >= x1 or y0 >= y1:
            continue
        dx = xs[y0:y1, x0:x1] - mu[i, 0]
        dy = ys[y0:y1, x0:x1] - mu[i, 1]
        m2 = dx * dx * inv[0, 0] + 2 * dx * dy * inv[0, 1] + dy * dy * inv[1, 1]
        ai = alpha[i] * np.exp(-0.5 * m2)
        blk = canvas[y0:y1, x0:x1]
        canvas[y0:y1, x0:x1] = ai[..., None] * rgb[i] + (1.0 - ai[..., None]) * blk
        trans[y0:y1, x0:x1] *= (1.0 - ai)
    return canvas, trans


def main():
    ply = sys.argv[1]
    cdir = Path(sys.argv[2])
    out = sys.argv[3] if len(sys.argv) > 3 else "viewrender.png"
    yaw = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0
    pitch = float(sys.argv[5]) if len(sys.argv) > 5 else 18.0

    nv, cols = read_ply_all(ply)
    xyz = np.stack([np.asarray(cols[k]) for k in "xyz"], axis=1)
    sig = np.stack([np.exp(np.asarray(cols["scale_%d" % k])) for k in range(3)], axis=1)
    quat = np.stack([np.asarray(cols["rot_%d" % k]) for k in range(4)], axis=1)
    alpha = 1.0 / (1.0 + np.exp(-np.asarray(cols["opacity"])))
    fdc = np.stack([np.asarray(cols["f_dc_%d" % k]) for k in range(3)], axis=1)
    rgb = np.clip(0.5 + C0 * fdc, 0, 1)
    # ⚠ 只用 SH 的 DC 项（f_dc_*），高阶项（f_rest_*）全部丢弃。
    # 后果：颜色是"各方向平均色"，没有视角相关的高光/明暗。
    # 所以本脚本的图只能回答"有没有雾、主体在不在"，**不能**回答"颜色对不对、
    # 清不清晰"——后者是真机专属判据。每次出图都要把这句一起报出来。
    # read_ply_all 只返回它认得的列，f_rest_* 不在里面，所以属性数必须直接读 header。
    with open(ply, "rb") as fh:
        buf = fh.read(1 << 16)
    head = buf[:buf.find(b"end_header")].decode("ascii", "ignore")
    n_rest = sum(1 for ln in head.split("\n")
                 if ln.strip().startswith("property") and " f_rest_" in ln)
    # 每通道的 rest 系数个数：sh1=3、sh2=8、sh3=15（总数再 ×3 通道）
    sh_deg = {0: 0, 3: 1, 8: 2, 15: 3}.get(n_rest // 3, -1)
    print("⚠ 颜色只用 SH 的 DC 项：PLY 有 %d 个 f_rest_* 属性（sh_degree≈%d），"
          "本渲染器全部丢弃 -> 无视角相关明暗，颜色失真属已知限制，不是质量问题。"
          % (n_rest, sh_deg))
    P = np.array([v["xyz"] for v in read_points3d(cdir / "points3D.bin").values()])
    cp = P.mean(0)

    step = max(1, nv // 20000)
    S = xyz[::step] - cp
    h95 = float(np.percentile(np.sqrt(S[:, 0] ** 2 + S[:, 2] ** 2), 95))
    v95 = float(np.percentile(np.abs(S[:, 1]), 95))
    tY = math.tan(FOV * math.pi / 360)
    tX = tY * ASP
    dist = max(h95 / (FILL * tX), v95 / (FILL * tY))
    f = H / (2 * tY)
    cx, cy = W / 2.0, H / 2.0

    y, p = math.radians(yaw), math.radians(pitch)
    dirv = np.array([math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y)])
    C = cp + dist * dirv
    z_c = -dirv
    up = np.array([0.0, 1.0, 0.0])
    x_c = np.cross(up, z_c)
    x_c /= np.linalg.norm(x_c)
    y_c = np.cross(x_c, z_c)          # 图像 v 向下
    Rc = np.stack([x_c, y_c, z_c])
    t = -Rc @ C

    mu, c2, d, inside = splat2d(xyz, sig, quat, Rc, t, f, cx, cy)

    only = sys.argv[6] if len(sys.argv) > 6 else "all"
    if only != "all":
        r3 = np.sqrt(((xyz[::step] - cp) ** 2).sum(1))
        # ⚠ np.percentile 的 q 是百分数(0~100)，不是 0~1 —— 写成 0.95 会取到近似最小值，
        #   front 截止深度会多出近 2 个单位、把大半个主体都划成"主体前"。这里必须是 95。
        r3q = float(np.percentile(r3, 95))
        depth = (xyz - C) @ (-dirv)
        frontm = inside & (depth < dist - r3q)
        keep = frontm if only == "front" else inside & ~frontm
        print("  [only=%s] 保留 %d / 视锥内 %d (h95 %.3f v95 %.3f r3_p95 %.3f, front 截止 depth %.3f)"
              % (only, int(keep.sum()), int(inside.sum()), h95, v95, r3q, dist - r3q))
        inside = keep

    img, trans = render(mu, c2, d, rgb, alpha, inside, W, H)
    print("%s: splats %d 视锥内 %d | dist %.3f f %.1f | 不透明(1-T)>0.1 占屏 %.3f "
          ">0.5 占屏 %.3f | 平均被糊 %.4f"
          % (Path(ply).name, nv, int(inside.sum()), dist, f,
             float(((1 - trans) > 0.1).mean()), float(((1 - trans) > 0.5).mean()),
             float((1 - trans).mean())))
    write_png(out, (np.clip(img, 0, 1) * 255).astype(np.uint8))
    print("  -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
