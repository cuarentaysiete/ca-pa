#!/bin/bash
# Convert the PNG app icon into the macOS .icns that the bundle uses.
#
# The repo already ships a small 32px ca-pa_icon.icns so the bundle works out
# of the box.  This optional step rebuilds it at full resolution (16-1024px)
# for a crisp Retina icon, using the stock macOS tools (sips + iconutil).
# Run it on a Mac, before building with PyInstaller.  Idempotent.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/src/ca_pa/resources/ca-pa_icon.png"
OUT="$HERE/src/ca_pa/resources/ca-pa_icon.icns"

if [[ ! -f "$SRC" ]]; then
    echo "error: $SRC not found" >&2
    exit 1
fi
if ! command -v sips >/dev/null 2>&1 || ! command -v iconutil >/dev/null 2>&1; then
    echo "error: sips/iconutil not found -- run this on macOS" >&2
    exit 1
fi

ICONSET="$(mktemp -d)/ca-pa.iconset"
mkdir -p "$ICONSET"

# Standard iconset: for each base size, a 1x and a @2x (double) rendition.
for base in 16 32 128 256 512; do
    dbl=$((base * 2))
    sips -z "$base" "$base" "$SRC" --out "$ICONSET/icon_${base}x${base}.png" >/dev/null
    sips -z "$dbl" "$dbl" "$SRC" --out "$ICONSET/icon_${base}x${base}@2x.png" >/dev/null
done

iconutil -c icns "$ICONSET" -o "$OUT"
rm -rf "$(dirname "$ICONSET")"
echo "wrote $OUT"
