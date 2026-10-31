"""把签好名的 APK 落到团队约定的文件名。

两份：
  dist/dian-di-gao-si-v0.1-<abiShort>.apk   （team-lead 指定的交付名，x86_64 / arm64）
  dist/点点高斯-v0.1-<abi>.apk               （中文名，PowerShell 5.1 直写会乱码，交给 Python 走 UTF-8）
"""
import os
import shutil
import sys

src = sys.argv[1]
abi = sys.argv[2]
abi_short = "arm64" if abi.startswith("arm64") else abi
dist = os.path.dirname(src)

targets = [
    os.path.join(dist, "dian-di-gao-si-v0.1-" + abi_short + ".apk"),
    os.path.join(dist, "点点高斯-v0.1-" + abi + ".apk"),
]

# 第一个目标用 rename（省一次 80MB 拷贝），其余用硬链接/拷贝
first = True
for dst in targets:
    if os.path.exists(dst):
        os.remove(dst)
    if first:
        os.rename(src, dst)
        first = False
    else:
        try:
            os.link(targets[0], dst)
        except Exception:
            shutil.copyfile(targets[0], dst)
    print("apk -> " + dst + "  " + str(os.path.getsize(dst)) + " bytes")
