#!/bin/bash
# ===========================================================================
# Protein Ledger -- double-click installer for macOS (GFP-161).
#
# WHY THIS FILE EXISTS. install.sh works perfectly, but Finder does not run a
# .sh on a double-click -- it opens it in a text editor, or asks what to open
# it with. So the documented path was "open Terminal, cd into the folder, run
# ./install.sh", which is three unfamiliar steps before anything happens.
#
# macOS DOES run a .command in Terminal on a double-click. This is the same
# installer, reachable the way a non-technical user expects.
#
# macOS has no equivalent of PowerShell's execution policy, so unlike the
# Windows Install.cmd this wrapper is not working around a security control --
# it exists purely because of how Finder treats file extensions.
#
# THE QUARANTINE FLAG IS PASSED DELIBERATELY. install.sh will not strip
# quarantine on its own, and that is the right default for a script somebody
# ran on purpose from a terminal. This path is different: it is the
# double-click route, aimed at someone who will otherwise meet
# "Apple could not verify Protein Ledger is free of malware" and have no way
# forward except System Settings > Privacy & Security > Open Anyway. Clearing
# quarantine on THE APP THIS INSTALLER JUST INSTALLED, at the user's explicit
# request to install it, is within what they asked for -- and it is announced
# below rather than done quietly.
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")"

echo
echo "  Installing Protein Ledger..."
echo
echo "  This will also tell macOS to trust the app it installs, so it opens"
echo "  without a security warning. The app is not signed by Apple, which is"
echo "  why macOS would otherwise block it."
echo

./install.sh --clear-quarantine "$@"
STATUS=$?

echo
if [ "$STATUS" -ne 0 ]; then
    echo "  The installer reported a problem (exit code $STATUS)."
    echo "  The messages above say what went wrong."
fi
echo "  You can close this window."
echo
# Without this the Terminal window can close before anything is read.
read -r -p "  Press Return to finish. " _ || true
exit "$STATUS"
