#!/usr/bin/env python3
"""视锥内前景遮挡 —— 用小型光栅化直接测"主体前面糊了多少"。

为什么不用「取景框内飞点数」
----------------------------
取景框按分位数（分轴 p95 ≈ 2.05）定义，"远场"判据是欧氏半径 r > 3，
阈值本来就取在取景半径之上，「框内 r>3 = 0」是几何上预定的，与画面脏不脏无关
—— 和"落在 SfM bbox 内 99.4%"是同一类自证指标。

为什么不用「Σ α·2π√det(cov2d) / 屏面积」这类解析积分
----------------------------------------------------
它把**屏外**的 splat 也按全额计入：一个中心在画面外一米、只在画面里露出一条边的
巨大 splat，解析积分算出来和摆在正中央一样大。所以解析积分只能当上界，
真实遮挡必须位置感知 —— 那就光栅化。

做法
----
对每个训练相机（images.bin 的 qvec/tvec，COLMAP world→camera）：
1. 每个 splat 投影出 2D 高斯：μ₂d = (f·X/Z + cx, f·Y/Z + cy)，
   Σ₂d = J Σ_cam Jᵀ。公式逐条对照
   `crates/brush-render/src/shaders/helpers.wgsl`：
   - calc_cov3d(156-159): cov3d = M Mᵀ, M = R(quat)·diag(scale) → **scale 就是 σ**
   - quat_to_mat(111-116): PLY 的 rot_0=w, rot_1=x, rot_2=y, rot_3=z
   - calc_cov2d(179-191): cov2d = J·(R_cam cov3d R_camᵀ)·Jᵀ，对角加 COV_BLUR
2. 在降采样 4× 的网格上光栅化，按深度分三桶**分别累计**（前到后 alpha 合成）：
     前景   d < 0.8 · d_mid          （挡在主体前面的东西）
     主体带 [p10, p90] 的中位深度 ±（场景本体，作为"正常不透明"的参照）
     背景   d > 1.25 · d_mid
   d_mid = 有效深度 [p10,p90] 带的中位数（由深度分布定，不由取景定）
3. 每桶输出：像素级 occ = 1 - Π(1 - α_i) 的**屏幕占比**（occ > 0.1 / > 0.5），
   以及前景/主体的比值 —— 比值是自校准的，不受绝对单位影响。

用法: python occlusion_index.py <splat.ply> <colmap_dir> [标签]
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sfm" / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_colmap import read_cameras, read_images, qvec_to_rotmat  # noqa: E402
from tail_crosstab import read_ply_all  # noqa: E402

COV_BLUR = 0.3     # helpers.wgsl 加到 cov2d 对角线上的模糊量
DS = 8             # 光栅化降采样倍数（修窗 bug 后窗口变大，用 8 控耗时；测的是面积占比，够用）
WIN_CAP = 240      # 单 splat 光栅化窗口半宽上限（网格像素），防巨大 splat 拖死耗时


def quat_to_rotmat_np(q):
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((q.shape[0], 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z)
    R[:, 0, 1] = 2 * (x * y - z * w)
    R[:, 0, 2] = 2 * (x * z + y * w)
    R[:, 1, 0] = 2 * (x * y + z * w)
    R[:, 1, 1] = 1 - 2 * (x * x + z * z)
    R[:, 1, 2] = 2 * (y * z - x * w)
    R[:, 2, 0] = 2 * (x * z - y * w)
    R[:, 2, 1] = 2 * (y * z + x * w)
    R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R


def load_ply(ply):
    nv, cols = read_ply_all(ply)
    xyz = np.stack([np.asarray(cols[k]) for k in "xyz"], axis=1)
    sig = np.stack([np.exp(np.asarray(cols["scale_%d" % k])) for k in range(3)], axis=1)
    quat = np.stack([np.asarray(cols["rot_%d" % k]) for k in range(4)], axis=1)
    alpha = 1.0 / (1.0 + np.exp(-np.asarray(cols["opacity"])))
    return nv, xyz, sig, quat, alpha


def splat2d(xyz, sig, quat, Rc, t, f, cx, cy):
    """返回投影后的 μ₂d(N,2)、Σ₂d(N,2,2)、深度 d(N)、视锥内掩码(N,)"""
    n = len(xyz)
    Xc = xyz @ Rc.T + t
    d = Xc[:, 2]
    ok = d > 1e-6
    rz = np.where(ok, 1.0 / np.maximum(d, 1e-6), 0.0)
    mu = np.stack([f * Xc[:, 0] * rz + cx, f * Xc[:, 1] * rz + cy], axis=1)
    R3 = quat_to_rotmat_np(quat)
    ccam = np.einsum("ij,njk,lk->nil", Rc, np.einsum("nij,nj,nkj->nik", R3, sig ** 2, R3), Rc)
    J = np.zeros((n, 2, 3))
    J[:, 0, 0] = f * rz
    J[:, 1, 1] = f * rz
    J[:, 0, 2] = -f * Xc[:, 0] * rz * rz
    J[:, 1, 2] = -f * Xc[:, 1] * rz * rz
    c2 = np.einsum("nij,njk,nlk->nil", J, ccam, J)
    c2[:, 0, 0] += COV_BLUR
    c2[:, 1, 1] += COV_BLUR
    inside = ok & (mu[:, 0] > -f * 0.2) & (mu[:, 0] < f * 1.2) \
        & (mu[:, 1] > -f * 0.2) & (mu[:, 1] < f * 1.2)
    return mu, c2, d, inside


def rasterize(mu, c2, a, sel, W4, H4):
    """把选中的 splat 光栅化进 (H4,W4) 的 occ 图，前到后合成。"""
    occ = np.zeros((H4, W4))
    ys, xs = np.mgrid[0:H4, 0:W4]
    px = xs * DS + (DS - 1) / 2.0
    py = ys * DS + (DS - 1) / 2.0
    idx = np.where(sel)[0]
    capped = 0
    for i in idx:
        if a[i] < 0.004:
            continue
        s = c2[i]
        det = max(s[0, 0] * s[1, 1] - s[0, 1] ** 2, 1e-12)
        inv = np.array([[s[1, 1], -s[0, 1]], [-s[0, 1], s[0, 0]]]) / det
        # 等高线 α>0.004 的半径: r² = 2σ_major²·ln(α/0.004)，
        # σ_major = 1/sqrt(min(inv 特征值))（inv 的特征值是 1/σ²）
        r2 = 2.0 * math.log(max(a[i] / 0.004, 1.0001))
        rad = math.sqrt(max(r2 / max(min(inv[0, 0], inv[1, 1]), 1e-12), 0.0))
        hw = min(int(rad / DS) + 2, WIN_CAP)
        if hw >= WIN_CAP:
            capped += 1
        x0, x1 = max(int(mu[i, 0] / DS) - hw, 0), min(int(mu[i, 0] / DS) + hw + 1, W4)
        y0, y1 = max(int(mu[i, 1] / DS) - hw, 0), min(int(mu[i, 1] / DS) + hw + 1, H4)
        if x0 >= x1 or y0 >= y1:
            continue
        dx = px[y0:y1, x0:x1] - mu[i, 0]
        dy = py[y0:y1, x0:x1] - mu[i, 1]
        m2 = dx * dx * inv[0, 0] + 2 * dx * dy * inv[0, 1] + dy * dy * inv[1, 1]
        ai = a[i] * np.exp(-0.5 * m2)
        blk = occ[y0:y1, x0:x1]
        occ[y0:y1, x0:x1] = 1.0 - (1.0 - blk) * (1.0 - ai)
    return occ, capped


def band_stats(occ):
    return (float((occ > 0.1).mean()), float((occ > 0.5).mean()), float(occ.mean()))


def main():
    ply, cdir = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else Path(ply).stem
    cdir = Path(cdir)
    nv, xyz, sig, quat, alpha = load_ply(ply)
    cams = read_cameras(cdir / "cameras.bin")
    imgs = read_images(cdir / "images.bin")

    acc = {"fg": [], "mid": [], "bg": [], "capped": 0, "cams": 0}
    for iid, im in sorted(imgs.items()):
        cam = cams[im["camera_id"]]
        f, cx, cy = cam["params"][0], cam["params"][1], cam["params"][2]
        W, H = cam["width"], cam["height"]
        Rc = np.asarray(qvec_to_rotmat(im["qvec"]))
        t = np.asarray(im["tvec"])
        mu, c2, d, inside = splat2d(xyz, sig, quat, Rc, t, f, cx, cy)
        dv = d[inside]
        if len(dv) < 100:
            continue
        d_mid = float(np.median(dv[(dv >= np.percentile(dv, 10))
                                   & (dv <= np.percentile(dv, 90))]))
        use = inside & (alpha > 0.004)
        fg = use & (d < 0.8 * d_mid)
        mid = use & (np.abs(d - d_mid) <= 0.25 * d_mid)
        bg = use & (d > 1.25 * d_mid)
        for key, sel in (("fg", fg), ("mid", mid), ("bg", bg)):
            o, capped = rasterize(mu, c2, alpha, sel, W // DS, H // DS)
            acc[key].append(band_stats(o))
            acc["capped"] += capped
        acc["cams"] += 1

    print("== {} ==  (splats {}, 相机 {}, 网格 {}/{})".format(
        label, nv, acc["cams"], cams[1]["width"] // DS, cams[1]["height"] // DS))
    print("%-16s %28s %28s %28s" % ("camera", "前景 occ>0.1/>0.5/mean",
                                    "主体带 occ>0.1/>0.5/mean",
                                    "背景 occ>0.1/>0.5/mean"))
    for k, im in enumerate(sorted(imgs.values(), key=lambda x: x["name"])):
        if k >= len(acc["fg"]):
            break
        r = (acc["fg"][k], acc["mid"][k], acc["bg"][k])
        print("%-16s %28s %28s %28s" % (
            im["name"],
            "%.3f / %.3f / %.4f" % r[0],
            "%.3f / %.3f / %.4f" % r[1],
            "%.3f / %.3f / %.4f" % r[2]))
    print("\n汇总(中位 / p90, %d 个相机):" % acc["cams"])
    for key, name in (("fg", "前景"), ("mid", "主体带"), ("bg", "背景")):
        A = np.array(acc[key])
        print("  %s: occ>0.1 占屏 中位 %.3f p90 %.3f | occ>0.5 中位 %.3f p90 %.3f | mean 中位 %.4f"
              % (name, np.median(A[:, 0]), np.percentile(A[:, 0], 90),
                 np.median(A[:, 1]), np.percentile(A[:, 1], 90),
                 np.median(A[:, 2])))
    fg10 = np.array([x[0] for x in acc["fg"]])
    mid10 = np.array([x[0] for x in acc["mid"]])
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(mid10 > 0, fg10 / np.maximum(mid10, 1e-9), np.nan)
    print("  前景/主体 (occ>0.1 占屏比): 中位 %.3f  p90 %.3f" %
          (np.nanmedian(ratio), np.nanpercentile(ratio, 90)))
    print("  窗口截断的 splat 次数: %d (巨大 splat 被裁到 %d 网格半宽, 轻微低估)"
          % (acc["capped"], WIN_CAP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
