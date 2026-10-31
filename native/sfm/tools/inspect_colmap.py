#!/usr/bin/env python3
"""独立实现一份 COLMAP 二进制读取器, 用来交叉校验 libiii_sfm 的产物。

字段级对齐 COLMAP 官方 scripts/python/read_write_model.py:
  cameras.bin : u64 n, {i32 id, i32 model, u64 w, u64 h, f64 params[4]}
  images.bin  : u64 n, {i32 id, f64 q[4], f64 t[3], i32 cam_id,
                        char[] name(NUL 结尾, 无长度前缀), u64 npts,
                        {f64 x, f64 y, i64 pid} * npts}
  points3D.bin: u64 n, {i64 id, f64 xyz[3], u8 rgb[3], f64 err,
                        u64 track_len, {i32 img_id, i32 p2d_idx}}
"""
import argparse
import math
import struct
import sys
from pathlib import Path

CAMERA_MODELS = {
    0: ("SIMPLE_PINHOLE", 3),
    1: ("PINHOLE", 4),
    2: ("SIMPLE_RADIAL", 4),
    3: ("RADIAL", 5),
    4: ("OPENCV", 8),
}


def read_next(f, fmt):
    size = struct.calcsize(fmt)
    data = f.read(size)
    if len(data) != size:
        return None
    return struct.unpack(fmt, data)


def read_cameras(path):
    cams = {}
    with open(path, "rb") as f:
        n = read_next(f, "<Q")[0]
        for _ in range(n):
            cid, mid = read_next(f, "<ii")
            w, h = read_next(f, "<QQ")
            name, nparam = CAMERA_MODELS.get(mid, ("UNKNOWN", 0))
            params = read_next(f, "<%dd" % nparam)
            cams[cid] = dict(id=cid, model=name, model_id=mid, width=w, height=h, params=params)
    return cams


def qvec_to_rotmat(q):
    qw, qx, qy, qz = q
    n = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    qw, qx, qy, qz = qw / n, qx / n, qy / n, qz / n
    return [
        [1 - 2 * qy * qy - 2 * qz * qz, 2 * qx * qy - 2 * qz * qw, 2 * qy * qw + 2 * qx * qz],
        [2 * qx * qy + 2 * qz * qw, 1 - 2 * qx * qx - 2 * qz * qz, 2 * qy * qz - 2 * qx * qw],
        [2 * qx * qz - 2 * qy * qw, 2 * qy * qz + 2 * qx * qw, 1 - 2 * qx * qx - 2 * qy * qy],
    ]


def read_images(path):
    imgs = {}
    with open(path, "rb") as f:
        n = read_next(f, "<Q")[0]
        for _ in range(n):
            iid = read_next(f, "<i")[0]
            q = read_next(f, "<4d")
            t = read_next(f, "<3d")
            cid = read_next(f, "<i")[0]
            # COLMAP 二进制 images.bin 里图像名是 NUL 结尾、不带长度前缀的字符串
            # (与官方 read_write_model.py / brush 的 colmap-reader 一致)
            name_buf = bytearray()
            while True:
                ch = f.read(1)
                if not ch or ch == b"\x00":
                    break
                name_buf += ch
            name = name_buf.decode("utf-8", "replace")
            npts = read_next(f, "<Q")[0]
            # COLMAP 的 2D 点是交错存放的: 每个点 f64 x, f64 y, i64 point3D_id (24 字节)
            blob = f.read(24 * npts)
            pids = []
            xys = []
            for k in range(npts):
                off = 24 * k
                xys.append((struct.unpack_from("<d", blob, off)[0],
                            struct.unpack_from("<d", blob, off + 8)[0]))
                pids.append(struct.unpack_from("<q", blob, off + 16)[0])
            imgs[iid] = dict(id=iid, qvec=q, tvec=t, camera_id=cid, name=name,
                             xys=xys, point3D_ids=pids)
    return imgs


def read_points3d(path):
    pts = {}
    with open(path, "rb") as f:
        n = read_next(f, "<Q")[0]
        for _ in range(n):
            pid = read_next(f, "<q")[0]
            xyz = read_next(f, "<3d")
            rgb = read_next(f, "<3B")
            err = read_next(f, "<d")[0]
            tlen = read_next(f, "<Q")[0]
            track = read_next(f, "<%di" % (2 * tlen))
            pts[pid] = dict(id=pid, xyz=xyz, rgb=rgb, error=err,
                            track=list(zip(track[0::2], track[1::2])))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("colmap_dir")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    d = Path(args.colmap_dir)
    cams = read_cameras(d / "cameras.bin")
    imgs = read_images(d / "images.bin")
    pts = read_points3d(d / "points3D.bin")

    print("cameras : %d" % len(cams))
    for c in cams.values():
        print("   id=%d %s %dx%d params=%s" %
              (c["id"], c["model"], c["width"], c["height"],
               ", ".join("%.3f" % p for p in c["params"])))
    print("images  : %d registered" % len(imgs))
    print("points  : %d" % len(pts))

    # 交叉校验: images 里引用的 point3D 必须存在
    obs = 0
    missing = 0
    for im in imgs.values():
        for pid in im["point3D_ids"]:
            if pid < 0:
                continue
            obs += 1
            if pid not in pts:
                missing += 1
    track_obs = sum(len(p["track"]) for p in pts.values())
    print("observations (from images) : %d" % obs)
    print("observations (from tracks) : %d" % track_obs)
    print("dangling point3D refs      : %d" % missing)

    # 校验位姿: 四元数单位化, 相机中心不重合
    centers = []
    for im in imgs.values():
        R = qvec_to_rotmat(im["qvec"])
        t = im["tvec"]
        c = [-sum(R[i][j] * t[j] for j in range(3)) for i in range(3)]
        centers.append(c)
    if len(centers) >= 2:
        dists = []
        for i in range(len(centers) - 1):
            a, b = centers[i], centers[i + 1]
            dists.append(math.dist(a, b))
        print("camera center step dist    : min %.4f  max %.4f  mean %.4f" %
              (min(dists), max(dists), sum(dists) / len(dists)))
        # 光心是否退化(全部重合说明没有真实位姿)
        span = max(max(abs(c[k] - centers[0][k]) for c in centers) for k in range(3))
        print("camera center span (bbox)  : %.4f" % span)

    if pts:
        errs = [p["error"] for p in pts.values()]
        errs_sorted = sorted(errs)
        print("mean reproj error (stored) : %.4f px" % (sum(errs) / len(errs)))
        print("p90  reproj error (stored) : %.4f px" % errs_sorted[int(len(errs) * 0.9)])
        tl = [len(p["track"]) for p in pts.values()]
        print("track length               : min %d max %d mean %.2f" %
              (min(tl), max(tl), sum(tl) / len(tl)))
        xs = [p["xyz"][0] for p in pts.values()]
        ys = [p["xyz"][1] for p in pts.values()]
        zs = [p["xyz"][2] for p in pts.values()]
        print("point bbox                 : x[%.2f,%.2f] y[%.2f,%.2f] z[%.2f,%.2f]" %
              (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))

    if args.verbose:
        for im in sorted(imgs.values(), key=lambda x: x["name"]):
            print("  %s id=%d cam=%d npts=%d" % (im["name"], im["id"], im["camera_id"],
                                                 len(im["xys"])))

    ok = missing == 0 and len(cams) >= 1 and len(imgs) >= 2 and len(pts) >= 10
    print("VERDICT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
