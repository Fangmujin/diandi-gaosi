"""通过 CDP 驱动 WebView 打开查看页，并抓 console 日志 / 渲染状态。

用法：python build/viewer_check.py <expr> [waitSec]
- 默认表达式会打开 demo 项目的查看页
- 除返回值外，还会把这段时间内的 console / exception 一起打印出来，
  便于定位 "渲染失败" 时到底是转换失败、加载失败还是 WebGL 不支持。
"""
import base64
import json
import os
import socket
import sys
import time
import urllib.request

HOST, PORT = "127.0.0.1", 9222


# 本机有全局 http_proxy，urllib 会把 127.0.0.1:9222 也走代理 -> 502，这里显式禁用代理
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(path):
    with _opener.open("http://%s:%d%s" % (HOST, PORT, path), timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def ws_connect(url):
    rest = url[5:]
    host_port, path = rest.split("/", 1)
    host, port = host_port.split(":")
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, int(port)))
    s.sendall((
        "GET %s HTTP/1.1\r\nHost: %s:%s\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
        % ("/" + path, host, port, key)
    ).encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    return s


def send(s, obj):
    payload = json.dumps(obj).encode("utf-8")
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        hdr = bytes([0x81, 0x80 | n]) + mask
    elif n < 65536:
        hdr = bytes([0x81, 0x80 | 126]) + n.to_bytes(2, "big") + mask
    else:
        hdr = bytes([0x81, 0x80 | 127]) + n.to_bytes(8, "big") + mask
    s.sendall(hdr + bytes(payload[i] ^ mask[i % 4] for i in range(n)))


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        r = s.recv(n - len(b))
        if not r:
            raise EOFError()
        b += r
    return b


def recv(s):
    b0 = recv_exact(s, 1)[0]
    opcode = b0 & 0x0F
    b1 = recv_exact(s, 1)[0]
    length = b1 & 0x7F
    if length == 126:
        length = int.from_bytes(recv_exact(s, 2), "big")
    elif length == 127:
        length = int.from_bytes(recv_exact(s, 8), "big")
    payload = recv_exact(s, length)
    return opcode, payload


def main(expr, wait_sec):
    pages = http_json("/json/list")
    page = [p for p in pages if p.get("type") == "page"]
    if not page:
        print("no page")
        return 1
    s = ws_connect(page[0]["webSocketDebuggerUrl"])
    s.settimeout(0.4)

    mid = [0]

    def call(method, params=None):
        mid[0] += 1
        send(s, {"id": mid[0], "method": method, "params": params or {}})
        return mid[0]

    call("Runtime.enable")
    call("Log.enable")
    call("Console.enable")
    call("Page.enable")

    # 资产走本机 HTTP（runtime 约 9MB，模拟器回环很慢），先等脚本就绪再触发，
    # 否则会撞上 "Viewer is not defined" 这种假失败
    ready_expr = ("(function(){return JSON.stringify({rs:document.readyState,"
                  "viewer:typeof Viewer,icons:typeof Icons,app:typeof App,"
                  "boot:(document.getElementById('boot')?document.getElementById('boot').hidden:null)});})()")
    ok = False
    for _ in range(int(wait_sec)):
        rid = call("Runtime.evaluate", {"expression": ready_expr, "returnByValue": True})
        t = time.time() + 6
        while time.time() < t:
            try:
                opcode, payload = recv(s)
            except socket.timeout:
                continue
            if opcode != 1:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            if msg.get("id") == rid:
                v = msg.get("result", {}).get("result", {}).get("value")
                print("[ready] " + str(v))
                try:
                    ok = json.loads(v).get("viewer") != "undefined"
                except Exception:
                    ok = False
                break
            if msg.get("method") in ("Runtime.consoleAPICalled", "Log.entryAdded",
                                     "Runtime.exceptionThrown"):
                print("[console] " + json.dumps(msg.get("params", {}), ensure_ascii=False)[:600])
        if ok:
            break
        time.sleep(1)
    if not ok:
        print("viewer script not ready, abort")

    eid = call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})

    logs = []
    result = None
    t0 = time.time()
    extra_exprs = [
        "(function(){var L=document.getElementById('layer-viewer');"
        "return JSON.stringify({hidden:L.hidden,"
        "ph:(L.querySelector('.vr-placeholder')?L.querySelector('.vr-placeholder').innerText:''),"
        "splats:(document.getElementById('v-splats')?document.getElementById('v-splats').textContent:''),"
        "size:(document.getElementById('v-size')?document.getElementById('v-size').textContent:''),"
        "canvas:(function(){var c=document.getElementById('vr-canvas');return c?c.width+'x'+c.height:'none';})()});})()"
    ]
    pending = []
    deadline = t0 + wait_sec
    next_probe = t0 + 3
    while time.time() < deadline:
        try:
            opcode, payload = recv(s)
        except socket.timeout:
            if pending == [] and time.time() >= next_probe and time.time() < deadline - 1:
                pending.append(call("Runtime.evaluate",
                                    {"expression": extra_exprs[0], "returnByValue": True}))
                next_probe = time.time() + 5
            continue
        if opcode != 1:
            continue
        try:
            msg = json.loads(payload.decode("utf-8"))
        except Exception:
            continue
        m = msg.get("method")
        if m in ("Runtime.consoleAPICalled", "Log.entryAdded", "Runtime.exceptionThrown"):
            logs.append(msg)
            txt = json.dumps(msg.get("params", {}), ensure_ascii=False)
            print("[console] " + txt[:800])
        elif msg.get("id") == eid:
            result = msg
            print("[eval] " + json.dumps(msg.get("result", {}), ensure_ascii=False)[:1500])
        elif msg.get("id") in pending:
            v = msg.get("result", {}).get("result", {}).get("value")
            print("[state] " + str(v))
            pending = []
    s.close()
    return 0


if __name__ == "__main__":
    e = sys.argv[1] if len(sys.argv) > 1 else "Viewer.open('demo','真实高斯模型')"
    w = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    sys.exit(main(e, w))
