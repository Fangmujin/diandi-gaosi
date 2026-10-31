"""端到端联调取证：导入 → 运行 → 查看 → 图库，每步设备端 screencap 落 docs/artifacts/shots/。

用法：python build/e2e_run.py
前置：
  - 模拟器已启动、adb 在线
  - 应用已安装并可调试（webview devtools socket 可用）
  - D:/点点高斯/Inputs/54734.mp4 存在（导入素材）
  - build/edit_small.ply 存在（查看页用的真实高斯）

设计要点（本机 adb 极不稳定，daemon 常被回收）：
  - 所有 adb 调用都带重试
  - 不依赖单条长连接，轮询式读取
  - 截图一律设备端 screencap 落 /sdcard 再 pull（PowerShell/bash 二进制重定向会写坏 PNG）
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
VIDEO = "D:/点点高斯/Inputs/54734.mp4"

sys.path.insert(0, os.path.join(ROOT, "build"))
import viewer_check as vc  # 复用它的 WebSocket / CDP 代码


def sh(args, retry=4):
    last = ""
    for i in range(retry):
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0 and "offline" not in out and "no devices" not in out:
            return out.strip()
        last = out.strip()
        time.sleep(2)
    print("adb failed:", args[:3], last[:200])
    return ""


def shell(cmd, retry=4):
    return sh([ADB, "shell", cmd], retry)


def tap_wait():
    # 模拟器在高负载下常弹出 "System UI isn't responding"，截图前点 Wait 把它按掉
    shell("input tap 350 1520", retry=1)
    time.sleep(0.8)


def shot(name):
    tap_wait()
    remote = "/sdcard/" + name
    shell("screencap -p " + remote)
    time.sleep(0.4)
    dst = os.path.join(SHOTS, name)
    sh([ADB, "pull", remote, dst])
    sz = os.path.getsize(dst) if os.path.exists(dst) else 0
    print("[shot] " + name + "  " + str(sz) + " bytes")
    return dst


def eval_expr(ws, send, recv, mid, expr, timeout=25):
    mid[0] += 1
    i = mid[0]
    send(ws, {"id": i, "method": "Runtime.evaluate",
              "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}})
    t = time.time() + timeout
    while time.time() < t:
        try:
            opcode, payload = recv(ws)
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
    return None


def main():
    if not os.path.exists(SHOTS):
        os.makedirs(SHOTS)

    # 1) 把素材推到应用的 imports 目录
    print("== push 素材 ==")
    tmp = "/data/local/tmp/54734.mp4"
    sh([ADB, "push", VIDEO, tmp], retry=3)
    shell("run-as %s mkdir -p files/imports" % PKG)
    shell("run-as %s sh -c 'cat %s > files/imports/54734.mp4'" % (PKG, tmp))
    print(shell("run-as %s ls -l files/imports/" % PKG))

    # 2) 连接 CDP
    pid = ""
    for _ in range(10):
        out = shell("ps -A")
        m = re.search(r"\S+\s+(\d+)\s+\d+.*" + PKG + r"\s*$", out, re.M)
        if m:
            pid = m.group(1)
            break
        time.sleep(2)
    if not pid:
        print("app not running, start it")
        shell("am start -n %s/.MainActivity" % PKG)
        time.sleep(15)
        out = shell("ps -A")
        m = re.search(r"\S+\s+(\d+)\s+\d+.*" + PKG + r"\s*$", out, re.M)
        pid = m.group(1) if m else ""
    print("pid=" + pid)
    sh([ADB, "forward", "tcp:9222", "localabstract:webview_devtools_remote_" + pid])
    time.sleep(1)

    pages = vc.http_json("/json/list")
    page = [p for p in pages if p.get("type") == "page"][0]
    ws = vc.ws_connect(page["webSocketDebuggerUrl"])
    ws.settimeout(0.5)
    mid = [0]

    def call(method, params=None):
        mid[0] += 1
        vc.send(ws, {"id": mid[0], "method": method, "params": params or {}})
        return mid[0]

    call("Runtime.enable")
    call("Log.enable")
    call("Console.enable")

    # 等脚本就绪
    for _ in range(60):
        v = eval_expr(ws, vc.send, vc.recv, mid,
                      "JSON.stringify({app:typeof App,viewer:typeof Viewer,rs:document.readyState})", 8)
        if v and '"app":"object"' in v:
            print("[ready] " + v)
            break
        time.sleep(2)

    # 3) 导入页：把素材塞进 App.state.media 并渲染
    print("== 导入页 ==")
    eval_expr(ws, vc.send, vc.recv, mid,
              "App.state.media=[{rel:'54734.mp4',name:'54734.mp4',kind:'video',"
              "bytes:%d,durationMs:8700}];App.state.tab='new';App.render();'ok'"
              % os.path.getsize(VIDEO))
    time.sleep(4)
    shot("10_e2e_import.png")

    # 4) 启动管道
    print("== 启动管道 ==")
    eval_expr(ws, vc.send, vc.recv, mid, "App.startJob({name:'端到端 54734',quality:'fast'});'ok'")
    time.sleep(5)
    shot("11_e2e_running_start.png")

    # 5) 轮询进度，抓"正在运行"的画面
    print("== 轮询进度 ==")
    last_state = ""
    shots_taken = set()
    deadline = time.time() + 1500
    while time.time() < deadline:
        v = eval_expr(ws, vc.send, vc.recv, mid, "Native.jobProgress()", 10)
        if not v:
            time.sleep(3)
            continue
        try:
            d = json.loads(v)
        except Exception:
            time.sleep(3)
            continue
        st = d.get("state", "")
        ov = int(round((d.get("overall") or 0) * 100))
        eta = d.get("etaSec", 0)
        idx = d.get("stageIndex", 0)
        names = ["抽帧", "提特征", "匹配", "重建", "训练", "导出"]
        cur_name = names[idx] if 0 <= idx < len(names) else "?"
        line = "%s overall=%d%% eta=%ss stage=%s(%d)" % (st, ov, eta, cur_name, idx)
        if line != last_state:
            print("[prog] " + line)
            last_state = line
        # 关键节点截图
        if st == "running":
            if idx == 0 and "s0" not in shots_taken:
                shot("11_e2e_running_extract.png"); shots_taken.add("s0")
            if idx >= 1 and "s1" not in shots_taken:
                shot("12_e2e_running_sfm.png"); shots_taken.add("s1")
            if idx == 4 and "s4" not in shots_taken:
                shot("13_e2e_running_train.png"); shots_taken.add("s4")
        if st in ("failed", "done", "cancelled"):
            print("[final state] " + st)
            print("[error] " + json.dumps(d.get("error"), ensure_ascii=False))
            time.sleep(4)
            shot("13_e2e_failed_train.png" if st == "failed" else "13_e2e_done.png")
            break
        time.sleep(4)

    # 6) 训练阶段落盘产物清单
    print("== ProjectStore 产物 ==")
    print(shell("run-as %s sh -c 'ls -lR files/projects | head -80'" % PKG))

    # 7) 图库页
    print("== 图库页 ==")
    eval_expr(ws, vc.send, vc.recv, mid, "App.setTab('gallery');'ok'")
    time.sleep(3)
    shot("14_e2e_gallery.png")

    # 8) 查看页：用真实高斯
    print("== 查看页 ==")
    eval_expr(ws, vc.send, vc.recv, mid, "Viewer.open('demo','真实高斯 edit.ply');'ok'", 30)
    time.sleep(25)
    shot("15_e2e_viewer.png")
    v = eval_expr(ws, vc.send, vc.recv, mid,
                  "(function(){var L=document.getElementById('layer-viewer');"
                  "return JSON.stringify({hidden:L.hidden,"
                  "ph:(L.querySelector('.vr-placeholder')?L.querySelector('.vr-placeholder').innerText:''),"
                  "splats:(document.getElementById('v-splats')?document.getElementById('v-splats').textContent:''),"
                  "size:(document.getElementById('v-size')?document.getElementById('v-size').textContent:''),"
                  "canvas:(function(){var c=document.getElementById('vr-canvas');return c?c.width+'x'+c.height:'none';})()});})()", 10)
    print("[viewer] " + str(v))
    ws.close()
    print("== E2E 完成 ==")


if __name__ == "__main__":
    main()
