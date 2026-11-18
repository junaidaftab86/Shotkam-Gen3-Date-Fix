# Shotkam-Gen3-Date-Fix

The ShotKam Gen 3 has a hard limit on the year: it keeps only 2017–2025. After 2025 the camera cannot keep 2026 and falls back to its firmware date, 01-01-2017. This patch fixes that.

A patch for the official ShotKam Android app that keeps the clock of a **ShotKam Gen 3** gun camera from resetting to 2017. You apply it to your own copy of the app. This repository contains no ShotKam code and no APK.

> Not affiliated with or endorsed by ShotKam. "ShotKam" is the name of their product and is used here only to say what this patch is for. Use at your own risk.

## The clock fault

| Item | Fact |
|---|---|
| Camera | ShotKam Gen 3: Ambarella A12, RTOS firmware (built October 2017). Wi-Fi `192.168.42.1`, control port 7878, data port 8787. |
| Fault | The camera keeps only the years **2017–2025** across a power-off. A later year resets the clock to 2017-01-01. |
| Trigger | The official app sets the real time (2026) each time it connects. |
| Workaround | Run the camera clock 8 years behind (2026 → 2018). Add the 8 years back to every clip date. |
| Limit | In 2034 the lagged clock reaches 2026. Then set `LAG = 12` and `LAG_SINCE = 2034` in `LagFix.java`, and empty the camera first. |

`LAG` is a multiple of 4, so the leap-year pattern stays the same.

The date stamp that the camera draws into the video shows the lagged year (2018). The patch cannot change those pixels. Set **Date stamp** to *Logo* or *Off* in the app settings if that matters to you.

On the card, folder names (`MM-DD-2018`), file times and MP4 headers keep the lagged year. The camera protocol has no rename command, so the app cannot change them. Clips that the app downloads get the real year. For clips copied by USB, run `scripts/fix_shotkam_dates.py <folder> --add-years 8 --apply`.

## What the patch changes

The app UI and all other features stay as they are. `patch/patch_smali.py` adds 4 calls to `patch/LagFix/com/shotkam/android/LagFix.java`:

| # | Hook in the app | Call | Result |
|---|---|---|---|
| 1 | `ConnectionViewModel.setInfoSettings` | `LagFix.now()` in place of `Calendar.getInstance()` | The camera clock is set to the real time minus 8 years. |
| 2 | `TimeUtilsKt.timeToLong` | `LagFix.shiftParsed()` | Camera dates (clip times, folder names) show the real year in the app and in the phone gallery entry. |
| 3 | `StorageRepositoryImpl.storeVideo` (after the MD5 check) | `LagFix.fixMp4()` | The saved clip's MP4 header (`mvhd`, `tkhd`, `mdhd`) has the real year. |
| 4 | `StorageRepositoryImpl.storeVideoInInternalStorage` (after the MD5 check) | `LagFix.fixFile()` | Same, for clips that the app keeps in app storage first. |

`LagFix` changes only years from `LAG_SINCE − LAG` (2018) to the current year − 8. Other dates stay as they are, including 2017 (the reset date). A second run changes nothing.

Clips recorded in 2018–2025 before you used the workaround would also get 8 years added if they are still on the camera. Empty the camera once before you use the patched app.

Tested with ShotKam **4.5.1** (versionCode 124) on a Samsung Galaxy S24 Ultra. Each hook must match exactly once. If a new app version changes the code, `patch_smali.py` stops with an error and the hooks need a review.

## Build

Needs Linux or WSL, Python 3, `curl`, `unzip`-capable Python and about 1 GB for the tools. `build.sh` installs JDK 17, Android build-tools 35, apktool and APKEditor into `.tools/` on the first run.

### 1. Get your copy of the app

Install ShotKam from the Play Store on an Android phone. **Do not open it** (it would set the real year on the camera). Copy the installed APK files with adb:

```sh
adb shell pm path com.shotkam.android     # lists base.apk and split_*.apk
adb pull <each path> splits/
```

Put all the `.apk` files into one ZIP file named `upstream/ShotKam.apks` (no compression is necessary). You need the ABI split (`split_config.arm64_v8a.apk` or similar): it holds libvlc. Without it, calibrate, test mode and playback crash.

### 2. Build

```sh
./build.sh                      # → ShotKam-lagfix.apk
```

`build.sh` merges the splits, decodes the app, applies the hooks, compiles `LagFix`, rebuilds and signs. On the first run it makes `lagfix.keystore` (password `lagfix`). Keep that file: Android installs an update only with the same key. To use another key, set `KEYSTORE` (path) or `LAGFIX_KEYSTORE_B64` (base64), and `LAGFIX_KS_PASS`.

**Do not publish the built APK.** It contains ShotKam's code.

## Install

1. Uninstall the official ShotKam app. The patched app has a different signature.
2. Install `ShotKam-lagfix.apk`. If Play Protect blocks it, use `adb install ShotKam-lagfix.apk`.
3. Connect to the camera as usual. The app sets the lagged clock.

Do not install the official app again. It sets the real year, and the clock resets at the next power-off.

## Camera card

Format the card only as FAT32 with 32 KB clusters, and **with no volume label**:

```powershell
format D: /FS:FAT32 /A:32K /Q
label D:        # press Enter for no name, then Y to delete the label
```

A FAT32 label is an entry in the root folder. If the label is `SHOTKAM`, the firmware finds the label before the `SHOTKAM` folder. Then `CD` and `LS` on `/tmp/SD0/SHOTKAM/` return `rval -1`, and the app's videos screen stays on "loading". Recording still works.

## Other tools

| Path | Use |
|---|---|
| `scripts/fix_shotkam_dates.py` | Add N years to clips copied by USB: `--add-years 8 --apply` (after 2034: `--add-years 12 --since 2034`). Needs ffmpeg. |
| `scripts/shotkam_sync.py` | Wi-Fi sync without the app (Termux or PC): set the clock, download, fix dates. Tested only against a mock camera. |
| `scripts/shotkam_probe.py` | Read or set the camera clock and settings over Wi-Fi. |
| `scripts/ls_test.py` | Read-only check of the camera `CD`/`LS` replies over Wi-Fi. Use it when the videos screen does not load. In the root listing, a folder ends with `/`. |
| `camera/autoexec.ash.example` | Set the clock at boot from the card root. Works only on a FAT32 card with 32 KB clusters. |

**Do not put `readl` or other memory commands in `autoexec.ash`.** A bad address makes the camera boot-loop. To recover, delete `autoexec.ash` while the drive shows on the PC.

## Protocol notes

The camera speaks the Ambarella JSON protocol: `257` start session, `258` stop session, `1`/`2` get/set setting (`camera_clock` as `YYYY-MM-DD HH:MM:SS`), `11` device info, `1282` LS, `1283` CD, `1285` get file (bytes on port 8787, then a `get_file_complete` notification with the MD5). Clips are in `/tmp/SD0/SHOTKAM/<MM-DD-YYYY>/`.

## License

MIT, see [LICENSE](LICENSE). The license covers the files in this repository only, not the ShotKam app.
