"""把 classes.dex、jniLibs（lib/<abi>/*.so）注入 base.apk，并补齐 zip 目录条目。

两个历史坑：
1) aapt2 不写 assets 子目录的 zip 目录条目，导致 WebView 读 assets/js/... 时 404 -> 必须补 add_dir
2) 原生引擎 .so 必须以 STORED（不压缩）方式放进 lib/<abi>/，否则系统不解压、无法 exec
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
PREBUILT = os.path.join(ROOT, "native", "prebuilt")

# 按 ABI 分包：build.ps1 通过 $env:DIANDI_ABI 指定本次组装哪个 ABI。
# 历史教训：x86_64 只有 trainer、arm64 只有 SfM，若两个 ABI 的 .so 混进同一个包，
# 安装后 libDir 里会出现"能 exec 的没有 SfM / 有 SfM 的不能 exec"的错位。
ABI = (os.environ.get("DIANDI_ABI") or "").strip()
KNOWN_ENGINES = ("libiii_sfm.so", "libbrushheadless.so")
SO_WARN_MB = 64  # 超过该体积只告警不失败（trainer 未 strip 时约 78MB）

src = os.path.join(BUILD, "base.apk")
dst = os.path.join(BUILD, "withdex.apk")
dex = os.path.join(BUILD, "dex", "classes.dex")

if not os.path.exists(src):
    print("missing base.apk")
    sys.exit(1)
if not os.path.exists(dex):
    print("missing classes.dex")
    sys.exit(1)

zin = zipfile.ZipFile(src, "r")
names = zin.namelist()
zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)

for n in names:
    info = zin.getinfo(n)
    data = zin.read(n)
    zout.writestr(info, data)
zin.close()

# 1) dex
with open(dex, "rb") as f:
    zout.writestr(zipfile.ZipInfo("classes.dex"), f.read(), compress_type=zipfile.ZIP_DEFLATED)

# 2) jniLibs：native/prebuilt/<abi>/*.so -> lib/<abi>/*.so（STORED），只装当前 ABI
so_added = []
so_missing = []
if not ABI:
    print("WARN: DIANDI_ABI 未设置，回退为注入全部 ABI（不推荐）")
    abis = sorted(os.listdir(PREBUILT)) if os.path.isdir(PREBUILT) else []
else:
    abis = [ABI]

for abi in abis:
    d = os.path.join(PREBUILT, abi)
    if not os.path.isdir(d):
        print("WARN: 该 ABI 没有预编译目录，跳过: %s" % abi)
        so_missing.extend(KNOWN_ENGINES)
        continue
    present = sorted(f for f in os.listdir(d) if f.endswith(".so") and os.path.isfile(os.path.join(d, f)))
    for fn in present:
        full = os.path.join(d, fn)
        arc = "lib/%s/%s" % (abi, fn)
        mb = os.path.getsize(full) / 1048576.0
        if mb > SO_WARN_MB:
            # 非致命：体积异常多半是没 strip，只提醒不阻断构建
            print("WARN: %s 体积 %.1fMB 偏大（可能未 strip），仍会打包" % (fn, mb))
        zi = zipfile.ZipInfo(arc)
        zi.compress_type = zipfile.ZIP_STORED  # jniLibs 必须 STORED，否则系统不解压、无法 exec
        zi.external_attr = 0o755 << 16
        with open(full, "rb") as f:
            zout.writestr(zi, f.read())
        so_added.append(arc)
    for need in KNOWN_ENGINES:
        if need not in present:
            so_missing.append(need)
            print("NOTE: %s 在 %s 下缺失，本次不打进包（UI 需降级提示）" % (need, abi))

# 3) 补齐 zip 目录条目
seen = set(names)


def add_dir(name):
    if name in seen:
        return
    seen.add(name)
    zi = zipfile.ZipInfo(name)
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = 0o755 << 16
    zout.writestr(zi, b"")


for n in list(names):
    if n.startswith("assets/"):
        parts = n.split("/")
        for i in range(1, len(parts)):
            add_dir("/".join(parts[:i]) + "/")
for arc in so_added:
    parts = arc.split("/")
    for i in range(1, len(parts)):
        add_dir("/".join(parts[:i]) + "/")
add_dir("assets/")

zout.close()

zf = zipfile.ZipFile(dst)
final = zf.namelist()
print("apk:", dst)
print("entries:", len(final))
print("has assets/js/:", "assets/js/" in final)
print("has assets/runtime/:", "assets/runtime/" in final)
print("abi:", ABI or "(all)")
print("jniLibs:", [n for n in final if n.startswith("lib/") and n.endswith(".so")])
print("engines missing:", sorted(set(so_missing)) or "none")
print("size(MB): %.2f" % (os.path.getsize(dst) / 1048576.0))
