"""端到端截图补抓：运行中的进度页 / 失败页 / 图库 / 查看页。

相比 e2e_run.py 的改进：CDP 连接断开会自动重连，eval 超时不会卡死整个流程。
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = "C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi"
ADB = "C:/Users/28928/AppData/Local/Android/Sdk/platform-tools/adb.exe"
SHOTS = os.path.join(ROOT, "docs", "artifacts", "shots")
PKG = "com.diandi.gaosi"

sys.path.insert(0, os.path.join(ROOT, "build"))
import viewer_check as vc


def sh(args, retry=4):
    last = ""
    for _ in range(retry):
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0 and "offline" not in out and "no devices" not in out:
            return out.strip()
        last = out.strip()
        time.sleep(2)
    return last


def shell(cmd, retry=4):
    return sh([ADB, "shell", cmd], retry)


def shot(name):
    # 不要去点 "System UI isn't responding" 的 "Wait"：点下去会触发 System UI 重启，
    # 之后 screencap 拿到的全是黑屏（实测踩过一次，把 11/12 两张好图覆盖成了 15KB 的黑图）。
    # 宁可让 ANR 弹窗留在画面里，也不要黑屏。
    remote = "/sdcard/" + name
    shell("screencap -p " + remote)
    time.sleep(0.5)
    dst = os.path.join(SHOTS, name)
    sh([ADB, "pull", remote, dst])
    print("[shot] " + name + "  " + str(os.path.getsize(dst) if os.path.exists(dst) else 0))
    return dst


class Cdp(object):
    def __init__(self):
        self.ws = None
        self.mid = 0

    def connect(self):
        out = shell("ps -A")
        # adb 输出是 CRLF，别用 $ 锚定结尾，直接按整行过滤再取第 2 列
        pid = ""
        for ln in out.replace("\r", "").splitlines():
            if ln.rstrip().endswith(PKG):
                f = ln.split()
                if len(f) >= 2:
                    pid = f[1]
                    break
        if not pid:
            return False
        sh([ADB, "forward", "tcp:9222", "localabstract:webview_devtools_remote_" + pid])
        time.sleep(1)
        try:
            pages = vc.http_json("/json/list")
            page = [p for p in pages if p.get("type") == "page"][0]
            self.ws = vc.ws_connect(page["webSocketDebuggerUrl"])
            self.ws.settimeout(0.5)
            self.mid = 0
            self.call("Runtime.enable")
            return True
        except Exception as e:
            print("cdp connect failed:", e)
            self.ws = None
            return False

    def call(self, method, params=None):
        self.mid += 1
        vc.send(self.ws, {"id": self.mid, "method": method, "params": params or {}})
        return self.mid

    def ev(self, expr, timeout=12):
        for attempt in range(3):
            if self.ws is None and not self.connect():
                time.sleep(2)
                continue
            try:
                i = self.call("Runtime.evaluate",
                              {"expression": expr, "returnByValue": True, "awaitPromise": True})
                t = time.time() + timeout
                while time.time() < t:
                    try:
                        opcode, payload = vc.recv(self.ws)
                    except Exception:
                        continue
                    if opcode != 1:
                        continue
                    try:
                        m = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    if m.get("id") == i:
                        return m.get("result", {}).get("result", {}).get("value")
            except Exception as e:
                print("cdp eval error:", e)
                self.ws = None
            time.sleep(1)
        return None


def main():
    # adb daemon 在每条命令结束时会一起被回收，所以整个流程必须在同一条命令/任务里跑完，
    # 否则每次 adb 调用都要重启 daemon，第一次握手常常是 "device offline"。
    shell("am force-stop %s" % PKG)
    time.sleep(2)
    shell("am start -n %s/.MainActivity" % PKG)
    time.sleep(6)
    c = Cdp()
    for _ in range(20):
        if c.connect():
            break
        print("[wait] cdp not ready, retry")
        time.sleep(4)
    if c.ws is None:
        print("no cdp")
        return
    for _ in range(40):
        v = c.ev("JSON.stringify({app:typeof App,rs:document.readyState})", 8)
        if v and '"app":"object"' in v:
            print("[ready] " + v)
            break
        time.sleep(2)

    # 导入页
    c.ev("App.state.media=[{rel:'54734.mp4',name:'54734.mp4',kind:'video',"
         "bytes:3600201,durationMs:8700}];App.state.tab='new';App.render();'ok'", 10)
    time.sleep(3)
    shot("10_e2e_import.png")

    # 启动
    c.ev("App.startJob({name:'端到端 54734',quality:'fast'});'ok'", 15)
    time.sleep(5)
    shot("11_e2e_running_start.png")

    names = ["抽帧", "提特征", "匹配", "重建", "训练", "导出"]
    last = ""
    done = {"s0", "s1", "s4"}
    t_end = time.time() + 900
    while time.time() < t_end:
        raw = c.ev("Native.jobProgress()", 12)
        # Native.jobProgress() 返回的是对象不是字符串（native.js 里已经 JSON.parse 过），
        # CDP returnByValue 会直接给 dict；两种形态都要能吃下
        d = None
        if isinstance(raw, dict):
            d = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                d = json.loads(raw)
            except Exception:
                d = None
        if not d:
            time.sleep(3)
            continue
        st = d.get("state", "")
        idx = d.get("stageIndex", 0)
        ov = int(round((d.get("overall") or 0) * 100))
        line = "%s %d%% %s" % (st, ov, names[idx] if 0 <= idx < 6 else "?")
        if line != last:
            print("[prog] " + line)
            last = line
        if st == "running":
            if idx == 0:
                shot("11_e2e_running_extract.png")
            elif 1 <= idx <= 3 and "sfm" not in done:
                shot("12_e2e_running_sfm.png")
                done.add("sfm")
            elif idx == 4 and "train" not in done:
                shot("13_e2e_running_train.png")
                done.add("train")
        if st in ("failed", "done", "cancelled"):
            print("[final] " + st)
            print("[error] " + json.dumps(d.get("error"), ensure_ascii=False))
            meta = d.get("error") if st == "failed" else {"status": st}
            if isinstance(meta, dict):
                meta["projectId"] = d.get("projectId") or d.get("id")
                meta["state"] = st
                try:
                    with open(os.path.join(ROOT, "docs", "artifacts", "e2e_meta.json"), "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print("[warn] write e2e_meta.json failed:", e)
            time.sleep(3)
            shot("13_e2e_failed_train.png" if st == "failed" else "13_e2e_done.png")
            break
        time.sleep(4)

    # 修复后的数据集布局: SfM 产物在 files/projects/<id>/dataset/sparse/0/
    ds = shell("run-as %s sh -c 'ls -l files/projects/*/dataset/sparse/0 | tail -8'" % PKG)
    print("[dataset/sparse/0]")
    print(ds)
    try:
        with open(os.path.join(ROOT, "docs", "artifacts", "e2e_colmap_files.txt"), "w", encoding="utf-8") as f:
            f.write("dataset/sparse/0 listing:\n" + ds + "\n")
    except Exception as e:
        print("[warn] write e2e_colmap_files.txt failed:", e)

    c.ev("App.setTab('gallery');'ok'", 10)
    time.sleep(3)
    shot("14_e2e_gallery.png")

    c.ev("Viewer.open('demo','真实高斯 edit.ply');'ok'", 30)
    time.sleep(22)
    shot("15_e2e_viewer.png")
    print("[viewer] " + str(c.ev(
        "(function(){var L=document.getElementById('layer-viewer');"
        "return JSON.stringify({hidden:L.hidden,"
        "ph:(L.querySelector('.vr-placeholder')?L.querySelector('.vr-placeholder').innerText:''),"
        "splats:(document.getElementById('v-splats')?document.getElementById('v-splats').textContent:''),"
        "size:(document.getElementById('v-size')?document.getElementById('v-size').textContent:'')});})()", 10)))
    print("== DONE ==")


if __name__ == "__main__":
    main()
