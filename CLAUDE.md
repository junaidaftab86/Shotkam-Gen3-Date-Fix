# CLAUDE.md

Notes for coding agents. Read `README.md` first. This file adds only what an agent needs to avoid breaking the build or the camera.

## What this repo is

A patch for the official ShotKam Android app (`com.shotkam.android`, tested with 4.5.1 / versionCode 124) for the ShotKam Gen 3 clock fault. The patched app runs the camera clock `LAG` years behind and adds `LAG` back to every date it reads.

**This repo is public. Never commit ShotKam's code:** no APK (`upstream/` is ignored), no build output, no decompiled sources, no keystores. `patch_smali.py` holds only the short smali lines that each hook must match.

## Layout

| Path | Role |
|---|---|
| `patch/LagFix/com/shotkam/android/LagFix.java` | All workaround logic. Plain Java 8, Android APIs only. |
| `patch/patch_smali.py` | Inserts 4 calls to `LagFix` into the decoded app. Each hook must match exactly once. |
| `build.sh` / `setup_tools.sh` | Merge splits → apktool decode → patch → compile LagFix to dex → rebuild → sign. |
| `scripts/` | Python tools for the camera (Ambarella JSON, `192.168.42.1`, ports 7878/8787) and for USB-copied clips. |
| `camera/autoexec.ash.example` | Boot script example. |

## Rules

- **`LAG` is a multiple of 4** (keeps leap years aligned). Change `LAG` and `LAG_SINCE` together (e.g. 12 and 2034). Keep `fix_shotkam_dates.py --since` in step.
- **2017 is the reset year.** Never shift it: `FIRST_LAGGED_YEAR` is at least 2018.
- **LagFix methods never throw.** The hooks run inside the app's save path. Catch everything, log with tag `LagFix`. Because failures are silent, test every write path.
- **Channels:** a `FileInputStream` channel is read-only and a `FileOutputStream` channel is write-only. For a `ParcelFileDescriptor`, use one of each on the same descriptor (`patch(rd, wr)`). `RandomAccessFile` channels can do both.
- **Camera time is local time written as UTC** (`Z` suffix). Shift MP4 times in UTC, not in the phone's zone.
- **MD5:** the app checks the camera's MD5 once, before the save hooks. Re-check this if the app version changes.
- **New app version:** if `patch_smali.py` stops with a match error, review the hook points in the new smali. Don't loosen the "exactly once" check.
- **Card: FAT32, 32 KB clusters, no volume label.** A label `SHOTKAM` clashes with the `SHOTKAM` folder: `CD`/`LS` there return `rval -1`, and the videos screen loops on "loading". Check the root listing (`scripts/ls_test.py`) before you debug the patch for that symptom.
- **Never put `readl` or other memory commands in `autoexec.ash`.** A bad address boot-loops the camera. Recovery: delete `autoexec.ash` while the drive shows on the PC.
- Don't recommend the official app next to the patched one: it sets the real year and the clock resets at the next power-off.

## Test

- Check the hooks without a full build: decode with `apktool d -r` and run `patch_smali.py` on the copy.
- Test `LagFix` on the JVM (with `android.jar` on the classpath) on MP4s made with
  `ffmpeg -f lavfi -i testsrc=d=1 -c:v libx264 -metadata creation_time=2018-11-18T00:08:12Z -metadata:s creation_time=2018-11-18T00:08:12Z a.mp4`.
  Expect 6 changed fields per clip (mvhd, tkhd, mdhd × 2), 0 for a 2017 clip, and 0 on a second run.
- On a phone: download one clip in the app, then `ffprobe -show_entries format_tags=creation_time` must show the real year.
- Camera tests without a PC on the camera Wi-Fi: phone on USB adb and on the camera Wi-Fi, then pipe JSON through `nc 192.168.42.1 7878` in `adb shell` (work in `/data/local/tmp`; SELinux blocks `mkfifo` there).
