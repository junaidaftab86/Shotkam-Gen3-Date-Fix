#!/usr/bin/env python3
"""Fix wrong recording dates on ShotKam clips (camera clock reset to 2017/1971).

Runs on Android (Termux) or any PC with Python 3 and ffmpeg.

How a clip gets its real date (first match wins):
  1. --date YYYY-MM-DD on the command line
  2. a camera folder name MM-DD-YYYY from 2019 or later (a renamed folder)
  3. the file date on the phone (the download date; download on the shooting day)

If the camera clock was set back 4, 8, ... years (shotkam_probe.py setback),
the script adds those years back and keeps the exact recording time.

For clips copied straight off the camera by USB (no download-date to go by),
use --add-years N: the camera runs N years behind (e.g. set to 2018 = lag 8),
so N is added to every clip's year, keeping the exact month/day/time. This is
the mode for the fixed-lag workflow. It is idempotent (clips already at 2026+
are left alone) and flags any clip whose year is below the expected lag floor,
which usually means that session's clock had reset to 2017.

Clips whose recorded date is within 2 days of the real date are left alone,
so it is safe to run the script again and again on the same folder.
Clips of one day keep their spacing from the camera clock. The first clip
of the day starts at --start (default 09:00).

The script only prints its plan. Add --apply to change the files.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

FOLDER_DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
MAX_GAP = dt.timedelta(hours=6)    # a larger or negative gap means a clock reset
RESET_GAP = dt.timedelta(minutes=1)
TOLERANCE = dt.timedelta(days=2)
MIN_TRUSTED_YEAR = 2019            # folder names before this can come from the broken clock


def find_default_folders():
    shared = Path.home() / "storage" / "shared"
    if not shared.exists():
        return []
    hits = []
    for root, dirs, _ in os.walk(shared):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Android"]
        for d in dirs:
            if "shotkam" in d.lower():
                hits.append(Path(root) / d)
        if root.count(os.sep) - str(shared).count(os.sep) >= 3:
            dirs[:] = []
    return hits


def camera_time(path):
    """Recorded time from the MP4. The camera writes local time with a 'Z'."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    try:
        value = json.loads(out.stdout)["format"]["tags"]["creation_time"]
        return dt.datetime.fromisoformat(value.replace("Z", "")[:19])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def real_date(path, forced):
    if forced:
        return forced
    m = FOLDER_DATE.match(path.parent.name)
    # The broken clock also names folders (2017/2018), so trust only later years.
    if m and int(m[3]) >= MIN_TRUSTED_YEAR:
        return dt.date(int(m[3]), int(m[1]), int(m[2]))
    return dt.datetime.fromtimestamp(path.stat().st_mtime).date()


def shift_forward(cam, real):
    """Undo 'shotkam_probe.py setback': the camera runs 4, 8, ... years behind.

    Returns the exact real time, or None when no such shift fits the real date.
    """
    if cam.year > 2025:
        return None
    for back in range(4, 41, 4):
        if cam.year + back < 2026:
            continue  # the clock worked before 2026, so no shift was used then
        shifted = cam.replace(year=cam.year + back)
        if abs(shifted - real) <= TOLERANCE:
            return shifted
    return None


def add_years(d, n):
    """d plus n years, keeping month/day/time. Feb 29 -> Feb 28 in a non-leap year."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


RESET_YEAR = 2017                  # the camera shows 2017-01-01 after a clock reset


def plan_fixed_offset(files, n, window, since=2026):
    """Add n years to lagged clips inside a narrow year window.

    floor = since - n is the year the camera was set to (e.g. 2018 for n=8, since=2026;
    2022 for n=12, since=2034). It is never below 2018, so reset clips stay as they are. Only
    clips with year in [floor, floor + window - 1] are shifted (window=3 -> 2018-2020),
    which keeps the script off genuine older footage. Returns (changes, skipped),
    where each skipped item is (file, cam_time, reason):
      - year < floor  -> that session's clock had reset; fix with --date.
      - year > upper (but < 2026) -> past the window; widen --window or re-set the camera.
    Clips already at 2026+ are left alone.
    """
    changes, skipped = [], []
    floor = max(since - n, RESET_YEAR + 1)
    upper = floor + window - 1
    for f in files:
        cam = camera_time(f)
        if cam is None:
            print(f"  skip (no recorded date): {f}")
            continue
        if cam.year >= since:
            continue            # already corrected, or a genuine date
        if floor <= cam.year <= upper:
            changes.append((f, cam, add_years(cam, n)))
        elif cam.year < floor:
            skipped.append((f, cam, "clock had reset that session; fix with --date YYYY-MM-DD"))
        else:                   # upper < year < since
            skipped.append((f, cam, f"year {cam.year} is past the {floor}-{upper} window; "
                                    "widen with --window or re-set the camera clock"))
    return changes, skipped


def plan(files, forced_date, start):
    groups = defaultdict(list)
    exact = []
    for f in files:
        cam = camera_time(f)
        if cam is None:
            print(f"  skip (no recorded date): {f}")
            continue
        day = real_date(f, forced_date)
        real = dt.datetime.combine(day, cam.time())
        if abs(cam - real) <= TOLERANCE:
            continue  # date already correct
        shifted = shift_forward(cam, real)
        if shifted:
            exact.append((f, cam, shifted))  # clock was set back on purpose: exact time
            continue
        groups[day].append((f, cam))

    changes = list(exact)
    for day, clips in sorted(groups.items()):
        clips.sort(key=lambda c: (c[1], c[0].name))
        new = dt.datetime.combine(day, start)
        prev_cam = None
        for f, cam in clips:
            if prev_cam is not None:
                gap = cam - prev_cam
                new += gap if dt.timedelta(0) <= gap <= MAX_GAP else RESET_GAP
            prev_cam = cam
            changes.append((f, cam, new))
    return changes


def apply_change(path, new_local):
    # The camera writes local wall-clock time with a 'Z' suffix, so match that
    # convention (no UTC conversion) - this keeps the time correct and makes a
    # re-run read back the same value, so the fix is idempotent.
    stamp = new_local.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    tmp = path.with_name(path.stem + ".fixing" + path.suffix)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0", "-c", "copy",
           "-map_metadata", "0", "-metadata", f"creation_time={stamp}",
           "-metadata:s", f"creation_time={stamp}", str(tmp)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")
    os.replace(tmp, path)
    ts = new_local.timestamp()
    os.utime(path, (ts, ts))


def media_scan(folders):
    if shutil.which("termux-media-scan"):
        for d in folders:
            subprocess.run(["termux-media-scan", "-r", str(d)], capture_output=True)
        print("Gallery refreshed.")
    elif Path("/data/data/com.termux").exists():
        print("Install Termux:API (pkg install termux-api) to refresh the gallery,"
              " or restart the phone.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folders", nargs="*", type=Path,
                   help="folders with clips (default: ShotKam folders on the phone)")
    p.add_argument("--date", type=dt.date.fromisoformat,
                   help="real shooting date YYYY-MM-DD for all clips")
    p.add_argument("--start", default="09:00",
                   help="time of the first clip of a day, HH:MM (default 09:00)")
    p.add_argument("--add-years", type=int, metavar="N",
                   help="fixed-lag mode: add N years to lagged clips (e.g. 8 for a 2018 camera)")
    p.add_argument("--window", type=int, default=3, metavar="W",
                   help="fixed-lag mode: how many lag years to accept, from the set year up "
                        "(default 3, e.g. 2018-2020 for a 2018 camera)")
    p.add_argument("--since", type=int, default=2026, metavar="YEAR",
                   help="fixed-lag mode: real year in which this lag was first used "
                        "(default 2026; use 2034 with --add-years 12)")
    p.add_argument("--apply", action="store_true", help="change the files")
    args = p.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. On Termux: pkg install ffmpeg")
    start = dt.time.fromisoformat(args.start)

    folders = args.folders or find_default_folders()
    if not folders:
        sys.exit("No folder given and no ShotKam folder found. "
                 "Run termux-setup-storage, or give the folder path.")
    files = sorted(f for d in folders for f in d.rglob("*")
                   if f.suffix.lower() == ".mp4" and ".fixing" not in f.name)
    print(f"Checking {len(files)} clips in: " + ", ".join(map(str, folders)))

    skipped = []
    if args.add_years is not None:
        if args.add_years <= 0:
            sys.exit("--add-years needs a positive number (e.g. 8).")
        if args.window <= 0:
            sys.exit("--window needs a positive number (e.g. 3).")
        changes, skipped = plan_fixed_offset(files, args.add_years, args.window, args.since)
    else:
        changes = plan(files, args.date, start)
    for f, cam, reason in skipped:
        print(f"  SKIP {f.name}: recorded {cam:%Y-%m-%d} - {reason}.")
    if not changes:
        print("All dates are correct. Nothing to do."
              + (f" ({len(skipped)} skipped - see above.)" if skipped else ""))
        return
    for f, cam, new in changes:
        print(f"  {f.name:28} {cam:%Y-%m-%d %H:%M} -> {new:%Y-%m-%d %H:%M}")

    if not args.apply:
        print(f"\n{len(changes)} clips need a fix. Run again with --apply to fix them.")
        return
    failed = 0
    for f, _, new in changes:
        try:
            apply_change(f, new)
        except Exception as e:  # keep going with the other clips
            failed += 1
            print(f"  FAILED {f.name}: {e}")
    print(f"\nFixed {len(changes) - failed} clips, {failed} failed.")
    media_scan(folders)


if __name__ == "__main__":
    main()
