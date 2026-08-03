#!/bin/bash
# ===========================================================================
# Snapshot every macOS location this app can touch (GFP-116).
# ===========================================================================
# The install/uninstall lifecycle job diffs these snapshots. That diff is the
# actual evidence that an install put things where it said and an uninstall
# took them away again -- an assertion that merely says "something was
# installed" is worthless, because the whole point is to catch a WRONG PATH.
#
# It also generates the ground truth for GFP-102's manual removal checklist.
# That list currently exists because a human wrote it down; anything that
# appears in a diff here and not in UNINSTALL.md is a gap in the checklist,
# which is exactly the sort of thing nobody notices until an uninstall fails
# on a customer's machine and the documentation names the wrong folder.
#
# EVERY PATH IS PRINTED RESOLVED, never as a pattern. A log that says
# "~/Library/..." cannot be debugged without the machine it came from, and the
# machine it came from is a runner that no longer exists.
#
# Usage:  ./scripts/mac_snapshot.sh <output-file> [label]
# ===========================================================================
set -uo pipefail

OUT="${1:?usage: mac_snapshot.sh <output-file> [label]}"
LABEL="${2:-snapshot}"

{
    echo "=============================================================="
    echo "SNAPSHOT: $LABEL"
    echo "when:     $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "user:     $(id -un) (uid $(id -u))"
    echo "home:     $HOME"
    echo "=============================================================="
    echo

    # The app bundle is 40 MB of Qt; listing its contents would bury the diff
    # in thousands of unchanging lines. Presence plus a file count is what
    # actually distinguishes "installed", "not installed" and "half removed".
    APP="$HOME/Applications/Grocery Planner.app"
    echo "--- app bundle ---"
    if [ -d "$APP" ]; then
        echo "PRESENT  $APP  ($(find "$APP" -type f | wc -l | tr -d ' ') files, $(du -sh "$APP" | cut -f1))"
    else
        echo "ABSENT   $APP"
    fi
    echo

    # Everything else is small enough to list in full, and listing in full is
    # what catches a stray file nobody thought to look for -- SQLite's
    # -wal/-shm files and hand-made backups being the known examples.
    for dir in \
        "$HOME/.local/share/grocery-planner" \
        "$HOME/Library/Application Support/grocery-planner" \
        "$HOME/Library/Logs/grocery-planner"
    do
        echo "--- $dir ---"
        if [ -d "$dir" ]; then
            find "$dir" -mindepth 1 -print0 2>/dev/null \
                | sort -z \
                | while IFS= read -r -d '' entry; do
                    if [ -d "$entry" ]; then
                        echo "  dir   $entry"
                    else
                        echo "  file  $entry  ($(wc -c <"$entry" | tr -d ' ') bytes)"
                    fi
                done
        else
            echo "  ABSENT"
        fi
        echo
    done

    echo "--- individual files ---"
    for path in \
        "$HOME/.local/bin/gplan" \
        "$HOME/Library/LaunchAgents/com.grocery-planner.refresh.plist"
    do
        if [ -L "$path" ]; then
            echo "  symlink $path -> $(readlink "$path")"
        elif [ -f "$path" ]; then
            echo "  file    $path  ($(wc -c <"$path" | tr -d ' ') bytes)"
        else
            echo "  ABSENT  $path"
        fi
    done
    echo

    echo "--- saved application state ---"
    STATE="$HOME/Library/Saved Application State"
    if [ -d "$STATE" ]; then
        find "$STATE" -maxdepth 1 -name "com.grocery-planner*" -print 2>/dev/null \
            | sort | sed 's/^/  /'
        find "$STATE" -maxdepth 1 -name "com.grocery-planner*" 2>/dev/null \
            | grep -q . || echo "  none"
    else
        echo "  ABSENT  $STATE"
    fi
    echo

    # THE ONE THAT MATTERS MOST. A leftover LaunchAgent keeps FIRING against a
    # binary that no longer exists -- it is the only artifact that goes on
    # acting after the app is gone.
    echo "--- loaded launch agents (launchctl) ---"
    if launchctl list 2>/dev/null | grep -i grocery; then
        :
    else
        echo "  none loaded"
    fi
    echo

    echo "--- preferences (expected: none, ever) ---"
    # The app uses no QSettings anywhere, so anything appearing here is a
    # regression and a gap in UNINSTALL.md, which states there is no plist.
    find "$HOME/Library/Preferences" -maxdepth 1 -name "*grocery*" -print 2>/dev/null \
        | sort | sed 's/^/  /'
    find "$HOME/Library/Preferences" -maxdepth 1 -name "*grocery*" 2>/dev/null \
        | grep -q . || echo "  none"
    echo

    echo "--- shell profile ---"
    if [ -f "$HOME/.zprofile" ]; then
        COUNT=$(grep -c "added by Grocery Planner installer" "$HOME/.zprofile" 2>/dev/null || true)
        echo "  $HOME/.zprofile has ${COUNT:-0} Grocery Planner PATH line(s)"
    else
        echo "  ABSENT  $HOME/.zprofile"
    fi
} > "$OUT" 2>&1

echo "snapshot written: $OUT"
