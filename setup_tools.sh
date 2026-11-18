#!/usr/bin/env bash
# Installs the build tools into $TOOLS (default: .tools next to this script).
# Gets a JDK 17 only if no java is on the PATH. Safe to run again.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
T="${TOOLS:-$ROOT/.tools}"
mkdir -p "$T" && cd "$T"

if ! command -v java >/dev/null && [ ! -x jdk/bin/java ]; then
  echo "JDK 17"
  curl -fsSL -o jdk.tar.gz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
  mkdir -p jdk && tar -xzf jdk.tar.gz -C jdk --strip-components=1 && rm jdk.tar.gz
fi
[ -x jdk/bin/java ] && export JAVA_HOME="$T/jdk" PATH="$T/jdk/bin:$PATH"

SDK="$T/android-sdk"
if [ ! -x "$SDK/build-tools/35.0.0/apksigner" ] || [ ! -f "$SDK/platforms/android-35/android.jar" ]; then
  echo "Android SDK (build-tools 35, platform 35)"
  if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
    mkdir -p "$SDK/cmdline-tools"
    curl -fsSL -o clt.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
    python3 -c "import zipfile; zipfile.ZipFile('clt.zip').extractall('$SDK/cmdline-tools')"
    rm -rf "$SDK/cmdline-tools/latest" && mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
    chmod +x "$SDK/cmdline-tools/latest/bin/"* && rm clt.zip
  fi
  yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" --licenses >/dev/null || true
  "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" "build-tools;35.0.0" "platforms;android-35" >/dev/null
fi

[ -f apktool.jar ]   || curl -fsSL -o apktool.jar   https://github.com/iBotPeaches/Apktool/releases/download/v2.10.0/apktool_2.10.0.jar
[ -f APKEditor.jar ] || curl -fsSL -o APKEditor.jar https://github.com/REAndroid/APKEditor/releases/download/V1.4.1/APKEditor-1.4.1.jar
echo "Tools ready in $T"
