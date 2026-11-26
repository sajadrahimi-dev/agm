#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/agm"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required."
    echo "  Linux : sudo apt install python3"
    echo "  Termux: pkg install python"
    exit 1
fi

chmod +x "$SRC"

if [[ "${PREFIX:-}" == *"com.termux"* ]] || [[ -d "/data/data/com.termux" ]]; then
    TARGET_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
    ENV_NAME="Termux"
else
    TARGET_DIR="$HOME/.local/bin"
    ENV_NAME="Linux"
fi

mkdir -p "$TARGET_DIR"
ln -sf "$SRC" "$TARGET_DIR/agm"

echo "✓ Installed agm for $ENV_NAME → $TARGET_DIR/agm"

case ":$PATH:" in
    *":$TARGET_DIR:"*)
        ;;
    *)
        echo "! Add to ~/.bashrc or ~/.zshrc:"
        echo "    export PATH=\"$TARGET_DIR:\$PATH\""
        ;;
esac

if [ -t 0 ] && [ -t 1 ]; then
    exec "$TARGET_DIR/agm"
fi
