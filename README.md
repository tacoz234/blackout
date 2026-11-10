# Blackout

A tiny app that turns your screens pure black (`#000000`) — ideal for OLED displays.
It covers the entire display (menu bar/notch on macOS, taskbar on Windows), hides the cursor,
and shows a brief "Press escape to exit" hint that fades away.

Opening Blackout blacks out immediately. Afterwards it stays in the menu bar (macOS) / system tray
(Windows and Linux) so you can black out again with a global hotkey. See [Linux setup](#linux) below.

| | macOS | Windows / Linux |
|---|---|---|
| Black out / end | **⌃⌥⌘B** | **Ctrl+Alt+B** (or left-click the tray icon) |
| End blackout | Escape or ⌘Q | Escape or Alt+F4 |
| Options & quit | menu bar icon | right-click the tray icon |

**Gaming on another display:** if only some displays are blacked out, Blackout never takes focus,
so a fullscreen game on the uncovered display keeps running. End it with the hotkey, or click the black screen and press Escape.
Blackout uses essentially no CPU, whether idle in the tray/menu bar or blacked out: it doesn't poll, it just waits for events.

## Linux

The Linux app stays in the system tray after blackout ends. Closing its options
window hides it; **Quit Blackout** explicitly exits. **Ctrl+Alt+B** or left-clicking
the tray toggles blackout; right-click opens the full options menu. Escape ends
blackout. When only some monitors are covered, click a black screen before pressing
Escape, or use the global shortcut without leaving your game.

All options below are implemented on Linux, including the fading clock, automatic
updates, end-on-lock, and start-at-login. Settings are stored in
`$XDG_CONFIG_HOME/blackout/settings.json` (default `~/.config/blackout/settings.json`).

Install the runtime dependencies:

```sh
# Ubuntu / Debian
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-dbusmenu-gtk3-0.4 gir1.2-gtklayershell-0.1
# Fedora
sudo dnf install python3-gobject gtk3 libdbusmenu-gtk3 gtk-layer-shell
# Arch
sudo pacman -S python-gobject gtk3 libdbusmenu-gtk3 gtk-layer-shell
```

Install for your user and enable background startup at login:

```sh
./linux/build.sh --install --autostart
~/.local/bin/blackout --background
```

The installer adds an applications-menu entry. Launch normally to black out immediately;
`--background` starts silently in the tray. `--preferences` opens the controls,
`--toggle` toggles blackout, `--stop` ends it, `--status` reports integration status,
and `--quit` exits. All commands reach the same running instance. The tray's **Start at
login** checkbox controls an XDG autostart entry. The app waits for events rather than
polling for mouse movement, keyboard input, or lock state.

KDE uses its native global shortcut service. X11 sessions register Ctrl+Alt+B directly.
Other Wayland desktops use the global-shortcut portal, which may ask permission or
require configuring the binding. If registration is unavailable, bind
`~/.local/bin/blackout --toggle` in your desktop keyboard settings. Shortcut conflicts
are reported in Options. A desktop that supports StatusNotifierItem is required for
the tray; on GNOME, enable an AppIndicator/StatusNotifier extension, or open the
controls with `blackout --preferences`.

On KDE and compatible Wayland compositors, layer-shell places the black surface over
the entire chosen monitor without taking focus from a game on another monitor.
Clicking that surface explicitly gives it keyboard input. Compositors without
layer-shell use Xwayland when available. Keep-awake, lock reporting, tray behavior,
and shortcut permissions still depend on desktop services; identical behavior on
every Linux desktop cannot be guaranteed. Native KDE Wayland coverage, sleep-inhibitor
release, tray activation, shortcut dispatch, and fullscreen focus preservation have
been tested on a two-monitor setup, including a rotated display with fractional scaling.

Release archives named `Blackout-<version>-linux.tar.gz` contain the same source files;
extract one and run `./linux/build.sh --install`. Package with
`VERSION=1.2.0 ./linux/build.sh --package`. Linux updates require a per-user installation,
verify GitHub's SHA-256 digest, replace the app with rollback on failure, and restart
in the background. Development builds only check for updates when asked.

To uninstall, disable **Start at login**, run `blackout --quit`, and remove
`~/.local/bin/blackout`, `~/.local/share/blackout`, and
`~/.local/share/applications/io.github.tacoz234.Blackout.desktop` (adjust data paths if
you set `XDG_DATA_HOME`). Remove the autostart file
`~/.config/autostart/io.github.tacoz234.Blackout.desktop` if still present.

Linux regression tests (GTK, Xvfb, D-Bus, xdotool, desktop-file-utils required):

```sh
dbus-run-session -- xvfb-run -a /usr/bin/python3 -m unittest discover -s linux -p 'test_[bip]*.py' -v
# Optional live KDE test: briefly covers your displays and uses a fullscreen focus probe.
/usr/bin/python3 linux/test_kde.py
```

## Options

All saved between launches:

- **Displays** — choose which monitors to black out (e.g. only the second one while you work on the main one).
- **Auto-exit** — end the blackout after 15 min – 4 hours.
- **Keep display awake** *(on by default)* — prevents display/system sleep while blacked out.
- **Exit on mouse move or any key** — screensaver-style.
- **Show clock on mouse move** — briefly shows the time on the screen under the cursor, then fades back to black.
- **Use 24-hour clock** — choose `13:05` when enabled, or `1:05 PM` when disabled (the default), on all platforms.
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

GitHub Actions builds all three platforms on every push and PR. To publish a release, push a version tag:

```sh
git tag v1.0.0 && git push origin v1.0.0
```

For signed + notarized macOS builds, add the secrets listed at the top of `.github/workflows/build.yml`.
