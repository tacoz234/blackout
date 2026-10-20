# Blackout

A tiny macOS app that turns every screen pure black (`#000000`) — ideal for OLED displays.
It covers the entire display, including the menu bar and notch area, hides the Dock and cursor,
and shows a brief "Press escape to exit" hint that fades away.

**Exit:** Escape (or ⌘Q).

## Build

```sh
./build.sh            # builds Blackout.app
./build.sh --install  # builds and copies it to /Applications
```

Requires Xcode command line tools. Runs on macOS 12+ (Apple Silicon and Intel).
