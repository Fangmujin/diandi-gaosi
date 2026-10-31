#!/usr/bin/env python3
"""取景视锥内的脏点统计 —— 回答"框外飞点到底看不看得见"。

为什么要有这个工具
------------------
framing_stats.py（native-brush）算的是"取景框内"，而取景框是一个**竖直圆柱**
（水平 <= h95 且 竖直 <= v95）。飞点落在圆柱外不等于看不见：
只要它在**视锥内且位于相机与主体之间**，就会挡在主体前面。

所以这里按 APK 侧真实口径重建取景相机，再做投影计数：
  - 原点/目标：SfM 点云质心（与 framing_stats.py 同口径，不用 splat 自身质心）
  - 相机姿态：viewer.js 的 home = yaw 30, pitch 18；位置 = target + dist * 方向
  - dist    ：viewer.js::fitDistance() = max(rH/(fill*tX), rV/(fill*tY))，fill=0.9
  - 视场    ：fov=45（PlayCanvas 默认，竖直方向），tY=tan(fov/2)，tX=tY*asp
  - 分轴半径：水平 sqrt(dx^2+dz^2)、竖直 |dy|，各取分位（viewer.js::plyFraming）
  - 采样    ：plyFraming 只等间隔采 <=20000 个点算分位，这里照做（计数仍用全量）

判据：
  A 视锥内（depth>0 且 |横向|/depth <= tX 且 |纵向|/depth <= tY）
  B A 且 depth < dist - r3q  —— 在主体之前，是潜在遮挡物
  C B 且 exp(max(scale)) > 1.0 —— 大飞点，遮挡能力最强
  D A 且 depth > dist + r3q —— 主体之后，表现为背景雾

注意：asp（画布宽高比）会改变 dist 和视锥形状，真机是竖屏。所以两个 asp 都算，
结论必须在这两个极端下一致才敢说。

用法: python occlusion_stats.py <splat.ply> <colmap_dir> [标签]
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_colmap import read_points3d  # noqa: E402

YAW = 30.0
PITCH = 18.0
FOV = 45.0
FILL = 0.9
BIG_SCALE = 1.0
Q = 0.95          # 快档分位（framingQuantile 默认）
SAMPLE = 20000    # plyFraming 的等间隔采样上限
ASPECTS = (0.45, 1.0)
YAW_STEPS = 72     # 绕一圈的采样数（5 度一格）
NEAR = 0.1         # PlayCanvas camera 默认 nearClip（viewer.js 只显式设了 farClip=5000）
FAR = 5000.0


def read_ply(path):
    """独立实现：按 header 声明累加偏移，不假设 stride；长度自检能报错。"""
    with open(path, "rb") as f:
        raw = f.read()
    end = raw.find(b"end_header")
    if end < 0:
        raise SystemExit("no end_header: %s" % path)
    nl = raw.find(b"\n", end)
    data_off = nl + 1
    head = raw[:end].decode("ascii").split("\n")
    if not any(l.strip().startswith("format binary_little_endian") for l in head):
        raise SystemExit("only binary_little_endian supported: %s" % path)
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


def saturated(contribs):
    """线性求和会**高估**：一片盖住整屏的 alpha=0.74 和十片各盖 10% 的 alpha=0.74，
    线性求和分别是 0.74 和 7.4，但真渲染里屏幕上每一点的透射率最多降到 0.26。
    所以再给一个饱和版：假设各片在屏上随机独立覆盖，
      屏幕平均透射率 ≈ Π(1 - alpha_i * frac_i)，雾团 = 1 - 透过率
    它和 native-brush 的光栅化 alpha 合成更接近，两边对不上时先看这个数。"""
    c = np.clip(np.asarray(contribs, dtype=np.float64), 0.0, 1.0)
    if not c.size:
        return 0.0
    return float(-np.expm1(np.sum(np.log1p(-c))))


def verdict(fog):
    """雾团指数的肉眼对照分档。锚点：1.0 = 整屏被一层不透明覆盖；0.1 = 能看出来的量级
    （team-lead 给的锚点）。**档位是量级判断不是阈值**：1σ vs 2σ 的取法会让整列差 4 倍，
    所以只有在同一个约定（本脚本：R = exp(scale) = 1σ）内才可比。"""
    if fog < 0.01:
        return "完全看不出"
    if fog < 0.1:
        return "隐约"
    return "明显"


def quant(a, q):
    """viewer.js::pct 同口径：floor(n*q) 下标，不是插值。"""
    s = np.sort(a)
    return float(s[min(len(s) - 1, int(len(s) * q))])


def main():
    ply, cdir = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else Path(ply).stem
    nv, d = read_ply(ply)
    P = np.array([p["xyz"] for p in read_points3d(str(Path(cdir) / "points3D.bin")).values()])
    cp = P.mean(axis=0)

    X = d["x"].astype(np.float64) - cp[0]
    Y = d["y"].astype(np.float64) - cp[1]
    Z = d["z"].astype(np.float64) - cp[2]
    R = np.sqrt(X * X + Y * Y + Z * Z)
    sc = np.stack([d["scale_0"], d["scale_1"], d["scale_2"]]).astype(np.float64)
    S = np.exp(sc.max(axis=0))                      # 大 splat 计数用：最大轴
    sc = np.sort(np.exp(sc), axis=0)                # 各向异性高斯的两个主轴（上界：忽略朝向）
    S2 = sc[1] * sc[2]
    S_gm = (sc[0] * sc[1] * sc[2]) ** (1.0 / 3.0)   # 各向同性等价尺度（典型朝向的粗估）
    op = 1.0 / (1.0 + np.exp(-d["opacity"].astype(np.float64)))

    # ---- 复现 framing_stats.py 的圆柱口径 ----
    step = max(1, nv // SAMPLE)
    idx = np.arange(0, nv, step)
    H = np.sqrt(X[idx] ** 2 + Z[idx] ** 2)
    V = np.abs(Y[idx])
    h95, v95 = quant(H, Q), quant(V, Q)
    r3q = quant(np.sqrt(X[idx] ** 2 + Y[idx] ** 2 + Z[idx] ** 2), Q)
    Hb = np.sqrt(X ** 2 + Z ** 2)
    inb = (Hb <= h95) & (np.abs(Y) <= v95)
    far_all = int((R > 3).sum())

    print("== %s ==" % label)
    print("  splats %d   SfM 点 %d" % (nv, len(P)))
    print("  圆柱框: h95 %.3f v95 %.3f  框内 %d (%.1f%%)  框内 r>3 = %d / 全局 %d"
          % (h95, v95, int(inb.sum()), 100.0 * inb.sum() / nv,
             int((inb & (R > 3)).sum()), far_all))
    print("  框内大splat>1.0: %d (%.4f%%)"
          % (int((inb & (S > BIG_SCALE)).sum()),
             100.0 * (inb & (S > BIG_SCALE)).sum() / nv))
    print("  r3 p95 %.4f p99 %.4f max %.4f" % (r3q, quant(R, 0.99), float(R.max())))

    # ---- 重建取景相机（viewer.js::applyCam / fitDistance）----
    yaw, pitch = math.radians(YAW), math.radians(PITCH)
    dirv = np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                     math.cos(pitch) * math.cos(yaw)])
    tY0 = math.tan(FOV * math.pi / 360)
    up_w = np.array([0.0, 1.0, 0.0])
    right = np.cross(dirv, up_w)
    right /= np.linalg.norm(right)
    up_c = np.cross(right, dirv)

    # 全量坐标（含质心偏移还原），视锥判定要用
    XYZ = np.stack([X + cp[0], Y + cp[1], Z + cp[2]], axis=1)
    far = R > 3

    for asp in ASPECTS:
        tY = tY0
        tX = tY * asp
        dist = max(h95 / (FILL * tX), v95 / (FILL * tY))
        C = cp + dist * dirv                       # 相机在世界坐标里的位置
        dhat = -dirv                               # 朝向 target = -dirv
        V3 = XYZ - C
        depth = V3 @ dhat
        lat = np.abs(V3 @ right)
        vert = np.abs(V3 @ up_c)
        infr = ((depth > NEAR) & (depth < FAR)
                & (lat <= tX * depth) & (vert <= tY * depth))
        front = infr & (depth < dist - r3q)
        back = infr & (depth > dist + r3q)
        print("  [asp %.2f] dist %.3f | 视锥内 %d | 主体前(潜在遮挡) %d | 其中大splat %d"
              " | 其中不透明>0.5 %d | 主体后 %d"
              % (asp, dist, int(infr.sum()), int(front.sum()),
                 int((front & (S > BIG_SCALE)).sum()),
                 int((front & (op > 0.5)).sum()), int(back.sum())))
        print("            视锥内 r>3 %d | 其中不透明>0.5 %d"
              % (int((infr & far).sum()), int((infr & far & (op > 0.5)).sum())))

        # ---- 雾团指数 ----
        # 计数不能当强度：700 个 alpha=0.05 和 700 个 alpha=0.9 的大 splat 观感完全不同。
        # 占屏比：归一化屏幕坐标下（u = 横向/(tX·depth)，v = 纵向/(tY·depth)，屏幕是 2x2=4）
        #   一个半径 R 的高斯覆盖的椭圆面积 = pi·(R/(tX·d))·(R/(tY·d))，占屏比再除以 4。
        #   R 用 exp(scale) 后的物理尺度（PLY 存 log），取三轴最大 —— 上界。
        #   alpha 用 sigmoid(opacity)（PLY 存 logit）。
        # 累计雾团指数 = sum(alpha x 占屏比)，单个 splat 的贡献封顶 1（不可能盖过整屏两次）。
        # 近远裁剪：nearClip=0.1 是 PlayCanvas 默认值（viewer.js 只显式设了 farClip=5000）。
        # 两种尺度口径：
        #   S2   = 两个最大轴之积 -> 朝向最不利时的上界（细长针状片会被高估）
        #   S_gm = 三轴几何平均   -> 各向同性等价，典型朝向的粗估（细长片会被低估）
        # 真值在两者之间，取决于每个片相对视线的朝向。两个都打，避免拿上界当真值。
        def screen_frac(dk, s2):
            f = np.where(dk > 0, 2 * np.pi * s2 / (4 * tX * tY * np.maximum(dk, 1e-9) ** 2), 0.0)
            return np.minimum(f, 1.0)

        def infr_of(dv):
            """视锥掩码（含近远裁剪）。抽出来是为了让"视锥内 r>3"也能按同一口径数。"""
            Ck = cp + dist * dv
            rw = np.cross(dv, up_w)
            rw /= np.linalg.norm(rw)
            uc = np.cross(rw, dv)
            Vk = XYZ - Ck
            dk = Vk @ (-dv)
            return ((dk > NEAR) & (dk < FAR)
                    & (np.abs(Vk @ rw) <= tX * dk) & (np.abs(Vk @ uc) <= tY * dk))

        def fog_row(dv, label):
            Ck = cp + dist * dv
            dk = (XYZ - Ck) @ (-dv)
            infr = infr_of(dv)
            front = infr & (dk < dist - r3q)
            frac = screen_frac(dk, S2)
            frac_g = screen_frac(dk, S_gm * S_gm)
            contrib = op * frac
            contrib_g = op * frac_g
            fg = float(contrib[front].sum())
            fgg = float(contrib_g[front].sum())
            fa = float(contrib[infr].sum())
            worst = float(frac[front].max()) if front.any() else 0.0
            print("            %-14s 危险splat %5d | 雾团指数 线性 上界 %.4f / 典型 %.4f"
                  " | 饱和 %.4f / %.4f | 最严重单个占屏比 %.4f | %s | 全视锥雾 %.3f | 前方/全视锥 %.1f%%"
                  % (label, int(front.sum()), fg, fgg,
                     saturated(contrib[front]), saturated(contrib_g[front]),
                     worst, verdict(saturated(contrib_g[front])), fa,
                     100.0 * fg / fa if fa else 0.0))
            # top-1 贡献占比：雾团到底是被"一个巨型片"还是"一群中等飞点"撑起来的，
            # 这两者的处置完全不同（前者可枚举，后者只能靠复跑/单调量）。
            # 注意默认参数那批曾经出现 top-1 占 89%，生产参数下只有 17-30%（native-brush 实测），
            # 所以这个数必须每次都算，不能沿用印象。
            # 再注意：top-1 占比本身也是"格位相关"的量（和水平量一样随视角变）
            # —— 钉死 yaw 0° 时典型口径能到 55-67%（个体主导成立），初始/最坏格只有 17-30%。
            # 所以下面两个口径都打，且必须带上格位标注，不能脱离格子引用。
            if front.any():
                t1 = float(contrib[front].max())
                t1g = float(contrib_g[front].max())
                print("               top-1 贡献 上界 %.4f / 雾 %.4f = %.1f%% | 典型 %.4f / 雾 %.4f = %.1f%%"
                      % (t1, fg, 100.0 * t1 / fg if fg else 0.0,
                         t1g, fgg, 100.0 * t1g / fgg if fgg else 0.0))
            # 前 3 名逐条列出来：不看具体 sigma/alpha/depth 就没法判断它是真雾团还是算错了
            idx = np.where(front)[0]
            if len(idx):
                order = idx[np.argsort(-contrib[idx])][:3]
                for j in order:
                    print("               #%-7d sigma(%.3f,%.3f,%.3f) alpha %.3f depth %.3f"
                          " 质心半径 %.3f 占屏比 %.4f 贡献 %.4f"
                          % (j, sc[0][j], sc[1][j], sc[2][j], op[j], dk[j], R[j],
                             frac[j], contrib[j]))
            return int(front.sum()), fg, worst

        print("            --- 雾团指数（锚点：1.0 = 整屏被一层不透明覆盖，0.1 = 可看出来的量级）---")
        fog_row(dirv, "初始yaw30")

        # ---- 绕 yaw 转一圈：用户能拖动旋转，初始视角只是一个朝向 ----
        # 只看 yaw 不放 pitch，因为全自由度会退化成"任何远点都可能挡"，没有信息量。
        rows = []
        any_front = np.zeros(nv, dtype=bool)
        for k in range(YAW_STEPS):
            y = 2 * math.pi * k / YAW_STEPS
            dv = np.array([math.cos(pitch) * math.sin(y), math.sin(pitch),
                           math.cos(pitch) * math.cos(y)])
            Ck = cp + dist * dv
            rw = np.cross(dv, up_w)
            rw /= np.linalg.norm(rw)
            uc = np.cross(rw, dv)
            Vk = XYZ - Ck
            dk = Vk @ (-dv)
            infr_k = ((dk > NEAR) & (dk < FAR)
                      & (np.abs(Vk @ rw) <= tX * dk) & (np.abs(Vk @ uc) <= tY * dk))
            ok = infr_k & (dk < dist - r3q)
            any_front |= ok
            frac = screen_frac(dk, S2)
            frac_g = screen_frac(dk, S_gm * S_gm)
            worst = float(frac[ok].max()) if ok.any() else 0.0
            cu, cg = op * frac, op * frac_g
            t1u = float(cu[ok].max()) if ok.any() else 0.0
            t1g = float(cg[ok].max()) if ok.any() else 0.0
            rows.append((float((op * frac)[ok].sum()), int(ok.sum()), worst,
                         float((op * frac_g)[ok].sum()),
                         saturated((op * frac)[ok]), saturated((op * frac_g)[ok]),
                         int((infr_k & far).sum()), t1u, t1g))
        # "视锥内 r>3" 同样是视角相关的量，按同一条纪律给三行（初始/最坏/中位）
        fars = [r[6] for r in rows]
        iwf = max(range(YAW_STEPS), key=lambda i: fars[i])
        imf = sorted(range(YAW_STEPS), key=lambda i: fars[i])[YAW_STEPS // 2]
        print("            视锥内 r>3: 初始 %d | 绕圈最坏 %d (yaw %d°) | 绕圈中位 %d"
              % (int((infr_of(dirv) & far).sum()),
                 fars[iwf], round(360 * iwf / YAW_STEPS), fars[imf]))
        fgs = [r[0] for r in rows]
        iw = max(range(YAW_STEPS), key=lambda i: fgs[i])
        im = sorted(range(YAW_STEPS), key=lambda i: fgs[i])[YAW_STEPS // 2]
        for tag, i in (("绕圈最坏", iw), ("绕圈中位", im)):
            print("            %-8s yaw=%3d° 危险splat %5d | 线性 上界 %.4f / 典型 %.4f"
                  " | 饱和 上界 %.4f / 典型 %.4f | 最严重单个占屏比 %.4f | %s"
                  % (tag, round(360 * i / YAW_STEPS), rows[i][1], rows[i][0], rows[i][3],
                     rows[i][4], rows[i][5], rows[i][2], verdict(rows[i][5])))
            print("                     top-1 占比 上界 %.1f%% / 典型 %.1f%%"
                  " （集中度也是格位相关量，禁止脱离格位引用）"
                  % (100.0 * rows[i][7] / rows[i][0] if rows[i][0] else 0.0,
                     100.0 * rows[i][8] / rows[i][3] if rows[i][3] else 0.0))
        print("            绕 yaw 一圈曾挡在主体前 %d | 其中大splat %d | 不透明>0.5 %d"
              % (int(any_front.sum()),
                 int((any_front & (S > BIG_SCALE)).sum()),
                 int((any_front & (op > 0.5)).sum())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
