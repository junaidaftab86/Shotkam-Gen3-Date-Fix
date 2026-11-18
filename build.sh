#!/usr/bin/env bash
# Builds ShotKam-lagfix.apk: the official ShotKam app with the clock workaround.
#
#   ./build.sh [input]
#
# Input (default: the first that exists; see README):
#   upstream/ShotKam.apks   full split set exported from the phone (preferred)
#   upstream/ShotKam.apk    single APK (a base-only APK has no libvlc: video does not play)
#
# Signing key, first match:
#   $LAGFIX_KEYSTORE_B64    base64 of a PKCS12/JKS keystore (CI variable)
#   $KEYSTORE               path to a keystore
#   lagfix.keystore         made here on the first run (not in git)
# Keystore password and alias: $LAGFIX_KS_PASS (default lagfix), alias "lagfix".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
T="${TOOLS:-$ROOT/.tools}"
[ -x "$T/jdk/bin/java" ] && export JAVA_HOME="$T/jdk" PATH="$T/jdk/bin:$PATH"
SDK="${ANDROID_SDK:-$T/android-sdk}"
BT="$SDK/build-tools/35.0.0"
AJ="$SDK/platforms/android-35/android.jar"
W="$ROOT/work"
OUT="${OUT:-$ROOT/ShotKam-lagfix.apk}"
PASS="${LAGFIX_KS_PASS:-lagfix}"

IN="${1:-}"
if [ -z "$IN" ]; then
  for f in "$ROOT"/upstream/ShotKam.apks "$ROOT"/upstream/ShotKam.xapk "$ROOT"/upstream/ShotKam.apk; do
    [ -f "$f" ] && { IN="$f"; break; }
  done
fi
[ -n "$IN" ] && [ -f "$IN" ] || { echo "no input APK (put it in upstream/)"; exit 1; }
if head -c 100 "$IN" | grep -q 'git-lfs'; then
  echo "$IN is a Git LFS pointer: run 'git lfs pull'"; exit 1
fi
[ -f "$BT/apksigner" ] && [ -f "$T/apktool.jar" ] && [ -f "$T/APKEditor.jar" ] || TOOLS="$T" "$ROOT/setup_tools.sh"
IN="$(realpath "$IN")"
rm -rf "$W" && mkdir -p "$W"

echo "1/6 input: $IN"
case "$IN" in
  *.apks|*.xapk|*.apkm) java -jar "$T/APKEditor.jar" m -i "$IN" -o "$W/merged.apk" >/dev/null ;;
  *) cp "$IN" "$W/merged.apk" ;;
esac
if ! python3 -c "import sys,zipfile; sys.exit(not any(n.startswith('lib/') for n in zipfile.ZipFile('$W/merged.apk').namelist()))"; then
  echo "   WARNING: no native libraries (lib/). The video player needs libvlc from the abi split."
  echo "            Export the full split set from the phone into upstream/ShotKam.apks."
fi

echo "2/6 decode"
java -jar "$T/apktool.jar" d -f -o "$W/src" "$W/merged.apk" >/dev/null

echo "3/6 patch smali"
python3 "$ROOT/patch/patch_smali.py" "$W/src"
# A merged APK must not ask for split parts.
M="$W/src/AndroidManifest.xml"
sed -i -E 's/ android:requiredSplitTypes="[^"]*"//; s/ android:splitTypes="[^"]*"//; s/ android:isSplitRequired="[^"]*"//' "$M"
sed -i -E '/com\.android\.vending\.(splits|splits\.required|derived\.apk\.id)"/d' "$M"
sed -i -E '/com\.android\.stamp\.(source|type)"/d' "$M"
# apktool compresses lib/*.so. Then Android must extract them at install time.
if grep -q 'android:extractNativeLibs=' "$M"; then
  sed -i -E 's/android:extractNativeLibs="[^"]*"/android:extractNativeLibs="true"/' "$M"
else
  sed -i -E 's/<application /<application android:extractNativeLibs="true" /' "$M"
fi

echo "4/6 compile LagFix"
mkdir -p "$W/classes" "$W/dex"
javac -source 8 -target 8 -bootclasspath "$AJ" -Xlint:-options -d "$W/classes" \
  "$ROOT/patch/LagFix/com/shotkam/android/LagFix.java"
"$BT/d8" --min-api 28 --lib "$AJ" --output "$W/dex" $(find "$W/classes" -name '*.class')

echo "5/6 build"
java -jar "$T/apktool.jar" b -o "$W/unsigned.apk" "$W/src" >/dev/null
N=$(python3 -c "
import zipfile
names = zipfile.ZipFile('$W/unsigned.apk').namelist()
i = 2
while f'classes{i}.dex' in names: i += 1
print(i)")
cp "$W/dex/classes.dex" "$W/classes$N.dex"
python3 -c "
import zipfile
with zipfile.ZipFile('$W/unsigned.apk', 'a', zipfile.ZIP_STORED) as z: z.write('$W/classes$N.dex', 'classes$N.dex')"
echo "   LagFix added as classes$N.dex"

echo "6/6 align and sign"
KS="${KEYSTORE:-$ROOT/lagfix.keystore}"
if [ -n "${LAGFIX_KEYSTORE_B64:-}" ]; then
  KS="$W/ci.keystore"; echo "$LAGFIX_KEYSTORE_B64" | base64 -d > "$KS"
elif [ ! -f "$KS" ]; then
  [ -n "${CI:-}" ] && echo "   WARNING: no LAGFIX_KEYSTORE_B64 variable. A new key each build: updates will not install."
  keytool -genkeypair -keystore "$KS" -storepass "$PASS" -keypass "$PASS" -alias lagfix \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=ShotKam LagFix" >/dev/null 2>&1
fi
"$BT/zipalign" -f -p 4 "$W/unsigned.apk" "$W/aligned.apk"
"$BT/apksigner" sign --ks "$KS" --ks-pass "pass:$PASS" --key-pass "pass:$PASS" --out "$OUT" "$W/aligned.apk"
"$BT/apksigner" verify "$OUT"
echo "DONE: $OUT"
