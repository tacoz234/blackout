#!/bin/sh
# Builds Blackout.app (universal, macOS 12+).
#   VERSION=1.2.3 ./build.sh   stamp a version (default 0.0.0-dev)
#   ./build.sh --package       also write dist/Blackout-<version>-macos.zip
#   ./build.sh --install       also copy it to /Applications
# Signing is ad-hoc unless SIGN_IDENTITY="Developer ID Application: ..." is set.
# With NOTARY_PROFILE set (from `xcrun notarytool store-credentials`), --package also
# notarizes and staples the app so Gatekeeper opens it without warnings.
set -e
cd "$(dirname "$0")"
VERSION="${VERSION:-0.0.0-dev}"
APP=Blackout.app
PLIST="$APP/Contents/Info.plist"

rm -rf "$APP" build && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build
cp Info.plist "$PLIST"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "${VERSION%%-*}" "$PLIST"

swift make_icon.swift build/Blackout.iconset
iconutil -c icns build/Blackout.iconset -o "$APP/Contents/Resources/AppIcon.icns"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos12.0" main.swift -o "build/Blackout-$arch"
done
lipo -create build/Blackout-arm64 build/Blackout-x86_64 -output "$APP/Contents/MacOS/Blackout"

if [ -n "$SIGN_IDENTITY" ]; then
  codesign --force --options runtime --timestamp -s "$SIGN_IDENTITY" "$APP"
else
  codesign --force -s - "$APP"
fi
echo "Built $(pwd)/$APP ($VERSION)"

for arg in "$@"; do
  case "$arg" in
    --package)
      mkdir -p ../dist
      ZIP="../dist/Blackout-$VERSION-macos.zip"
      rm -f "$ZIP" && ditto -c -k --keepParent "$APP" "$ZIP"
      if [ -n "$NOTARY_PROFILE" ]; then
        xcrun notarytool submit "$ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
        xcrun stapler staple "$APP"
        rm -f "$ZIP" && ditto -c -k --keepParent "$APP" "$ZIP"
      fi
      echo "Packaged $ZIP"
      ;;
    --install)
      pkill -x Blackout || true
      rm -rf /Applications/Blackout.app && cp -R "$APP" /Applications/
      echo "Installed to /Applications/Blackout.app"
      ;;
  esac
done
