"""通过 -shell-serial 暴露的 guest root shell，把宿主 adb 公钥写进 /data/misc/adb/adb_keys。

用途：google_apis_playstore 是 user 版，强制 USB 调试授权；headless 起机没有对话框可点，
-skip-adb-auth 在本次实测不生效，于是换这条路。
"""
import os
import socket
import sys
import time


HOST = "127.0.0.1"
PORT = 5557
KEY = os.path.join(os.path.expanduser("~"), ".android", "adbkey.pub")


def conn():
    for _ in range(60):
        try:
            s = socket.create_connection((HOST, PORT), timeout=5)
            return s
        except Exception:
            time.sleep(5)
    return None


def main():
    if not os.path.exists(KEY):
        print("no adbkey.pub:", KEY)
        return 1
    pub = open(KEY, "r", encoding="utf-8").read().split()[0]
    print("pubkey len:", len(pub))

    s = conn()
    if s is None:
        print("cannot connect to shell serial")
        return 2
    s.settimeout(3)
    print("[connected]")

    def rd(t=3.0):
        buf = b""
        end = time.time() + t
        while time.time() < end:
            try:
                d = s.recv(65536)
            except socket.timeout:
                break
            except Exception:
                break
            if not d:
                break
            buf += d
        return buf.decode("utf-8", "replace")

    def w(cmd):
        s.sendall((cmd + "\n").encode("utf-8"))
        time.sleep(1.5)
        return rd(3.0)

    print("--- banner:")
    print(rd(5.0)[:2000])

    print("--- id:")
    print(w("id"))
    print("--- whoami:")
    print(w("whoami"))

    cmds = [
        "mkdir -p /data/misc/adb",
        "echo '%s' > /data/misc/adb/adb_keys" % pub,
        "chmod 600 /data/misc/adb/adb_keys",
        "chown system:system /data/misc/adb/adb_keys",
        "ls -l /data/misc/adb/",
        "cat /data/misc/adb/adb_keys | head -c 40",
        "setprop ctl.restart adbd",
    ]
    for c in cmds:
        out = w(c)
        print("$ %s\n%s" % (c, out.strip()[:400]))

    print("--- verify:")
    print(w("wc -c /data/misc/adb/adb_keys"))
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
