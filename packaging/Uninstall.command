#!/bin/bash
# Protein Ledger -- double-click uninstaller for macOS (GFP-161).
# Same reasoning as Install.command: Finder will not run a .sh on a
# double-click, so removing the app should not require a terminal either.
set -euo pipefail
cd "$(dirname "$0")"
echo
echo "  Uninstalling Protein Ledger..."
echo
./uninstall.sh "$@"
STATUS=$?
echo
read -r -p "  Press Return to finish. " _ || true
exit "$STATUS"
