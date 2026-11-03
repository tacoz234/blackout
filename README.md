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

**Gaming on another display:** if only some displays are blacked out, Blackout never takes focus,
so a fullscreen game on the uncovered display keeps running. End it with the hotkey, or click the black screen and press Escape.
Blackout uses essentially no CPU, whether idle in the tray/menu bar or blacked out: it doesn't poll, it just waits for events.

## Options

All saved between launches:

- **Displays** — choose which monitors to black out (e.g. only the second one while you work on the main one).
- **Auto-exit** — end the blackout after 15 min – 4 hours.
- **Keep display awake** *(on by default)* — prevents display/system sleep while blacked out.
- **Exit on mouse move or any key** — screensaver-style.
- **Show clock on mouse move** — briefly shows the time on the screen under the cursor, then fades back to black.
- **End blackout when the computer locks** *(on by default)*.
- **Start at login** — launches into the tray/menu bar at sign-in without blacking out.
- **Check for updates automatically** *(on by default)* — see below.

## Updates

Blackout checks GitHub for a new release shortly after it starts and once a day after that.
When one is out, **Update to Blackout x.y.z** appears at the top of the tray/menu-bar menu (Windows also shows a notification).
Click it and Blackout downloads the new version, checks it against the SHA-256 hash GitHub publishes for the release,
installs it in place, and restarts. You can also choose **Check for updates** from the menu at any time.
The only network request is to the GitHub API for this repository's latest release; turn off
**Check for updates automatically** to stop it. On macOS the app must be in a folder you can write to (like Applications).

## Download

Get the latest version from [Releases](../../releases). No command line needed.

**Windows:** download `Blackout-<version>-windows-setup.exe` and run it. It installs just for you (no admin prompt), adds Blackout to the Start Menu, and can start it when you sign in. Blackout then sits in the system tray; press **Ctrl+Alt+B** or click the tray icon to black out. Uninstall it from *Settings → Apps* like any other app.
Because the installer isn't code-signed, SmartScreen may say "Windows protected your PC". Click *More info → Run anyway*.
Prefer no installer? `Blackout-<version>-windows.zip` has the bare `Blackout.exe`.

**macOS:** download `Blackout-<version>-macos.dmg`, open it, and drag Blackout into Applications. Then open it from Launchpad or Spotlight. It lives in the menu bar; press **⌃⌥⌘B** to black out.
Unless the release is notarized, macOS blocks the first launch. On macOS 15 and later, go to *System Settings → Privacy & Security* and click *Open Anyway*. On older versions, right-click the app and choose *Open*.

## Build

**macOS** (Xcode command line tools, macOS 12+, universal binary):

```sh
macos/build.sh             # builds macos/Blackout.app
macos/build.sh --install   # also copies it to /Applications
VERSION=1.2.0 macos/build.sh --package   # writes dist/Blackout-1.2.0-macos.dmg
```

Set `SIGN_IDENTITY` to sign with a Developer ID (hardened runtime), and `NOTARY_PROFILE` to notarize when packaging.

**Windows** (no SDK needed — uses the C# compiler built into Windows; Windows 10/11):

```powershell
powershell -ExecutionPolicy Bypass -File windows\build.ps1             # builds windows\Blackout.exe
powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Install    # also installs + adds a Start Menu shortcut
powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Version 1.2.0 -Package     # writes dist\Blackout-1.2.0-windows.zip
powershell -ExecutionPolicy Bypass -File windows\build.ps1 -Version 1.2.0 -Installer   # writes dist\Blackout-1.2.0-windows-setup.exe (needs Inno Setup 6)
```

## Releases

GitHub Actions builds both platforms on every push and PR. To publish a release, push a version tag:

```sh
git tag v1.0.0 && git push origin v1.0.0
```

For signed + notarized macOS builds, add the secrets listed at the top of `.github/workflows/build.yml`.
