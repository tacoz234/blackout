#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
version=${VERSION:-$(cat linux/VERSION)}
case "$version" in *[!a-zA-Z0-9.+-]*|'') echo 'Invalid VERSION' >&2; exit 2;; esac
case "${1:-}" in
  --install)
    data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
    bin_home="$HOME/.local/bin"
    mkdir -p "$data_home/blackout" "$bin_home" "$data_home/applications"
    install -m644 linux/blackout.py linux/desktop.py linux/updater.py "$data_home/blackout/"
    printf '%s\n' "$version" > "$data_home/blackout/VERSION"
    /usr/bin/python3 - "$data_home" "$bin_home" "${2:-}" <<'PY'
from pathlib import Path
import shlex
import sys
data, binaries = map(Path, sys.argv[1:3])
script = data / 'blackout/blackout.py'
sys.path.insert(0, str(script.parent))
import desktop
launcher = binaries / 'blackout'
launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(script)) + ' "$@"\n')
launcher.chmod(0o755)
(data / 'applications/io.github.tacoz234.Blackout.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Blackout\n'
    'Comment=Turn selected displays pure black\n'
    f'Exec={desktop.desktop_exec([launcher])}\nIcon=weather-clear-night\nTerminal=false\nCategories=Utility;\n')
if sys.argv[3] == '--autostart':
    desktop.set_autostart(True, script)
elif sys.argv[3]:
    raise SystemExit('Usage: linux/build.sh --install [--autostart]')
PY
    echo "Installed. Launch Blackout from your applications menu or $bin_home/blackout"
    ;;
  --package)
    mkdir -p dist
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    mkdir -p "$stage/linux"
    cp linux/blackout.py linux/desktop.py linux/updater.py linux/build.sh "$stage/linux/"
    cp README.md "$stage/"
    printf '%s\n' "$version" > "$stage/linux/VERSION"
    tar -czf "dist/Blackout-$version-linux.tar.gz" -C "$stage" linux README.md
    ;;
  '')
    /usr/bin/python3 -c "import ast; from pathlib import Path; [ast.parse(p.read_text()) for p in Path('linux').glob('*.py')]"
    echo 'Run: /usr/bin/python3 linux/blackout.py'
    ;;
  *) echo "Usage: $0 [--install [--autostart]|--package]" >&2; exit 2;;
esac
