# Blackout

A tiny app that turns your screens pure black (`#000000`) — ideal for OLED displays.
It covers the entire display (menu bar/notch on macOS, taskbar on Windows), hides the cursor,
and shows a brief "Press escape to exit" hint that fades away.

Opening Blackout blacks out immediately. Afterwards it stays in the menu bar (macOS) / system tray
(Windows) so you can black out again with a global hotkey.

| | macOS | Windows |
|---|---|---|
| Black out / end | **⌃⌥⌘B** | **Ctrl+Alt+B** (or left-click the tray icon) |
| End blackout | Escape or ⌘Q | Escape or Alt+F4 |
| Options & quit | menu bar icon | right-click the tray icon |

## Options

All saved between launches:

- **Displays** — choose which monitors to black out (e.g. only the second one while you work on the main one).
- **Auto-exit** — end the blackout after 15 min – 4 hours.
- **Keep display awake** *(on by default)* — prevents display/system sleep while blacked out.
- **Exit on mouse move or any key** — screensaver-style.
- **Show clock on mouse move** — briefly shows the time on the screen under the cursor, then fades back to black.
- **End blackout when the computer locks** *(on by default)*.
- **Start at login** — launches into the tray/menu bar at sign-in without blacking out.

## Download

Grab the latest zip for your platform from [Releases](../../releases).

- **macOS:** unzip and move `Blackout.app` to Applications. Unless the release is notarized, right-click → Open the first time.
- **Windows:** unzip and run `Blackout.exe`. SmartScreen may warn because it's unsigned — choose *More info → Run anyway*.

## Build

**macOS** (Xcode command line tools, macOS 12+, universal binary):

```sh
macos/build.sh             # builds macos/Blackout.app
macos/build.sh --install   # also copies it to /Applications
VERSION=1.2.0 macos/build.sh --package   # writes dist/Blackout-1.2.0-macos.zip
```

Set `SIGN_IDENTITY` to sign with a Developer ID (hardened runtime), and `NOTARY_PROFILE` to notarize when packaging.

**Windows** (no SDK needed — uses the C# compiler built into Windows; Windows 10/11):

```powershell
powershell -ExecutionPolicy Bypass -File windows\build.ps1             # builds windows\Blackout.exe
powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Install    # also installs + adds a Start Menu shortcut
powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Version 1.2.0 -Package   # writes dist\Blackout-1.2.0-windows.zip
```

## Releases

GitHub Actions builds both platforms on every push and PR. To publish a release, push a version tag:

```sh
git tag v1.0.0 && git push origin v1.0.0
```

For signed + notarized macOS builds, add the secrets listed at the top of `.github/workflows/build.yml`.
