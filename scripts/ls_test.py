#!/usr/bin/env python3
"""Find the folder-listing request that the camera firmware accepts.

Run on the camera Wi-Fi (Termux or PC):  python ls_test.py
Read-only: it sends only session, info, clock-read, CD and LS requests.
The output also goes to ls_test.txt (in ~/storage/downloads on Termux).
"""
import json
import os
import socket
import time
from pathlib import Path

IP, PORT = os.environ.get("SHOTKAM_IP", "192.168.42.1"), 7878
out_dir = Path.home() / "storage" / "downloads"
OUT = (out_dir if out_dir.exists() else Path.cwd()) / "ls_test.txt"
lines = []


def log(text):
    print(text)
    lines.append(text)


class Cam:
    def __init__(self):
        self.s = socket.create_connection((IP, PORT), timeout=8)
        self.buf, self.token = "", 0
        self.token = self.call({"msg_id": 257, "token": 0}).get("param", 0)

    def read(self):
        dec = json.JSONDecoder()
        while True:
            t = self.buf.lstrip()
            if t:
                try:
                    obj, end = dec.raw_decode(t)
                    self.buf = t[end:]
                    return obj
                except ValueError:
                    pass
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("camera closed the connection")
            self.buf += chunk.decode("utf-8", "replace")

    def call(self, msg):
        msg = dict(msg)
        msg.setdefault("token", self.token)
        log(">> " + json.dumps(msg))
        self.s.sendall(json.dumps(msg).encode())
        while True:
            try:
                r = self.read()
            except socket.timeout:
                log("<< (no reply in 8 s)")
                return {}
            log("<< " + json.dumps(r)[:600])
            if r.get("msg_id") == msg["msg_id"]:
                return r


def main():
    log(f"ls_test {time.strftime('%Y-%m-%d %H:%M:%S')}")
    cam = Cam()
    steps = [
        ("info", {"msg_id": 11}),
        ("clock", {"msg_id": 1, "type": "camera_clock"}),
        ("app: CD VIDEOS", {"msg_id": 1283, "param": "/tmp/SD0/VIDEOS/"}),
        ("app: LS SHOTKAM path", {"msg_id": 1282, "param": "/tmp/SD0/SHOTKAM/"}),
        ("LS SHOTKAM no slash", {"msg_id": 1282, "param": "/tmp/SD0/SHOTKAM"}),
        ("LS SHOTKAM -D -S", {"msg_id": 1282, "param": "/tmp/SD0/SHOTKAM/ -D -S"}),
        ("CD SD0", {"msg_id": 1283, "param": "/tmp/SD0/"}),
        ("LS cwd", {"msg_id": 1282}),
        ("LS cwd -D -S", {"msg_id": 1282, "param": " -D -S"}),
        ("CD SHOTKAM", {"msg_id": 1283, "param": "/tmp/SD0/SHOTKAM/"}),
        ("LS cwd in SHOTKAM", {"msg_id": 1282}),
        ("LS cwd in SHOTKAM -D -S", {"msg_id": 1282, "param": " -D -S"}),
        ("LS SHOTKAM path after CD", {"msg_id": 1282, "param": "/tmp/SD0/SHOTKAM/"}),
        ("CD day folder", {"msg_id": 1283, "param": "/tmp/SD0/SHOTKAM/11-18-2018/"}),
        ("LS day folder", {"msg_id": 1282, "param": " -D -S"}),
        ("CD AMBA", {"msg_id": 1283, "param": "/tmp/SD0/AMBA/"}),
    ]
    try:
        for name, msg in steps:
            log(f"\n== {name}")
            cam.call(msg)
            time.sleep(0.3)
        log("\n== stop session")
        cam.call({"msg_id": 258})
    finally:
        cam.s.close()
        OUT.write_text("\n".join(lines) + "\n")
        print(f"\nSaved to {OUT}")


if __name__ == "__main__":
    main()
