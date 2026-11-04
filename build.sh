#!/bin/sh
set -eu
cd "$(dirname "$0")"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
android/gradlew -p android assembleDebug --console=plain
app='build/Nachim.app/Contents'
mkdir -p "$app/MacOS" "$app/Resources"
cp android/build/outputs/apk/debug/*-debug.apk "$app/Resources/player.apk"
cp mac/send.py mac/captions.py mac/commands.py mac/transport.py "$app/Resources/"
cp mac/FrameShift.icns "$app/Resources/"
cp mac/Info.plist "$app/"
MACOSX_DEPLOYMENT_TARGET=13.0 xcrun swiftc -parse-as-library -O mac/USBPlayer.swift mac/PlaybackTimeline.swift -o "$app/MacOS/Nachim"
codesign --force --sign - 'build/Nachim.app'
printf '%s\n' "Built $(pwd)/build/Nachim.app"
