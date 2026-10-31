"""CDP Runtime.evaluate client."""
import base64
import hashlib
import json
import os
import random
import socket
import sys
import time
import urllib.request


def get_ws_url():
    req = urllib.request.Request("http://127.0.0.1:9222/json/list")
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    for p in data:
        if "webSocketDebuggerUrl" in p:
            return p["webSocketDebuggerUrl"]
    raise RuntimeError("no ws url")


def parse_url(url):
    rest = url[5:]
    host_port, path = rest.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":")
        port = int(port)
    else:
        host, port = host_port, 80
    return host, port, "/" + path


def send_text(sock, text):
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = bytes([0x81, 0x80 | length]) + mask
    elif length < 65536:
        header = bytes([0x81, 0x80 | 126]) + length.to_bytes(2, "big") + mask
    else:
        header = bytes([0x81, 0x80 | 127]) + length.to_bytes(8, "big") + mask
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(length))
    sock.sendall(header + masked)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        r = sock.recv(n - len(buf))
        if not r:
            raise EOFError()
        buf += r
    return buf


def recv_text(sock):
    b0 = ord(recv_exact(sock, 1))
    opcode = b0 & 0x0F
    b1 = ord(recv_exact(sock, 1))
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = int.from_bytes(recv_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(recv_exact(sock, 8), "big")
    mask = recv_exact(sock, 4) if masked else b""
    payload = recv_exact(sock, length)
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(length))
    return opcode, payload


def main(expr, timeout=6):
    url = get_ws_url()
    host, port, path = parse_url(url)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode("ascii"))
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += sock.recv(4096)
    if b"101" not in resp.split(b"\r\n")[0]:
        print("handshake failed:", resp.decode(errors="ignore")[:500])
        return 1
    send_text(sock, json.dumps({"id": 1, "method": "Runtime.enable", "params": {}}))
    # consume until Runtime.executionContextCreated maybe
    t0 = time.time()
    ctx_id = None
    while time.time() - t0 < timeout:
        try:
            sock.settimeout(0.3)
            opcode, payload = recv_text(sock)
            if opcode == 1:
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("method") == "Runtime.executionContextCreated":
                    ctx_id = msg["params"]["context"]["id"]
                    break
        except socket.timeout:
            continue
    if ctx_id is None:
        print("no execution context")
        return 1
    send_text(sock, json.dumps({
        "id": 2,
        "method": "Runtime.evaluate",
        "params": {"expression": expr, "includeCommandLineAPI": True}
    }))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            sock.settimeout(0.3)
            opcode, payload = recv_text(sock)
            if opcode == 1:
                msg = json.loads(payload.decode("utf-8"))
                if msg.get("id") == 2:
                    print(json.dumps(msg, ensure_ascii=False))
                    return 0
        except socket.timeout:
            continue
    print("no eval response")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 6.0))
