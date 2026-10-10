#!/usr/bin/env bash
# Builds dist/Sundown-x86_64.AppImage. Needs: python3 with PySide6 and pyinstaller, appimagetool in PATH.
set -euo pipefail
cd "$(dirname "$0")/.."

pyinstaller --noconfirm --clean --windowed --name sundown \
    --add-data sundown/assets:sundown/assets \
    sundown.py

APPDIR=build/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
cp -r dist/sundown/. "$APPDIR/"
cp packaging/sundown.desktop "$APPDIR/sundown.desktop"
cp sundown/assets/sundown.svg "$APPDIR/sundown.svg"
cp sundown/assets/sundown.svg "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/sundown" "$@"
RUN
chmod +x "$APPDIR/AppRun"

ARCH=x86_64 appimagetool --no-appstream "$APPDIR" dist/Sundown-x86_64.AppImage
