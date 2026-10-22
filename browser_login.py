from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def find_edge() -> str:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("msedge")
    if found:
        return found
    raise FileNotFoundError("未找到 Microsoft Edge")


class CDPSocket:
    def __init__(self, url: str):
        from urllib.parse import urlparse

        parsed = urlparse(url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = self._read_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("无法连接 Edge 调试接口")
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if expected.lower() not in response.lower():
            raise RuntimeError("Edge WebSocket 握手校验失败")

    def _read_until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("连接提前关闭")
            data += chunk
        return data

    def send_json(self, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        mask = secrets.token_bytes(4)
        length = len(data)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(header + masked)

    def recv_json(self) -> dict:
        first = self._recv_exact(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        if first[1] & 0x80:
            mask = self._recv_exact(4)
        else:
            mask = None
        data = self._recv_exact(length)
        if mask:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        if opcode == 8:
            raise ConnectionError("Edge 页面已关闭")
        if opcode == 9:
            return self.recv_json()
        return json.loads(data.decode("utf-8"))

    def _recv_exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("连接提前关闭")
            data += chunk
        return data

    def evaluate(self, expression: str, request_id: int) -> str | None:
        self.send_json({"id": request_id, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}})
        while True:
            response = self.recv_json()
            if response.get("id") == request_id:
                return (((response.get("result") or {}).get("result") or {}).get("value"))


def login_and_capture_token(timeout_seconds: int = 300) -> str:
    profile = Path(tempfile.mkdtemp(prefix="autodl-watch-login-"))
    process = None
    try:
        process = subprocess.Popen(
            [
                find_edge(),
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "https://www.autodl.com/login?url=%2Fconsole%2Finstance%2Flist",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        port_file = profile / "DevToolsActivePort"
        deadline = time.time() + timeout_seconds
        while time.time() < deadline and not port_file.exists():
            time.sleep(0.2)
        if not port_file.exists():
            raise TimeoutError("启动 Edge 登录窗口超时")
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        request_id = 1
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as response:
                    pages = json.loads(response.read())
                page = next((item for item in pages if item.get("type") == "page" and "autodl.com" in item.get("url", "")), None)
                if page:
                    ws = CDPSocket(page["webSocketDebuggerUrl"])
                    ws.sock.settimeout(3)
                    token = ws.evaluate("localStorage.getItem('token')", request_id)
                    ws.sock.close()
                    request_id += 1
                    if token:
                        return str(token)
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError("登录等待超时，请重新尝试")
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(profile, ignore_errors=True)
