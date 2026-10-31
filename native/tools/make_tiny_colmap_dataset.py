#!/usr/bin/env python3
"""Generate a minimal COLMAP (text) dataset used to smoke test brush-headless.

No third party dependencies: PNGs are written by hand with zlib.

    python make_tiny_colmap_dataset.py <out_dir> [--views N] [--size S]

Layout produced:

    <out_dir>/images/*.png
    <out_dir>/sparse/0/cameras.txt
    <out_dir>/sparse/0/images.txt
"""

import argparse
import math
import os
import struct
import zlib


def png_bytes(width: int, height: int, pixel_fn) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            r, g, b = pixel_fn(x, y)
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def scene_pixel(cx: float, cy: float):
    """A crude scene: colored blobs on a gradient background."""

    def fn(x: int, y: int):
        u = x / cx
        v = y / cy
        r = int(40 + 150 * u)
        g = int(40 + 150 * v)
        b = 120
        # Two blobs so there is some parallax between views.
        for bx, by, col in ((0.35, 0.45, (230, 60, 60)), (0.65, 0.6, (60, 220, 90))):
            d = math.hypot(u - bx, v - by)
            if d < 0.18:
                r, g, b = col
        return r, g, b

    return fn


def look_at(eye, target, up=(0.0, 1.0, 0.0)):
    """COLMAP world-to-camera quaternion + translation.

    brush reads the camera's forward direction as the 3rd row of the rotation,
    so rows are (right, up, forward).
    """

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def norm(a):
        n = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        return (a[0] / n, a[1] / n, a[2] / n)

    forward = norm(sub(target, eye))
    right = norm(cross(up, forward))
    true_up = cross(forward, right)
    rows = (right, true_up, forward)

    # Rotation matrix -> quaternion (row major).
    m00, m01, m02 = rows[0]
    m10, m11, m12 = rows[1]
    m20, m21, m22 = rows[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (m21 - m12) / s
        qy = (m02 - m20) / s
        qz = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        qw = (m21 - m12) / s
        qx = 0.25 * s
        qy = (m01 + m10) / s
        qz = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        qw = (m02 - m20) / s
        qx = (m01 + m10) / s
        qy = 0.25 * s
        qz = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        qw = (m10 - m01) / s
        qx = (m02 + m20) / s
        qy = (m12 + m21) / s
        qz = 0.25 * s

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    # t = -R * eye
    tx = -dot(rows[0], eye)
    ty = -dot(rows[1], eye)
    tz = -dot(rows[2], eye)
    return (qw, qx, qy, qz), (tx, ty, tz)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--views", type=int, default=6)
    ap.add_argument("--size", type=int, default=128)
    args = ap.parse_args()

    size = args.size
    images_dir = os.path.join(args.out_dir, "images")
    sparse_dir = os.path.join(args.out_dir, "sparse", "0")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(sparse_dir, exist_ok=True)

    focal = float(size)

    with open(os.path.join(sparse_dir, "cameras.txt"), "w", newline="\n") as f:
        f.write("# Camera list with one line of data per camera:\n")
        f.write("#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        f.write(f"1 PINHOLE {size} {size} {focal} {focal} {size / 2} {size / 2}\n")

    lines = [
        "# Image list with two lines of data per image:",
        "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME",
        "#   POINTS2D[] as (X, Y, POINT3D_ID)",
    ]

    radius = 3.0
    for i in range(args.views):
        angle = 2.0 * math.pi * i / args.views
        # Cameras on a circle looking at the origin. The height varies so the
        # scene bounding box is not degenerate in Y (brush panics on an empty
        # random-sampling range otherwise).
        pos = (
            radius * math.sin(angle),
            0.8 * math.sin(angle * 2.0),
            radius * math.cos(angle),
        )
        (qw, qx, qy, qz), (tx, ty, tz) = look_at(pos, (0.0, 0.0, 0.0))

        name = f"view_{i:03d}.png"
        with open(os.path.join(images_dir, name), "wb") as f:
            f.write(png_bytes(size, size, scene_pixel(size, size)))

        lines.append(
            f"{i + 1} {qw:.6f} {qx:.6f} {qy:.6f} {qz:.6f} "
            f"{tx:.6f} {ty:.6f} {tz:.6f} 1 {name}"
        )
        lines.append("")  # no 2D points

    with open(os.path.join(sparse_dir, "images.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print(f"wrote {args.views} views to {args.out_dir}")


if __name__ == "__main__":
    main()
