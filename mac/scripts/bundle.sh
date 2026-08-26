#!/usr/bin/env bash
# Builds SubstrateMac and wraps it in a minimal .app so macOS treats it as a real GUI app.
# No signing, no notarization — this is a local build.
set -euo pipefail

cd "$(dirname "$0")/.."

swift build -c release --product SubstrateMac
BIN="$(swift build -c release --show-bin-path)/SubstrateMac"

APP="Substrate.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Substrate"
chmod +x "$APP/Contents/MacOS/Substrate"

# Icon. macOS wants a multi-resolution .icns, so build one from the single source PNG with
# sips + iconutil — both ship with macOS, so this adds no dependency. Every size is resampled
# from the original rather than from a previous downscale, which keeps the small ones sharp.
ICON_SRC="Substrate-icon.png"
if [[ -f "$ICON_SRC" ]]; then
    ICONSET="$(mktemp -d)/AppIcon.iconset"
    mkdir -p "$ICONSET"
    for spec in 16:16x16 32:16x16@2x 32:32x32 64:32x32@2x 128:128x128 256:128x128@2x \
                256:256x256 512:256x256@2x 512:512x512 1024:512x512@2x; do
        px="${spec%%:*}"; name="${spec#*:}"
        sips -s format png -z "$px" "$px" "$ICON_SRC" --out "$ICONSET/icon_$name.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
    rm -rf "$(dirname "$ICONSET")"
else
    echo "warning: $ICON_SRC missing — building without an icon" >&2
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                 <string>Substrate</string>
    <key>CFBundleDisplayName</key>          <string>Substrate</string>
    <key>CFBundleIdentifier</key>           <string>dev.substrate.mac</string>
    <key>CFBundleExecutable</key>           <string>Substrate</string>
    <key>CFBundleIconFile</key>             <string>AppIcon</string>
    <key>CFBundleIconName</key>             <string>AppIcon</string>
    <key>CFBundlePackageType</key>          <string>APPL</string>
    <key>CFBundleShortVersionString</key>   <string>0.1.0</string>
    <key>CFBundleVersion</key>              <string>1</string>
    <key>LSMinimumSystemVersion</key>       <string>26.0</string>
    <key>NSHighResolutionCapable</key>      <true/>
    <key>NSPrincipalClass</key>             <string>NSApplication</string>
    <!-- The backend is http://localhost:8000; this is the narrow ATS exemption for it. -->
    <key>NSAppTransportSecurity</key>
    <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

echo "Built $(pwd)/$APP"
