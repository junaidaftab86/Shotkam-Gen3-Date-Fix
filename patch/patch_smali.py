#!/usr/bin/env python3
"""Insert the LagFix calls into the decoded ShotKam app. Usage: patch_smali.py <apktool dir>

Each patch must match exactly once, else the script stops: a new app version
then needs a review of the hook points.
"""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
PKG = "com/shotkam/android"
LAGFIX = "Lcom/shotkam/android/LagFix;"


def find(rel):
    hits = list(ROOT.glob(f"smali*/{PKG}/{rel}"))
    if len(hits) != 1:
        sys.exit(f"expected 1 file for {rel}, found {hits}")
    return hits[0]


def patch(rel, old, new, what):
    f = find(rel)
    text = f.read_text()
    n = text.count(old)
    if n != 1:
        sys.exit(f"{what}: expected 1 match in {f}, found {n}")
    f.write_text(text.replace(old, new))
    print(f"   {what}: {f.relative_to(ROOT)}")


# 1. Camera clock: real time minus LAG years.
patch("ui/scope/ConnectionViewModel$setInfoSettings$1.smali",
      "invoke-static {}, Ljava/util/Calendar;->getInstance()Ljava/util/Calendar;",
      f"invoke-static {{}}, {LAGFIX}->now()Ljava/util/Calendar;",
      "clock")

# 2. Every camera date (clip time, folder name) gets LAG years added back.
patch("utils/TimeUtilsKt.smali",
      """    invoke-virtual {p0}, Ljava/util/Date;->getTime()J

    move-result-wide p0

    return-wide p0""",
      f"""    invoke-virtual {{p0}}, Ljava/util/Date;->getTime()J

    move-result-wide p0

    invoke-static {{p0, p1}}, {LAGFIX}->shiftParsed(J)J

    move-result-wide p0

    return-wide p0""",
      "camera dates")

# 3a. Clip saved straight into the gallery: fix the MP4 header before is_pending=0.
C = "Lcom/shotkam/android/data/repository/storage/StorageRepositoryImpl$storeVideo$2$1;"
patch("data/repository/storage/StorageRepositoryImpl$storeVideo$2$1.smali",
      """    const-string v0, "storeVideo(): md5sum passed"

    invoke-static {v3, v0}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I
""",
      f"""    const-string v0, "storeVideo(): md5sum passed"

    invoke-static {{v3, v0}}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    iget-object v0, p0, {C}->this$0:Lcom/shotkam/android/data/repository/storage/StorageRepositoryImpl;

    invoke-static {{v0}}, Lcom/shotkam/android/data/repository/storage/StorageRepositoryImpl;->access$getContentResolver$p(Lcom/shotkam/android/data/repository/storage/StorageRepositoryImpl;)Landroid/content/ContentResolver;

    move-result-object v0

    iget-object v2, p0, {C}->$storedUri:Landroid/net/Uri;

    invoke-static {{v0, v2}}, {LAGFIX}->fixMp4(Landroid/content/ContentResolver;Landroid/net/Uri;)V
""",
      "gallery save")

# 3b. Clip saved in app storage first (copied to the gallery later): fix the file.
C = "Lcom/shotkam/android/data/repository/storage/StorageRepositoryImpl$storeVideoInInternalStorage$2$1;"
patch("data/repository/storage/StorageRepositoryImpl$storeVideoInInternalStorage$2$1.smali",
      """    const-string p1, "storeVideoInInternalStorage(): md5sum passed"

    invoke-static {v3, p1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I
""",
      f"""    const-string p1, "storeVideoInInternalStorage(): md5sum passed"

    invoke-static {{v3, p1}}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    iget-object p1, p0, {C}->$videoFile:Ljava/io/File;

    invoke-static {{p1}}, {LAGFIX}->fixFile(Ljava/io/File;)V
""",
      "internal save")
