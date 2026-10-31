# 临时：重打包后的交付核验（.so sha256 / native-code / dex 参数串 / viewer.js 一致性 / apksigner）
import hashlib, os, subprocess, sys, zipfile, re

ROOT = r"C:\Users\28928\WorkBuddy\2026-10-11-15-32-41\diandi-gaosi"
DIST = os.path.join(ROOT, "dist")
BT = r"C:\Users\28928\AppData\Local\Android\Sdk\build-tools\36.1.0"
SRC_VIEWER = os.path.join(ROOT, "app", "assets", "js", "viewer.js")

TARGETS = [
    ("arm64-v8a", "点点高斯-v0.1-arm64-v8a.apk"),
    ("x86_64", "点点高斯-v0.1-x86_64.apk"),
]

def sha(b):
    return hashlib.sha256(b).hexdigest()

src_viewer_sha = sha(open(SRC_VIEWER, "rb").read())
print("源码 app/assets/js/viewer.js  sha256 =", src_viewer_sha, " size =", os.path.getsize(SRC_VIEWER))
print()

for abi, name in TARGETS:
    p = os.path.join(DIST, name)
    print("=" * 70)
    print("APK:", name, " size =", os.path.getsize(p))
    z = zipfile.ZipFile(p)
    sos = [i for i in z.infolist() if i.filename.endswith(".so")]
    print("  native-code (jniLibs):")
    for i in sos:
        print("    %-46s %12d  %s  compress_type=%d  external_attr=0o%o"
              % (i.filename, i.file_size, sha(z.read(i.filename)), i.compress_type, i.external_attr >> 16))
    abis = sorted({i.filename.split("/")[1] for i in sos})
    print("  -> ABI 集合:", abis, "单一 ABI:", len(abis) == 1 and abis[0] == abi)
    vj = [i for i in z.infolist() if i.filename.endswith("assets/js/viewer.js")]
    if vj:
        b = z.read(vj[0].filename)
        print("  assets/js/viewer.js in-APK: size =", len(b), " sha256 =", sha(b))
        print("  -> 与源码一致:", sha(b) == src_viewer_sha)
    else:
        print("  !! assets/js/viewer.js 缺失")
    dex = [i for i in z.infolist() if i.filename.endswith(".dex")]
    hits = {}
    for key in ("scale-loss-weight", "aux-loss-time", "opac-loss-weight"):
        cnt = 0
        for d in dex:
            cnt += z.read(d.filename).count(key.encode("ascii"))
        hits[key] = cnt
    print("  dex 参数串检出:", hits, " (期望 scale/aux 各 2、opac 0)")
    # apksigner
    r = subprocess.run([os.path.join(BT, "apksigner.bat"), "verify", "--print-certs", "-v", p],
                       capture_output=True, text=True, shell=True)
    txt = r.stdout + r.stderr
    for line in txt.splitlines():
        if "Verifies" in line or "scheme (APK Signature Scheme v" in line or "Number of signers" in line:
            print("  apksigner:", line.strip())
    if "Verifies" not in txt:
        print("  !! apksigner 未输出 Verifies；原始输出：", txt[:400])
print()
print("DONE")
