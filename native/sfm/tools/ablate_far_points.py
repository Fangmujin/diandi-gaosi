#!/usr/bin/env python3
"""消融数据集生成器：把 SfM 点云里的远场点摘掉，其余一律不动。

为什么用"事后摘点"而不是"提高三角化阈值重跑"
--------------------------------------------
要分开两个混杂变量（离群点数 vs 视角数），最直接的做法是重跑一次 SfM 并调阈值。
但调阈值不是一个变量：阈值一动，三角化接受的点集、BA 的目标函数、甚至注册帧数
都可能跟着变，产出的不是"少了 52 个远点"而是"另一套点云"。

事后摘点只改一件事：删掉那 52 个点（并把 images.bin 里对它们的引用置成 COLMAP
的 -1 未注册哨兵）。位姿、内参、图像、其余点全部逐字节不动。这样
  ds_44        (44 视角, 52 远点)
  ds_44_nofar  (44 视角,  0 远点)   <- 本脚本产出
  ds_11        (11 视角,  0 远点)
两两对比就能把"视角数"和"离群点数"分开。

**这是 oracle 过滤**：真实阈值做不到只摘这 52 个，所以它是"收益上界"。
结论要按上界读 —— 如果连上界都没差，那阈值怎么调都没用。

用法: python ablate_far_points.py <colmap_dir> <out_dir> [半径阈值=3.0]
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roundtrip_colmap import (parse_cameras, parse_images, parse_points3d,  # noqa: E402
                              ser_cameras, ser_images, ser_points3d)


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    thr = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0

    cams = parse_cameras(str(src / "cameras.bin"))
    imgs = parse_images(str(src / "images.bin"))
    pts = parse_points3d(str(src / "points3D.bin"))

    cx = sum(p["xyz"][0] for p in pts) / len(pts)
    cy = sum(p["xyz"][1] for p in pts) / len(pts)
    cz = sum(p["xyz"][2] for p in pts) / len(pts)

    def rad(p):
        return ((p["xyz"][0] - cx) ** 2 + (p["xyz"][1] - cy) ** 2
                + (p["xyz"][2] - cz) ** 2) ** 0.5

    far = [p for p in pts if rad(p) > thr]
    keep = [p for p in pts if rad(p) <= thr]
    drop_ids = set(p["id"] for p in far)
    print("源 %s: 点 %d, 质心 (%.4f, %.4f, %.4f)" % (src, len(pts), cx, cy, cz))
    print("  阈值 r>%.2f 命中 %d 个（%.3f%%），最远 %.4f"
          % (thr, len(far), 100.0 * len(far) / len(pts),
             max((rad(p) for p in far), default=0.0)))

    # 注意：原本未注册的特征点 pid 已经是 -1，不能把它们算进"被改动"的计数里
    n_pre = sum(1 for im in imgs for (_, _, pid) in im["points"] if pid == -1)
    n_ref = 0
    for im in imgs:
        new = []
        for (x, y, pid) in im["points"]:
            if pid in drop_ids:
                n_ref += 1
                new.append((x, y, -1))
            else:
                new.append((x, y, pid))
        im["points"] = new

    dst.mkdir(parents=True, exist_ok=True)
    (dst / "cameras.bin").write_bytes(ser_cameras(cams))
    (dst / "images.bin").write_bytes(ser_images(imgs))
    (dst / "points3D.bin").write_bytes(ser_points3d(keep))
    total = sum(len(im["points"]) for im in imgs)
    print("  写出 %s: 点 %d（-%d），改动引用 %d 条 / 全部 %d 条（原已为 -1 的 %d 条不动）"
          % (dst, len(keep), len(far), n_ref, total, n_pre))
    print("  被删点的 track 长度: 合计 %d, 最长 %d, 最短 %d"
          % (sum(len(p["track"]) for p in far),
             max(len(p["track"]) for p in far),
             min(len(p["track"]) for p in far)))

    # 自检：读回来，确认没有指向已删点的悬空 id
    back = parse_points3d(str(dst / "points3D.bin"))
    back_ids = set(p["id"] for p in back)
    bad = 0
    for im in parse_images(str(dst / "images.bin")):
        bad += sum(1 for (_, _, pid) in im["points"] if pid != -1 and pid not in back_ids)
    assert len(back) == len(keep), "点数对不上"
    assert bad == 0, "仍有 %d 条悬空引用" % bad
    print("  自检通过：读回 %d 点，悬空引用 0" % len(back))

    # COLMAP 数据集布局：<root>/images + <root>/sparse/0，训练器要读图
    s = src.parent.parent / "images"
    d = dst.parent.parent / "images"
    if s.is_dir() and not d.exists():
        shutil.copytree(s, d)
        print("  已复制 %s -> %s" % (s, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
