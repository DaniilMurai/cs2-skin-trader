#!/bin/sh
# Renders the plist template for this checkout and loads it. Run from the repo root.
set -e
repo=$(pwd)
uv_bin=$(command -v uv)
mkdir -p data/logs
out="$HOME/Library/LaunchAgents/com.cs2trader.collect.plist"
sed -e "s|@REPO@|$repo|g" -e "s|@UV@|$uv_bin|g" ops/com.cs2trader.collect.plist.template > "$out"
launchctl bootout "gui/$(id -u)/com.cs2trader.collect" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$out"
launchctl print "gui/$(id -u)/com.cs2trader.collect" | head -20
