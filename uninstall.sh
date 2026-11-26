#!/usr/bin/env bash
set -euo pipefail

if [[ "${PREFIX:-}" == *"com.termux"* ]] || [[ -d "/data/data/com.termux" ]]; then
    TARGET_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin/agm"
else
    TARGET_BIN="$HOME/.local/bin/agm"
fi

if [[ -L "$TARGET_BIN" || -f "$TARGET_BIN" ]]; then
    rm -f "$TARGET_BIN"
    echo "✓ Removed $TARGET_BIN"
else
    echo "• $TARGET_BIN not found"
fi

if [[ -d "$HOME/.agm" ]]; then
    rm -rf "$HOME/.agm"
    echo "✓ Removed $HOME/.agm"
fi

echo "✓ AGM uninstalled."
