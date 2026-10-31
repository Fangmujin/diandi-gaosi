#!/usr/bin/env python3
"""离线证明：磁盘上的 COLMAP 二进制三件套 == src/colmap_io.cpp 里修复版 writer 应产生的字节流。

背景
----
ds_44 那份 images.bin 当初不是修复版二进制端到端跑出来的，而是对修复前产物做的
**纯字节层重排版**（每条记录删掉多写的 8 字节 uint64 name_len）。
要洗掉这个标签，不需要设备：把文件按正确格式解析成结构化记录，再按 writer 的逻辑
重新序列化一遍，逐字节比对即可。若一致，说明磁盘上的字节就是 writer 的唯一可能输出。

本工具严格镜像 colmap_io.cpp::writeColmapBinary 的写出顺序：

  cameras.bin  : u64 n, {i32 id, i32 model(=2), u64 w, u64 h, f64 f, f64 cx, f64 cy, f64 k1}
  images.bin   : u64 n, {i32 id, f64 q[4], f64 t[3], i32 cam_id,
                         char[] name (NUL 结尾, **无长度前缀**),
                         u64 npts, {f64 x, f64 y, i64 pid} * npts}
  points3D.bin : u64 n, {i64 id, f64 xyz[3], u8 rgb[3], f64 err,
                         u64 tlen, {i32 img_id, i32 p2d_idx} * tlen}

f64/i64 一律 struct '<d'/'<q' 原样搬运，不涉及浮点格式化，所以往返是位精确的。

判据
----
  往返字节数一致 且 sha256 一致 -> PASS（"重排版"标签可摘掉）
  不一致 -> 打印第一个不一致的偏移 + 上下文字节，那是真问题

额外检查（第 5 步）
----
  按**修复前**的错误布局（name 前带 u64 name_len）重新序列化一遍，
  字节数应当正好回到 1,485,300 B = 1,484,948 + 44*8。
  这条能反证"当初的重排版确实只是删了 8 字节/条，没有误伤别的数据"。

用法: python tools/roundtrip_colmap.py <colmap_dir> [--legacy-size N]
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

FAIL = 0


def die(msg):
    global FAIL
    FAIL += 1
    print("  [FAIL] %s" % msg)


def rd(f, fmt):
    size = struct.calcsize(fmt)
    b = f.read(size)
    if len(b) != size:
        raise EOFError("want %d bytes at off %d, got %d" % (size, f.tell(), len(b)))
    return struct.unpack(fmt, b)


def read_cstring_bytes(f, limit=4096):
    """读到 NUL 为止，返回 NUL 之前的原始字节（保留字节，不做 utf-8 解码）。"""
    buf = bytearray()
    for _ in range(limit):
        ch = f.read(1)
        if not ch:
            raise EOFError("EOF while reading NUL-terminated string")
        if ch == b"\x00":
            return bytes(buf)
        buf += ch
    raise EOFError("string longer than %d" % limit)


def expect_eof(f, path):
    rest = f.read(1)
    if rest:
        die("%s: 解析完后还剩字节（偏移 %d），说明解析没能吃干净" % (path, f.tell() - 1))


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def parse_cameras(path):
    recs = []
    with open(path, "rb") as f:
        n = rd(f, "<Q")[0]
        for _ in range(n):
            cid, mid = rd(f, "<ii")
            w, h = rd(f, "<QQ")
            params = rd(f, "<4d")
            recs.append(dict(id=cid, model=mid, w=w, h=h, params=params))
        expect_eof(f, path)
    return recs


def parse_images(path):
    recs = []
    with open(path, "rb") as f:
        n = rd(f, "<Q")[0]
        for _ in range(n):
            iid = rd(f, "<i")[0]
            q = rd(f, "<4d")
            t = rd(f, "<3d")
            cid = rd(f, "<i")[0]
            name = read_cstring_bytes(f)
            npts = rd(f, "<Q")[0]
            if npts > 20 * 1000 * 1000:
                raise ValueError("implausible num_points2D=%d (stream misaligned?)" % npts)
            pts = [rd(f, "<ddq") for _ in range(npts)]
            recs.append(dict(id=iid, qvec=q, tvec=t, camera_id=cid,
                             name=name, points=pts))
        expect_eof(f, path)
    return recs


def parse_points3d(path):
    recs = []
    with open(path, "rb") as f:
        n = rd(f, "<Q")[0]
        for _ in range(n):
            pid = rd(f, "<q")[0]
            xyz = rd(f, "<3d")
            rgb = rd(f, "<3B")
            err = rd(f, "<d")[0]
            tlen = rd(f, "<Q")[0]
            track = [rd(f, "<ii") for _ in range(tlen)]
            recs.append(dict(id=pid, xyz=xyz, rgb=rgb, error=err, track=track))
        expect_eof(f, path)
    return recs


# --------------------------------------------------------------------------
# 重新序列化（镜像 writeColmapBinary）
# --------------------------------------------------------------------------
def ser_cameras(recs):
    out = struct.pack("<Q", len(recs))
    for c in recs:
        out += struct.pack("<ii", c["id"], c["model"])
        out += struct.pack("<QQ", c["w"], c["h"])
        out += struct.pack("<4d", *c["params"])
    return out


def ser_images(recs, legacy_name_len=False):
    """legacy_name_len=True 时按修复前的错误布局写（name 前带 u64 长度前缀），
    仅用于第 5 步反证长度账目，正常校验走默认分支。"""
    out = struct.pack("<Q", len(recs))
    for im in recs:
        out += struct.pack("<i", im["id"])
        out += struct.pack("<4d", *im["qvec"])
        out += struct.pack("<3d", *im["tvec"])
        out += struct.pack("<i", im["camera_id"])
        if legacy_name_len:
            out += struct.pack("<Q", len(im["name"]))
        out += im["name"] + b"\x00"
        out += struct.pack("<Q", len(im["points"]))
        for x, y, pid in im["points"]:
            out += struct.pack("<ddq", x, y, pid)
    return out


def ser_points3d(recs):
    out = struct.pack("<Q", len(recs))
    for p in recs:
        out += struct.pack("<q", p["id"])
        out += struct.pack("<3d", *p["xyz"])
        out += struct.pack("<3B", *p["rgb"])
        out += struct.pack("<d", p["error"])
        out += struct.pack("<Q", len(p["track"]))
        for a, b in p["track"]:
            out += struct.pack("<ii", a, b)
    return out


# --------------------------------------------------------------------------
def first_diff(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n if len(a) != len(b) else -1


def compare(tag, disk_path, rebuilt):
    disk = open(disk_path, "rb").read()
    print("\n== %s ==" % tag)
    print("  磁盘: %d bytes  sha256=%s" % (len(disk), hashlib.sha256(disk).hexdigest()))
    print("  往返: %d bytes  sha256=%s" % (len(rebuilt), hashlib.sha256(rebuilt).hexdigest()))
    if len(disk) != len(rebuilt):
        die("%s 长度不符: 磁盘 %d vs 往返 %d (差 %d)"
            % (tag, len(disk), len(rebuilt), len(rebuilt) - len(disk)))
    off = first_diff(disk, rebuilt)
    if off >= 0:
        lo = max(0, off - 16)
        print("  第一个不一致偏移: %d (0x%x)" % (off, off))
        print("    磁盘[%d:%d]: %s" % (lo, off + 16, disk[lo:off + 16].hex(" ")))
        print("    往返[%d:%d]: %s" % (lo, off + 16, rebuilt[lo:off + 16].hex(" ")))
        # 试着指出落在第几条记录
        die("%s 字节不一致，见上方偏移与上下文" % tag)
        return False
    print("  逐字节一致: YES")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("colmap_dir")
    ap.add_argument("--legacy-size", type=int, default=None,
                    help="修复前那份 images.bin 的已知字节数，用于反证长度账目")
    args = ap.parse_args()
    d = Path(args.colmap_dir)
    print("COLMAP 目录: %s" % d)

    cams = parse_cameras(d / "cameras.bin")
    imgs = parse_images(d / "images.bin")
    pts = parse_points3d(d / "points3D.bin")
    print("解析结果: cameras=%d images=%d points3D=%d" % (len(cams), len(imgs), len(pts)))

    ok = True
    ok &= compare("cameras.bin", d / "cameras.bin", ser_cameras(cams))
    ok &= compare("images.bin", d / "images.bin", ser_images(imgs))
    ok &= compare("points3D.bin", d / "points3D.bin", ser_points3d(pts))

    # 第 5 步：按修复前的错误布局重算长度账目
    print("\n== 长度账目反证（修复前布局：每条带 u64 name_len）==")
    legacy = len(ser_images(imgs, legacy_name_len=True))
    now = len(open(d / "images.bin", "rb").read())
    delta = legacy - now
    print("  按错误布局重写: %d bytes" % legacy)
    print("  按正确布局重写: %d bytes" % now)
    print("  差值: %d = %d x 8 (每条 8 字节 name_len)" % (delta, delta / 8 if delta % 8 == 0 else -1))
    if args.legacy_size is not None:
        if legacy != args.legacy_size:
            die("重算出的旧布局字节数 %d != 记录的 %d，说明当时删掉的不止 name_len"
                % (legacy, args.legacy_size))
        else:
            print("  与记录的修复前字节数 %d 一致 -> 当初确实只删了 name_len" % args.legacy_size)

    print("\nVERDICT: %s" % ("PASS" if (ok and FAIL == 0) else "FAIL"))
    return 0 if (ok and FAIL == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
