#!/usr/bin/env python3
"""44 台训练相机视锥的并集覆盖率，及其补集（从未被任何训练相机看到的 splat）。

回答 team-lead 派的两个数：
  1. 补集 splat 数占比
  2. 补集与"雾团 top 贡献者"的重叠率

设计要点（防止再出参数化伪指标）：
  * "视锥边界怎么算"是自由参数，本脚本一次算三档定义（A/B/C），
    三档方向若不一致就明说"这个量是定义决定的"，不报单点值。
    A 中心严格：depth>0 且 u,v 落在 [0,W)x[0,H)，带 SIMPLE_RADIAL 畸变。
    B 留 10% margin：u,v 落在 [-0.1W,1.1W)x[-0.1H,1.1H)。
    C 高斯感知：按 1σ 投影半径放宽（σ_px = f·σ_world/depth），即"只要片的一部分进过画面"就算见过。
  * 雾团贡献者取"绕圈最坏视角 + 典型各向异性口径"（决策格，§9.5.14），
    top-K 的 K 也是自由参数，取 {50, 200} 两档。
  * 自带自检：把雾团指数重算一遍，必须复现 occlusion_stats.py 在 yaw=0 的已知值。

用法: python traincam_coverage.py <ply> <colmap_dir> [yaw_deg]
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_colmap as ic          # noqa: E402
import occlusion_stats as oc        # noqa: E402

NEAR, FAR = 0.1, 5000.0
TOPSK = (50, 200)


def qvec_to_R(q):
    return np.array(ic.qvec_to_rotmat(q), dtype=np.float64)


def project(Xc, cam):
    """SIMPLE_RADIAL (f,cx,cy,k1)：返回 (u, v, depth, mask_depth>0)。"""
    f, cx, cy, k1 = cam["params"]
    z = Xc[:, 2]
    ok = z > NEAR
    x = np.where(ok, Xc[:, 0] / np.where(ok, z, 1.0), 0.0)
    y = np.where(ok, Xc[:, 1] / np.where(ok, z, 1.0), 0.0)
    r2 = x * x + y * y
    fac = 1.0 + k1 * r2
    u = f * fac * x + cx
    v = f * fac * y + cy
    return u, v, z, ok


def main():
    ply, cdir = sys.argv[1], sys.argv[2]
    yaw_deg = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

    nv, d = oc.read_ply(ply)
    P = np.array([p["xyz"] for p in ic.read_points3d(str(Path(cdir) / "points3D.bin")).values()])
    cp = P.mean(axis=0)

    X = d["x"].astype(np.float64)
    Y = d["y"].astype(np.float64)
    Z = d["z"].astype(np.float64)
    XYZ = np.stack([X, Y, Z], axis=1)

    sc = np.sort(np.exp(np.stack([d["scale_0"], d["scale_1"], d["scale_2"]]).astype(np.float64)),
                 axis=0)
    s_gm = (sc[0] * sc[1] * sc[2]) ** (1.0 / 3.0)      # 典型各向异性口径
    s2_up = sc[1] * sc[2]                              # 上界口径
    op = 1.0 / (1.0 + np.exp(-d["opacity"].astype(np.float64)))

    cams = ic.read_cameras(str(Path(cdir) / "cameras.bin"))
    imgs = ic.read_images(str(Path(cdir) / "images.bin"))

    # ---------- 1. 并集覆盖率（三档定义） ----------
    seen = {k: np.zeros(nv, dtype=bool) for k in "ABC"}
    n_img = 0
    for im in imgs.values():
        cam = cams[im["camera_id"]]
        W, H = cam["width"], cam["height"]
        f = cam["params"][0]
        R = qvec_to_R(im["qvec"])
        t = np.asarray(im["tvec"], dtype=np.float64)
        Xc = (R @ XYZ.T).T + t
        u, v, z, ok = project(Xc, cam)
        sig_px = f * s_gm / np.maximum(z, 1e-9)
        seen["A"] |= ok & (u >= 0) & (u < W) & (v >= 0) & (v < H)
        seen["B"] |= (ok & (u >= -0.1 * W) & (u < 1.1 * W)
                      & (v >= -0.1 * H) & (v < 1.1 * H))
        seen["C"] |= (ok & (u >= -sig_px) & (u < W + sig_px)
                      & (v >= -sig_px) & (v < H + sig_px))
        n_img += 1

    print("== %s ==" % Path(ply).stem)
    print("  splats %d | 训练相机 %d 台（%dx%d, %s）"
          % (nv, n_img, cams[list(cams)[0]]["width"], cams[list(cams)[0]]["height"],
             cams[list(cams)[0]]["model"]))
    comp = {}
    for k in "ABC":
        c = int((~seen[k]).sum())
        comp[k] = ~seen[k]
        print("  定义 %s: 并集覆盖 %d (%.2f%%) | 补集 %d (%.2f%%)"
              % (k, int(seen[k].sum()), 100.0 * seen[k].sum() / nv, c, 100.0 * c / nv))
    print("    A 中心严格（带畸变，无 margin） / B 留 10%% margin / C 按 1σ 投影半径放宽")

    # ---------- 2. 雾团贡献者（绕圈最坏视角 + 典型口径）并自检 ----------
    yaw = math.radians(yaw_deg)
    pitch = math.radians(oc.PITCH)
    dv = np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                   math.cos(pitch) * math.cos(yaw)])
    up_w = np.array([0.0, 1.0, 0.0])
    tY0 = math.tan(oc.FOV * math.pi / 360)

    Xr, Yr, Zr = X - cp[0], Y - cp[1], Z - cp[2]
    Rr = np.sqrt(Xr * Xr + Yr * Yr + Zr * Zr)
    step = max(1, nv // oc.SAMPLE)
    idx = np.arange(0, nv, step)
    h95 = oc.quant(np.sqrt(Xr[idx] ** 2 + Zr[idx] ** 2), oc.Q)
    v95 = oc.quant(np.abs(Yr[idx]), oc.Q)
    r3q = oc.quant(Rr[idx], oc.Q)

    print("  --- 雾团贡献者：yaw %.0f°（绕圈最坏）+ 典型各向异性口径 ---" % yaw_deg)
    for asp in oc.ASPECTS:
        tX, tY = tY0 * asp, tY0
        dist = max(h95 / (oc.FILL * tX), v95 / (oc.FILL * tY))
        C = cp + dist * dv
        dk = (XYZ - C) @ (-dv)
        infr = ((dk > NEAR) & (dk < FAR)
                & (np.abs((XYZ - C) @ np.cross(dv, up_w) / np.linalg.norm(np.cross(dv, up_w)))
                   <= tX * dk)
                & (np.abs((XYZ - C) @ np.cross(np.cross(dv, up_w) / np.linalg.norm(np.cross(dv, up_w)), dv))
                   <= tY * dk))
        front = infr & (dk < dist - r3q)
        frac_up = np.minimum(2 * np.pi * s2_up / (4 * tX * tY * np.maximum(dk, 1e-9) ** 2), 1.0)
        frac_gm = np.minimum(2 * np.pi * (s_gm * s_gm) / (4 * tX * tY * np.maximum(dk, 1e-9) ** 2), 1.0)
        fg_up = float((op * frac_up)[front].sum())
        fg_gm = float((op * frac_gm)[front].sum())
        print("    [asp %.2f] 自检：雾 上界 %.4f / 典型 %.4f | 前方 splat %d"
              % (asp, fg_up, fg_gm, int(front.sum())))
        if abs(asp - 0.45) > 1e-9:
            continue

        # 贡献者名单（典型口径，与决策格一致）
        contrib = op * frac_gm
        fidx = np.where(front)[0]
        order = fidx[np.argsort(-contrib[fidx])]
        print("    重叠率（补集 ∩ 雾团 top-K）/ K：")
        for k in TOPSK:
            top = order[:k]
            row = ["%s %.1f%%" % (dk_, 100.0 * (comp[dk_][top]).sum() / k) for dk_ in "ABC"]
            print("      K=%-4d 补集占比 A/B/C = %.2f%%/%.2f%%/%.2f%% | 重叠: %s"
                  % (k, 100.0 * comp["A"].sum() / nv, 100.0 * comp["B"].sum() / nv,
                     100.0 * comp["C"].sum() / nv, "  ".join(row)))
        # 全量前方贡献者与补集的重叠（不限 K，避免只看头部）
        print("      全体前方贡献者 %d 个中，落在补集的比例：A %.1f%% / B %.1f%% / C %.1f%%"
              % (len(fidx), 100.0 * comp["A"][fidx].sum() / len(fidx),
                 100.0 * comp["B"][fidx].sum() / len(fidx),
                 100.0 * comp["C"][fidx].sum() / len(fidx)))


if __name__ == "__main__":
    main()
