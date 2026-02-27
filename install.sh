#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"

chmod +x "$SCRIPT_DIR/foreman-prepare.py"
chmod +x "$SCRIPT_DIR/foreman-run.py"
chmod +x "$SCRIPT_DIR/foreman-status.py"
chmod +x "$SCRIPT_DIR/foreman-add.py"

ln -sf "$SCRIPT_DIR/foreman-prepare.py" "$BIN_DIR/foreman-prepare"
ln -sf "$SCRIPT_DIR/foreman-run.py"     "$BIN_DIR/foreman-run"
ln -sf "$SCRIPT_DIR/foreman-status.py"  "$BIN_DIR/foreman-status"
ln -sf "$SCRIPT_DIR/foreman-add.py"     "$BIN_DIR/foreman-add"

echo "Installed:"
echo "  foreman-prepare -> $BIN_DIR/foreman-prepare"
echo "  foreman-run     -> $BIN_DIR/foreman-run"
echo "  foreman-status  -> $BIN_DIR/foreman-status"
echo "  foreman-add     -> $BIN_DIR/foreman-add"
echo "Also remember install the open-ralph-wiggum-v1.2.1-with-verbose!"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "Note: $BIN_DIR is not in your PATH."
    echo "Add this to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
