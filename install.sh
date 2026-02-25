#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"

chmod +x "$SCRIPT_DIR/foreman-prepare.py"
chmod +x "$SCRIPT_DIR/foreman-run.py"
chmod +x "$SCRIPT_DIR/foreman-report.py"

ln -sf "$SCRIPT_DIR/foreman-prepare.py" "$BIN_DIR/foreman-prepare"
ln -sf "$SCRIPT_DIR/foreman-run.py"     "$BIN_DIR/foreman-run"
ln -sf "$SCRIPT_DIR/foreman-report.py"  "$BIN_DIR/foreman-report"

echo "Installed:"
echo "  foreman-prepare -> $BIN_DIR/foreman-prepare"
echo "  foreman-run     -> $BIN_DIR/foreman-run"
echo "  foreman-report  -> $BIN_DIR/foreman-report"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "Note: $BIN_DIR is not in your PATH."
    echo "Add this to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
