#!/bin/sh
# Builds Blackout.app (universal, macOS 12+). Pass --install to copy it to /Applications.
set -e
cd "$(dirname "$0")"
rm -rf Blackout.app build && mkdir -p Blackout.app/Contents/MacOS Blackout.app/Contents/Resources build
cp Info.plist Blackout.app/Contents/Info.plist
swift make_icon.swift build/Blackout.iconset
iconutil -c icns build/Blackout.iconset -o Blackout.app/Contents/Resources/AppIcon.icns
swiftc -O -target arm64-apple-macos12.0 main.swift -o build/Blackout-arm64
swiftc -O -target x86_64-apple-macos12.0 main.swift -o build/Blackout-x86_64
lipo -create build/Blackout-arm64 build/Blackout-x86_64 -output Blackout.app/Contents/MacOS/Blackout
codesign --force -s - Blackout.app
if [ "$1" = "--install" ]; then
  rm -rf /Applications/Blackout.app && cp -R Blackout.app /Applications/
  echo "Installed to /Applications/Blackout.app"
fi
