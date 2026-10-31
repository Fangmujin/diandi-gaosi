#!/usr/bin/env python3
"""把训练 PLY 从某个训练相机位姿光栅化渲染出来，与该相机的实拍原图并排对比。

为什么要有这个工具
------------------
"前景遮挡占屏 11.7%"这类数字**定不了**画面脏不脏 —— 1.5% 的 α>0.5 遮挡可能是
一个显眼的雾团，也可能是看不见的薄膜。而训练时引擎自己就在渲染这些相机，
loss 收敛说明引擎的渲染和实拍对得上。所以最可靠的裁决是：
**用同一份 PLY、同一个位姿，自己光栅化渲染一帧，与实拍原图并排。**

渲染公式逐条对照 `crates/brush-render/src/shaders/`：
- 协方差: helpers.wgsl:156 `cov3d = M Mᵀ, M = R(quat)·diag(scale)`
- 四元数: helpers.wgsl:113 `w=quat.x, x=quat.y, y=quat.z, z=quat.w`（rot_0=w）
- 投影:   helpers.wgsl:179 `cov2d = J·(R_cam cov3d R_camᵀ)·Jᵀ` + COV_BLUR
- 颜色:   SH 0 阶基元 C0 = 0.28209479177387814, RGB = 0.5 + C0·f_dc
- 合成:   从远到近 canvas = α·c + (1-α)·canvas（等价于前到后 T·α·c 累加）

注意：本渲染器没做 tile 排序细化 / 没有高阶 SH（只算 DC），
色调会比引擎略平，但**几何和透明度是准的** —— 足够判断有没有雾团。

用法: python render_compare.py <splat.ply> <colmap_dir> <图像目录> <frame_name> [输出png]
"""
import math
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sfm" / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_colmap import read_cameras, read_images, qvec_to_rotmat  # noqa: E402
from occlusion_index import COV_BLUR, load_ply, splat2d  # noqa: E402

C0 = 0.28209479177387814


def write_png(path, img):
    """img: (H,W,3) uint8。不压缩的极简 PNG 写出。"""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def render(mu, c2, d, rgb, inside, W, H, ds=1):
    """远到近 alpha 合成。返回 (H//ds, W//ds, 3) float 0..1"""
    W4, H4 = W // ds, H // ds
    ys, xs = np.mgrid[0:H4, 0:W4]
    px = xs * ds + (ds - 1) / 2.0
    py = ys * ds + (ds - 1) / 2.0
    canvas = np.zeros((H4, W4, 3))
    order = np.argsort(-d)                     # 远 -> 近
    for i in order:
        if not inside[i] or d[i] <= 0:
            continue
        s = c2[i]
        det = max(s[0, 0] * s[1, 1] - s[0, 1] ** 2, 1e-12)
        inv = np.array([[s[1, 1], -s[0, 1]], [-s[0, 1], s[0, 0]]]) / det
        # inv 的特征值是 1/σ²，所以 σ_max = 1/sqrt(min(inv))。窗口半宽取 3σ_max。
        hw = min(int(3.0 / math.sqrt(max(min(inv[0, 0], inv[1, 1]), 1e-12)) / ds) + 2, 300)
        x0, x1 = max(int(mu[i, 0] / ds) - hw, 0), min(int(mu[i, 0] / ds) + hw + 1, W4)
        y0, y1 = max(int(mu[i, 1] / ds) - hw, 0), min(int(mu[i, 1] / ds) + hw + 1, H4)
        if x0 >= x1 or y0 >= y1:
            continue
        dx = px[y0:y1, x0:x1] - mu[i, 0]
        dy = py[y0:y1, x0:x1] - mu[i, 1]
        m2 = dx * dx * inv[0, 0] + 2 * dx * dy * inv[0, 1] + dy * dy * inv[1, 1]
        ai = np.exp(-0.5 * m2)
        blk = canvas[y0:y1, x0:x1]
        canvas[y0:y1, x0:x1] = ai[..., None] * rgb[i] + (1.0 - ai[..., None]) * blk
    return canvas


def main():
    ply, cdir, imgdir, frame = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    out = sys.argv[5] if len(sys.argv) > 5 else "render_compare.png"
    cdir, imgdir = Path(cdir), Path(imgdir)
    from tail_crosstab import read_ply_all
    nv, cols = read_ply_all(ply)
    xyz = np.stack([np.asarray(cols[k]) for k in "xyz"], axis=1)
    sig = np.stack([np.exp(np.asarray(cols["scale_%d" % k])) for k in range(3)], axis=1)
    quat = np.stack([np.asarray(cols["rot_%d" % k]) for k in range(4)], axis=1)
    alpha = 1.0 / (1.0 + np.exp(-np.asarray(cols["opacity"])))
    fdc = np.stack([np.asarray(cols["f_dc_%d" % k]) for k in range(3)], axis=1)
    rgb = np.clip(0.5 + C0 * fdc, 0, 1)
    cams = read_cameras(cdir / "cameras.bin")
    imgs = read_images(cdir / "images.bin")
    im = next(v for v in imgs.values() if v["name"] == frame)
    cam = cams[im["camera_id"]]
    f, cx, cy = cam["params"][0], cam["params"][1], cam["params"][2]
    W, H = cam["width"], cam["height"]
    Rc = np.asarray(qvec_to_rotmat(im["qvec"]))
    t = np.asarray(im["tvec"])
    mu, c2, d, inside = splat2d(xyz, sig, quat, Rc, t, f, cx, cy)
    ds = 2 if H >= 1000 else 1
    img = render(mu, c2, d, rgb, inside, W, H, ds)
    print("渲染 %s: %dx%d (降采样 %d), splats %d, 视锥内 %d"
          % (frame, W // ds, H // ds, ds, nv, int(inside.sum())))

    # 实拍原图, 降采样到与渲染同尺寸
    photo = read_jpeg_downsample(imgdir / frame, W // ds, H // ds)
    side = np.concatenate([img, photo], axis=1)
    write_png(out, (np.clip(side, 0, 1) * 255).astype(np.uint8))
    print("已写出 %s  (左=PLY渲染, 右=实拍原图)" % out)


def read_jpeg_downsample(path, tw, th):
    """不引 PIL: 用 ffmpeg 解码+缩放到内存, 再解析 PPM。"""
    import subprocess
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
           "scale=%d:%d" % (tw, th), "-frames:v", "1", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(th, tw, 3)
    return a.astype(np.float64) / 255.0


if __name__ == "__main__":
    sys.exit(main())
