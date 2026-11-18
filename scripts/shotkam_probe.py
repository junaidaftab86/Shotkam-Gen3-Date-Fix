#!/usr/bin/env python3
"""Talk to a ShotKam Gen 3 over its Wi-Fi (Ambarella JSON API, as the ShotKam app does).

Connect the PC to the camera Wi-Fi first. Commands:
  info                     device info and firmware versions        (read only)
  clock                    current camera clock                    (read only)
  settings                 all settings                            (read only)
  setclock "YYYY-MM-DD HH:MM:SS"   set the clock, then read it back
  rangetest                set several test years, read each back, then restore the PC time
  setback                  set the real time minus 4, 8, ... years, so the year is 2025 or
                           earlier (the camera's backup clock loses later years at power-off)
  lowtest                  try a range of low years (2000..2024), set each and read it back,
                           to find the oldest year the firmware accepts. Leaves 2006 if it
                           is accepted, else the oldest accepted year.
"""
import datetime as dt
import json
import socket
import sys
import time

IP, PORT = "192.168.42.1", 7878
LAST_GOOD_YEAR = 2025


def years_back(year):
    """A multiple of 4 (same leap-year pattern) that brings the year to 2025 or earlier."""
    return 0 if year <= LAST_GOOD_YEAR else 4 * -(-(year - LAST_GOOD_YEAR) // 4)


class Camera:
    def __init__(self):
        self.s = socket.create_connection((IP, PORT), timeout=10)
        self.buf = ""
        self.token = 0
        r = self.call({"msg_id": 257, "token": 0})          # AMBA_START_SESSION
        self.token = r.get("param", 0)

    def _read_json(self):
        dec = json.JSONDecoder()
        while True:
            text = self.buf.lstrip()
            if text:
                try:
                    obj, end = dec.raw_decode(text)
                    self.buf = text[end:]
                    return obj
                except json.JSONDecodeError:
                    pass
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("camera closed the connection")
            self.buf += chunk.decode("utf-8", "replace")

    def call(self, msg):
        msg = dict(msg)
        msg.setdefault("token", self.token)
        print(">>", json.dumps(msg))
        self.s.sendall(json.dumps(msg).encode())
        while True:
            r = self._read_json()
            print("<<", json.dumps(r))
            if r.get("msg_id") == msg["msg_id"]:
                return r

    def get_clock(self):
        return self.call({"msg_id": 1, "type": "camera_clock"}).get("param")

    def set_clock(self, value):
        return self.call({"msg_id": 2, "type": "camera_clock", "param": value})

    def close(self):
        try:
            self.call({"msg_id": 258})                      # AMBA_STOP_SESSION
        finally:
            self.s.close()


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    cam = Camera()
    try:
        if cmd == "info":
            cam.call({"msg_id": 11})
        elif cmd == "clock":
            print("camera clock:", cam.get_clock())
        elif cmd == "settings":
            cam.call({"msg_id": 3})
        elif cmd == "setclock":
            cam.set_clock(sys.argv[2])
            time.sleep(2)
            print("camera clock now:", cam.get_clock())
        elif cmd == "rangetest":
            now = dt.datetime.now()
            for year in (2024, 2025, 2026, 2027, 2030, 2037, 2038):
                value = now.replace(year=year).strftime("%Y-%m-%d %H:%M:%S")
                r = cam.set_clock(value)
                time.sleep(2)
                print(f"RESULT set {value} rval={r.get('rval')} -> reads {cam.get_clock()}")
            cam.set_clock(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            print("restored, camera clock:", cam.get_clock())
        elif cmd == "lowtest":
            now = dt.datetime.now()
            accepted = []
            for year in (2024, 2021, 2020, 2017, 2014, 2010, 2008, 2006, 2004, 2002, 2000):
                try:
                    target = now.replace(year=year)
                except ValueError:                 # 29 Feb in a non-leap test year
                    target = now.replace(year=year, day=28)
                value = target.strftime("%Y-%m-%d %H:%M:%S")
                r = cam.set_clock(value)
                time.sleep(1)
                got = str(cam.get_clock())
                ok = r.get("rval") == 0 and got.startswith(str(year))
                print(f"RESULT year {year}: rval={r.get('rval')} reads {got} -> {'ACCEPTED' if ok else 'REJECTED'}")
                if ok:
                    accepted.append(year)
            print("\nAccepted years:", accepted)
            if accepted:
                print("Oldest accepted:", min(accepted), "| 2006 accepted:", 2006 in accepted)
                final = 2006 if 2006 in accepted else min(accepted)
                cam.set_clock(now.replace(year=final).strftime("%Y-%m-%d %H:%M:%S"))
                print(f"left camera set to {final}; reads {cam.get_clock()}")
                print("NOTE: this only proves the year is accepted while powered. Whether it")
                print("survives a power-off needs the new battery (the dead cell likely backs the RTC).")
            else:
                print("No test year accepted. Re-run or report this output.")
        elif cmd == "setback":
            now = dt.datetime.now()
            target = now.replace(year=now.year - years_back(now.year))
            r = cam.set_clock(target.strftime("%Y-%m-%d %H:%M:%S"))
            time.sleep(1)
            got = cam.get_clock()
            print(f"camera clock set back to: {got}")
            if r.get("rval") != 0 or not str(got).startswith(str(target.year)):
                sys.exit(1)
        else:
            sys.exit(__doc__)
    finally:
        cam.close()


if __name__ == "__main__":
    main()
