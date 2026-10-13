#!/usr/bin/env bash
# Build PaperTranslatorVI-x86_64.AppImage: a standalone Python + Qt GUI + this package.
# torch/transformers are not bundled; the app installs them on first use (see gui.py).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${BUILD:-$ROOT/build}"
APPDIR="$BUILD/AppDir"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.12.7%2B20241016-x86_64-unknown-linux-gnu-install_only.tar.gz"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
OUT="${OUT:-$BUILD/PaperTranslatorVI-x86_64.AppImage}"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr" "$BUILD"

echo "==> Python"
[ -f "$BUILD/python.tar.gz" ] || curl -fL -o "$BUILD/python.tar.gz" "$PY_URL"
tar xzf "$BUILD/python.tar.gz" -C "$APPDIR/usr"
PY="$APPDIR/usr/python/bin/python3"

echo "==> App + Qt"
"$PY" -m pip install --no-cache-dir --disable-pip-version-check "$ROOT[gui]"

echo "==> Prune unused Qt parts"
QT="$("$PY" -c 'import os, PySide6; print(os.path.dirname(PySide6.__file__))')"
rm -rf "$QT"/{designer,linguist,assistant,lupdate,lrelease,qmllint,qmlformat,qmlls,qml,Qt/qml,typesystems,include,glue,scripts}
rm -rf "$QT"/Qt/lib/libQt6{Quick,Qml,Designer,Pdf,ShaderTools,VirtualKeyboard}*
find "$QT"/Qt/translations -type f ! -name 'qtbase_vi*' ! -name 'qt_vi*' -delete 2>/dev/null || true
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$APPDIR"/usr/python/lib/python3.12/test

echo "==> AppDir metadata"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
unset PYTHONHOME PYTHONPATH
if [ -z "${SSL_CERT_FILE:-}" ]; then
  for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
    [ -f "$f" ] && export SSL_CERT_FILE="$f" && break
  done
fi
cd "$HOME" 2>/dev/null || true
exec "$HERE/usr/python/bin/python3" -s -m paper_translator.gui "$@"
RUN
chmod +x "$APPDIR/AppRun"
cp "$ROOT/paper_translator/icon.svg" "$APPDIR/paper-translator-vi.svg"
cp "$ROOT/paper_translator/icon.svg" "$APPDIR/.DirIcon"
cat > "$APPDIR/paper-translator-vi.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Paper Translator VI
Comment=Dịch paper arXiv sang tiếng Việt, xuất thẳng PDF
Exec=paper-translator-vi %f
Icon=paper-translator-vi
Categories=Office;Education;
Terminal=false
DESK

echo "==> AppImage"
[ -x "$BUILD/appimagetool" ] || { curl -fL -o "$BUILD/appimagetool" "$TOOL_URL"; chmod +x "$BUILD/appimagetool"; }
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$BUILD/appimagetool" --no-appstream "$APPDIR" "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
