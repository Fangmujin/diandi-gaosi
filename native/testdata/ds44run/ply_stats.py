import struct, sys, math

path = sys.argv[1]
with open(path, 'rb') as f:
    data = f.read()

end = data.index(b'end_header') + len(b'end_header') + 1
header = data[:end].decode('ascii', 'replace').splitlines()
nverts = 0
props = []
for line in header:
    if line.startswith('element vertex'):
        nverts = int(line.split()[-1])
    elif line.startswith('property float'):
        props.append(line.split()[-1])

NF = len(props)
body = data[end:end + nverts * NF * 4]
ix, iy, iz = props.index('x'), props.index('y'), props.index('z')

xyz = []
for i in range(nverts):
    off = i * NF * 4
    x = struct.unpack_from('<f', body, off + ix * 4)[0]
    y = struct.unpack_from('<f', body, off + iy * 4)[0]
    z = struct.unpack_from('<f', body, off + iz * 4)[0]
    xyz.append((x, y, z))

cx = sum(p[0] for p in xyz) / nverts
cy = sum(p[1] for p in xyz) / nverts
cz = sum(p[2] for p in xyz) / nverts

radii = sorted(math.dist(p, (cx, cy, cz)) for p in xyz)


def pct(q):
    return radii[min(len(radii) - 1, int(len(radii) * q))]


bbox = (
    min(p[0] for p in xyz), max(p[0] for p in xyz),
    min(p[1] for p in xyz), max(p[1] for p in xyz),
    min(p[2] for p in xyz), max(p[2] for p in xyz),
)

print(f'file            : {path}')
print(f'vertices        : {nverts}')
print(f'centroid        : ({cx:.3f}, {cy:.3f}, {cz:.3f})')
print(f'radius p50      : {pct(0.50):.3f}')
print(f'radius p90      : {pct(0.90):.3f}')
print(f'radius p95      : {pct(0.95):.3f}')
print(f'radius p99      : {pct(0.99):.3f}')
print(f'radius max      : {radii[-1]:.3f}')
print(f'bbox            : x[{bbox[0]:.2f},{bbox[1]:.2f}] '
      f'y[{bbox[2]:.2f},{bbox[3]:.2f}] z[{bbox[4]:.2f},{bbox[5]:.2f}]')
for thr in (3.0, 5.0, 10.0):
    c = sum(1 for r in radii if r > thr)
    print(f'radius > {thr:<5} : {c} ({100.0 * c / nverts:.1f}%)')
