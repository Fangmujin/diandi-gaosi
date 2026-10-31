"""plyFraming 的回归测试：属性顺序 / SH degree 变化不能影响相机框选结果。

背景：x/y/z 落在 header 末尾只是 brush 当前导出顺序，不是 PLY 规范保证的。
如果实现里硬编码偏移，SH degree 从 1（f_rest 9 个）变到 2（24 个）就会读到错的数据，
而且不报错、只是悄悄框错地方——比崩溃更难查。

用法：python build/test_ply_framing.py [some.ply]
默认用 native/testdata/brush_real_splat.ply。
"""
import math
import os
import struct
import sys

TS = {'char': 1, 'uchar': 1, 'int8': 1, 'uint8': 1,
      'short': 2, 'ushort': 2, 'int16': 2, 'uint16': 2,
      'int': 4, 'uint': 4, 'int32': 4, 'uint32': 4,
      'float': 4, 'float32': 4, 'double': 8, 'float64': 8}


def parse(buf):
    """与 app/assets/js/viewer.js 的 plyFraming() 同算法。"""
    head = buf[:65536].decode('ascii', 'replace')
    end = head.index('end_header')
    data_off = head.index('\n', end) + 1
    props = []
    count = 0
    in_vertex = False
    for ln in head[:end].split('\n'):
        ln = ln.strip()
        if ln.startswith('element'):
            in_vertex = ln.split()[1] == 'vertex'
            if in_vertex:
                count = int(ln.split()[2])
            continue
        if not in_vertex or not ln.startswith('property'):
            continue
        f = ln.split()
        props.append((f[2], TS.get(f[1], 0)))
    names = [p[0] for p in props]
    sizes = [p[1] for p in props]
    stride = sum(sizes)
    off = {}
    acc = 0
    for n, s in zip(names, sizes):
        off[n] = acc
        acc += s
    return count, stride, data_off, names, sizes, off


def framing(buf):
    count, stride, data_off, names, sizes, off = parse(buf)
    for need in ('x', 'y', 'z'):
        if need not in off:
            return None
    step = max(1, count // 20000)
    xs, ys, zs = [], [], []
    for k in range(0, count, step):
        b = data_off + k * stride
        if b + stride > len(buf):
            break
        xs.append(struct.unpack_from('<f', buf, b + off['x'])[0])
        ys.append(struct.unpack_from('<f', buf, b + off['y'])[0])
        zs.append(struct.unpack_from('<f', buf, b + off['z'])[0])
    n = len(xs)
    cx, cy, cz = sum(xs) / n, sum(ys) / n, sum(zs) / n
    hs, vs, d3 = [], [], []
    for i in range(n):
        dx, dy, dz = xs[i] - cx, ys[i] - cy, zs[i] - cz
        hs.append(math.sqrt(dx * dx + dz * dz))
        vs.append(abs(dy))
        d3.append(math.sqrt(dx * dx + dy * dy + dz * dz))

    def pct(a):
        a.sort()
        return a[int(n * 0.95)]
    return (round(cx, 3), round(cy, 3), round(cz, 3),
            round(pct(hs), 3), round(pct(vs), 3), round(pct(d3), 3))


def fit_distance(rH, rV, fov=45.0, aspect=1.0, fill=0.9):
    """与 app/assets/js/viewer.js 的 fitDistance() 同算法。"""
    tY = math.tan(math.radians(fov) / 2)
    tX = tY * aspect
    return max(rH / (fill * tX), rV / (fill * tY))


def rewrite(src, out, order, n_rest):
    """按指定属性顺序 / f_rest 数量重写一份 PLY（用于验证解析不受顺序影响）。"""
    buf = open(src, 'rb').read()
    count, stride, data_off, names, sizes, off = parse(buf)
    outs = []
    for k in range(count):
        b = data_off + k * stride
        vals = {}
        for nm, sz in zip(names, sizes):
            vals[nm] = struct.unpack_from('<f', buf, b + off[nm])[0]
        row = []
        for nm in order:
            if nm == 'x' or nm == 'y' or nm == 'z':
                row.append(vals[nm])
            elif nm.startswith('f_rest_'):
                row.append(vals.get(nm, 0.0))
            else:
                row.append(vals.get(nm, 0.0))
        outs.extend(row)
    lines = ['ply', 'format binary_little_endian 1.0']
    lines += ['element vertex %d' % count]
    for nm in order:
        lines.append('property float ' + nm)
    lines.append('end_header')
    head = ('\n'.join(lines) + '\n').encode('ascii')
    with open(out, 'wb') as f:
        f.write(head)
        f.write(struct.pack('<%df' % len(outs), *outs))
    return out


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(root, 'native', 'testdata', 'brush_real_splat.ply')
    if not os.path.exists(src):
        print('missing', src)
        return 1
    buf = open(src, 'rb').read()

    base = framing(buf)
    print('原始                    质心 + rH/rV/r3D:', base)

    count, stride, data_off, names, sizes, off = parse(buf)
    print('  header: count=%d stride=%d x@%d y@%d z@%d'
          % (count, stride, off['x'], off['y'], off['z']))

    print('  ---- 按 FOV 分别算水平/竖直距离，取 max（fill=0.9，fov=45°）----')
    for tag, aspect in (('640x1136 素材原生', 640 / 1136),
                        ('411x914 app canvas', 411 / 914),
                        ('横屏 16:9', 16 / 9)):
        d = fit_distance(base[3], base[4], 45.0, aspect)
        # 主体在该距离下占画面的比例（1.0 = 刚好贴边）
        tY = math.tan(math.radians(45.0) / 2)
        fracW = (base[3] / d) / (tY * aspect)
        fracH = (base[4] / d) / tY
        print('    %-20s aspect=%.3f  dist=%.2f  占宽 %.0f%%  占高 %.0f%%'
              % (tag, aspect, d, fracW * 100, fracH * 100))

    # 变体 1：x/y/z 放最前（3DGS 官方 ply 的常规写法）
    order1 = ['x', 'y', 'z'] + [n for n in names if n not in ('x', 'y', 'z')]
    # 变体 2：x/y/z 放最前 + SH degree 2（f_rest 0..23）
    rest2 = ['f_rest_%d' % i for i in range(24)]
    order2 = ['x', 'y', 'z'] + [n for n in names
                                if n not in ('x', 'y', 'z') and not n.startswith('f_rest_')] + rest2

    ok = True
    for tag, order, expect_stride in (
            ('x/y/z 置顶(同 degree)', order1, stride),
            ('x/y/z 置顶 + SH degree 2', order2, stride + 15 * 4)):
        tmp = os.path.join(root, 'build', '_framing_case.ply')
        rewrite(src, tmp, order, len([n for n in order if n.startswith('f_rest_')]))
        got = framing(open(tmp, 'rb').read())
        c, s, dof, nm, sz, of = parse(open(tmp, 'rb').read())
        same = got[:5] == base[:5]
        print('%-26s 质心 + rH/rV/r3D: %s  stride=%d (期望 %d)  %s'
              % (tag, got, s, expect_stride, 'OK' if same else 'MISMATCH'))
        ok = ok and same
        os.remove(tmp)

    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
