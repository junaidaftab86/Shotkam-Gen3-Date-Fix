#!/usr/bin/env python3
"""ShotKam Gen 3 Wi-Fi sync: set the lagged clock, download new clips, fix their dates.

Runs on Termux (Android) or a PC with Python 3 and ffmpeg. Connect to the camera
Wi-Fi first. Never open the ShotKam app: it sets the real year and breaks the clock.

  python shotkam_sync.py                 set clock, download new clips, fix dates
  python shotkam_sync.py --list          only list the clips on the camera
  python shotkam_sync.py --no-clock      do not touch the camera clock
  python shotkam_sync.py --serve         web page on http://127.0.0.1:8080 with a Sync button

Protocol (from the decompiled ShotKam app, AmbarellaAsyncSocketRepositoryImpl):
  control port 7878, JSON messages; data port 8787, raw file bytes.
  1. connect 8787 (the app does this before the request)
  2. {"msg_id":1285,"token":T,"param":"<path>","offset":0,"fetch_size":0}
     -> {"rval":0,"msg_id":1285,"size":N,"rem_size":N}
  3. read N bytes from 8787
  4. notification {"msg_id":7,"type":"get_file_complete","param":[{"bytes sent":N},{"md5sum":"..."}]}
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import selectors
import socket
import sys
import threading
import time
from pathlib import Path

import fix_shotkam_dates as fixer

IP = os.environ.get("SHOTKAM_IP", "192.168.42.1")
CTRL_PORT, DATA_PORT = 7878, 8787
VIDEOS = "/tmp/SD0/VIDEOS/"
LAG_YEARS = 8                      # camera runs 8 years behind (2026 -> 2018)
START_SESSION, STOP_SESSION = 257, 258
GET_SETTING, SET_SETTING = 1, 2
NOTIFICATION = 7
LS, CD, GET_FILE, CANCEL_XFER = 1282, 1283, 1285, 1287

log = print                        # replaced by the web server to capture output


class Camera:
    def __init__(self, ip=IP, verbose=False):
        self.ip, self.verbose = ip, verbose
        self.s = socket.create_connection((ip, CTRL_PORT), timeout=10)
        self.buf = ""
        self.token = 0
        self.notes = []            # notifications that arrived while waiting for a reply
        r = self.call({"msg_id": START_SESSION, "token": 0})
        if r.get("rval", 0) != 0:
            raise RuntimeError(f"camera refused the session: {r}")
        self.token = r.get("param", 0)

    # --- control channel -------------------------------------------------
    def _parse(self):
        text = self.buf.lstrip()
        if not text:
            return None
        try:
            obj, end = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            return None
        self.buf = text[end:]
        return obj

    def _feed(self):
        chunk = self.s.recv(65536)
        if not chunk:
            raise ConnectionError("camera closed the control connection")
        self.buf += chunk.decode("utf-8", "replace")

    def read_msg(self):
        while True:
            obj = self._parse()
            if obj is not None:
                if self.verbose:
                    log("<< " + json.dumps(obj))
                return obj
            self._feed()

    def send(self, msg):
        msg = dict(msg)
        msg.setdefault("token", self.token)
        if self.verbose:
            log(">> " + json.dumps(msg))
        self.s.sendall(json.dumps(msg).encode())
        return msg

    def call(self, msg):
        msg = self.send(msg)
        while True:
            r = self.read_msg()
            if r.get("msg_id") == msg["msg_id"]:
                return r
            self.notes.append(r)

    def close(self):
        try:
            self.call({"msg_id": STOP_SESSION})
        except Exception:
            pass
        self.s.close()

    # --- clock -----------------------------------------------------------
    def get_clock(self):
        return self.call({"msg_id": GET_SETTING, "type": "camera_clock"}).get("param")

    def set_clock(self, when):
        r = self.call({"msg_id": SET_SETTING, "type": "camera_clock",
                       "param": when.strftime("%Y-%m-%d %H:%M:%S")})
        return r.get("rval") == 0

    # --- storage ---------------------------------------------------------
    def ls(self, folder):
        """Entries of a camera folder as (name, is_dir, size or None)."""
        r = self.call({"msg_id": CD, "param": folder})
        if r.get("rval", 0) != 0:
            raise RuntimeError(f"cd {folder} failed: {r}")
        r = self.call({"msg_id": LS, "param": " -D -S"})
        if r.get("rval", 0) != 0 or "listing" not in r:
            r = self.call({"msg_id": LS})
        out = []
        for item in r.get("listing") or []:
            for name, value in item.items():
                size = None
                head = str(value).split("|")[0].split()
                if head and head[0].isdigit():
                    size = int(head[0])
                out.append((name.rstrip("/"), name.endswith("/"), size))
        return out

    def clips(self):
        """All MP4 clips under VIDEOS/, as (camera path, size or None)."""
        found, todo = [], [VIDEOS]
        while todo:
            folder = todo.pop()
            for name, is_dir, size in self.ls(folder):
                path = folder + name
                if is_dir:
                    todo.append(path + "/")
                elif name.lower().endswith(".mp4"):
                    found.append((path, size))
        return sorted(found)

    def download(self, path, dest, progress=None):
        """Fetch one file over the data port. Returns the byte count; checks the MD5."""
        data = socket.create_connection((self.ip, DATA_PORT), timeout=5)
        data.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        tmp = dest.with_name(dest.name + ".part")
        md5, got, size, camera_md5, done = hashlib.md5(), 0, None, None, False
        try:
            self.send({"msg_id": GET_FILE, "param": path, "offset": 0, "fetch_size": 0})
            sel = selectors.DefaultSelector()
            sel.register(self.s, selectors.EVENT_READ, "ctrl")
            sel.register(data, selectors.EVENT_READ, "data")
            last = time.monotonic()
            with open(tmp, "wb") as out:
                while not (done and size is not None and got >= size):
                    events = sel.select(timeout=1)
                    if not events and time.monotonic() - last > 15:
                        raise TimeoutError(f"no data for 15 s ({got} of {size} bytes)")
                    for key, _ in events:
                        last = time.monotonic()
                        if key.data == "data":
                            chunk = data.recv(1 << 20)
                            if not chunk:
                                sel.unregister(data)
                                if size is None or got < size:
                                    raise ConnectionError("camera closed the data port early")
                                continue
                            out.write(chunk)
                            md5.update(chunk)
                            got += len(chunk)
                            if progress and size:
                                progress(got, size)
                        else:
                            self._feed()
                            while (msg := self._parse()) is not None:
                                if self.verbose:
                                    log("<< " + json.dumps(msg))
                                if msg.get("msg_id") == GET_FILE:
                                    if msg.get("rval", 0) != 0:
                                        raise RuntimeError(f"camera refused {path}: {msg}")
                                    size = msg.get("size", msg.get("rem_size"))
                                elif msg.get("type") == "get_file_complete":
                                    done = True
                                    for p in msg.get("param") or []:
                                        camera_md5 = p.get("md5sum") or camera_md5
                                else:
                                    self.notes.append(msg)
            if camera_md5 and camera_md5.lower() != md5.hexdigest():
                raise ValueError(f"MD5 mismatch: camera {camera_md5}, got {md5.hexdigest()}")
            os.replace(tmp, dest)
            return got
        except BaseException:
            try:
                self.send({"msg_id": CANCEL_XFER, "param": path})
            except Exception:
                pass
            tmp.unlink(missing_ok=True)
            raise
        finally:
            data.close()


def lagged_now(lag=LAG_YEARS):
    return fixer.add_years(dt.datetime.now().replace(microsecond=0), -lag)


def default_dest():
    phone = Path.home() / "storage" / "shared" / "DCIM" / "ShotKam"
    if (Path.home() / "storage" / "shared").exists():
        return phone
    return Path.home() / "shotkam" / "clips"


def local_name(cam_path):
    """VIDEOS/<folder>/<file> -> <folder>_<file>, so equal names in two folders do not clash."""
    parts = cam_path[len(VIDEOS):].strip("/").split("/")
    return "_".join(parts)


def sync(dest, set_clock=True, list_only=False, lag=LAG_YEARS, verbose=False):
    dest.mkdir(parents=True, exist_ok=True)
    state_file = dest / ".shotkam_downloaded.json"
    try:
        state = set(json.loads(state_file.read_text()))
    except (OSError, ValueError):
        state = set()

    log(f"Connecting to {IP} ...")
    cam = Camera(verbose=verbose)
    new_files = []
    try:
        log(f"Camera clock: {cam.get_clock()}")
        if set_clock and not list_only:
            target = lagged_now(lag)
            ok = cam.set_clock(target)
            log(f"Clock set to {target} ({'ok' if ok else 'REFUSED'}); reads {cam.get_clock()}")

        clips = cam.clips()
        todo = [(p, s) for p, s in clips
                if p not in state and not (dest / local_name(p)).exists()]
        log(f"{len(clips)} clips on the camera, {len(todo)} new.")
        if list_only:
            for p, s in clips:
                log(f"  {'NEW ' if (p, s) in todo else '    '}{p}  {s or '?'} bytes")
            return []

        for i, (path, size) in enumerate(todo, 1):
            target = dest / local_name(path)
            log(f"[{i}/{len(todo)}] {path} -> {target.name}")
            shown = [-1]

            def progress(got, total):
                pct = got * 100 // total
                if pct // 10 != shown[0]:
                    shown[0] = pct // 10
                    log(f"    {pct}%  ({got // 1048576} of {total // 1048576} MB)")
            try:
                n = cam.download(path, target, progress)
            except Exception as e:
                log(f"    FAILED: {e}")
                continue
            log(f"    done, {n} bytes, MD5 checked")
            state.add(path)
            state_file.write_text(json.dumps(sorted(state)))
            new_files.append(target)
    finally:
        cam.close()

    if not new_files:
        log("No new clips.")
        return []
    changes, skipped = fixer.plan_fixed_offset(new_files, lag, 3)
    for f, cam_time, reason in skipped:
        log(f"  SKIP date fix {f.name}: recorded {cam_time:%Y-%m-%d} - {reason}.")
    for f, old, new in changes:
        try:
            fixer.apply_change(f, new)
            log(f"  date {f.name}: {old:%Y-%m-%d %H:%M} -> {new:%Y-%m-%d %H:%M}")
        except Exception as e:
            log(f"  date fix FAILED {f.name}: {e}")
    fixer.media_scan([dest])
    log(f"Finished: {len(new_files)} clips in {dest}")
    return new_files


# --- small web front end (browsers cannot open raw TCP, so Python does the work) ---
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ShotKam Sync</title>
<style>body{font:16px system-ui;margin:16px;max-width:720px}button{font-size:18px;padding:12px 20px;margin:4px 8px 4px 0}
pre{background:#111;color:#ddd;padding:12px;white-space:pre-wrap;min-height:200px}</style></head><body>
<h2>ShotKam Sync</h2>
<label><input type="checkbox" id="clock" checked> Set camera clock (real time minus %LAG% years)</label><br>
<button onclick="go('sync')">Sync</button><button onclick="go('list')">List only</button>
<pre id="log"></pre>
<script>
async function go(a){await fetch('/run?action='+a+'&clock='+(clock.checked?1:0),{method:'POST'});}
async function poll(){try{const r=await fetch('/log');log.textContent=await r.text();}catch(e){}setTimeout(poll,1000);}
poll();
</script></body></html>"""


def serve(dest, lag, port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    global log
    lines, busy = [], threading.Lock()

    def capture(msg):
        lines.append(str(msg))
        print(msg)
    log = capture

    def worker(action, clock):
        try:
            sync(dest, set_clock=clock, list_only=(action == "list"), lag=lag)
        except Exception as e:
            capture(f"ERROR: {e}  (is the phone on the camera Wi-Fi?)")
        finally:
            busy.release()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body, ctype="text/plain; charset=utf-8", code=200):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/log":
                return self.reply("\n".join(lines[-400:]))
            self.reply(PAGE.replace("%LAG%", str(lag)), "text/html; charset=utf-8")

        def do_POST(self):
            q = parse_qs(urlparse(self.path).query)
            if not busy.acquire(blocking=False):
                return self.reply("busy", code=409)
            lines.clear()
            threading.Thread(target=worker, daemon=True,
                             args=(q.get("action", ["sync"])[0], q.get("clock", ["1"])[0] == "1")).start()
            self.reply("started")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Open http://127.0.0.1:{port} in the browser. Ctrl+C stops the server.")
    srv.serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dest", type=Path, default=default_dest(), help="download folder")
    p.add_argument("--lag", type=int, default=LAG_YEARS, help="years the camera runs behind (8)")
    p.add_argument("--list", action="store_true", help="only list clips, change nothing")
    p.add_argument("--no-clock", action="store_true", help="do not set the camera clock")
    p.add_argument("--serve", action="store_true", help="start the web page")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("-v", "--verbose", action="store_true", help="print every JSON message")
    a = p.parse_args()
    if a.lag % 4:
        sys.exit("--lag must be a multiple of 4 (keeps the leap-year pattern).")
    if a.serve:
        serve(a.dest, a.lag, a.port)
    else:
        sync(a.dest, set_clock=not a.no_clock, list_only=a.list, lag=a.lag, verbose=a.verbose)


if __name__ == "__main__":
    main()
