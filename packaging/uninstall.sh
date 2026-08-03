#!/bin/bash
# ===========================================================================
# Remove Grocery Planner and all of its data from this user account (GFP-92).
# ===========================================================================
# The macOS counterpart of uninstall.ps1, designed around the same two
# failures.
#
# THE FIRST is not leaving a file behind -- it is destroying a nutritionist's
# client list. Those records are hand-entered over months and exist nowhere
# else, so nothing is removed before an explicit confirmation, the confirmation
# names the database file, and --keep-data exists for the very common case of
# "I am reinstalling, not leaving".
#
# THE SECOND is the opposite: a half-finished uninstall leaving a Kroger
# client_secret and a live Whole Foods session cookie on a machine that is then
# resold, repaired or handed on. So this reports every path it could NOT
# remove, by full resolved path, and says plainly that those are credentials.
#
# TWO RULES FROM GFP-102'S CHECKLIST, both load-bearing:
#   1. THE LAUNCHAGENT FIRST, and BOOTED OUT BEFORE THE FILE IS DELETED.
#      Deleting a loaded agent's plist leaves it running until logout, which is
#      the one artifact that keeps ACTING after the app is gone.
#   2. THE DIRECTORY, NOT A LIST OF FILENAMES. The data directory accumulates
#      files no uninstaller can enumerate in advance.
#
# The plan comes from `gplan uninstall-plan`, so environment overrides are
# RESOLVED rather than assumed. If gplan will not run -- a normal state during
# an uninstall, not an exceptional one -- this falls back to the documented
# defaults and says which mode it is in.
#
# IDEMPOTENT: on a machine with nothing installed it says so and exits 0.
#
# Usage:
#   ./uninstall.sh --dry-run
#   ./uninstall.sh
#   ./uninstall.sh --keep-data
#   ./uninstall.sh --yes           # no prompt, for CI
# ===========================================================================
set -uo pipefail          # NOT -e: a removal that fails must be REPORTED, not
                          # abort the run and leave the rest in place.

# Pinned by GFP-102 -- mirrored in grocery_planner/install_paths.py.
APP_DISPLAY_NAME="Grocery Planner"
APP_BUNDLE_NAME="Grocery Planner.app"
SUPPORT_DIRNAME=".local/share/grocery-planner"
CLI_DIRNAME=".local/bin"
APPLICATIONS_DIRNAME="Applications"
LAUNCH_AGENT_LABEL="com.grocery-planner.refresh"
SAVED_STATE="com.grocery-planner.gui.savedState"
MANIFEST_FILENAME="install-manifest.json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0
KEEP_DATA=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)     YES=1; shift ;;
        --keep-data)  KEEP_DATA=1; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -h|--help)    sed -n '2,37p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

# Both timer kinds, not just this platform's. A plan produced by a build for
# the other OS -- which happens when a folder is copied between machines --
# must not fall through into the data list and get deleted as if it were a
# path.
is_timer() { [ "$1" = "agent" ] || [ "$1" = "task" ]; }

gray() { printf '  %s\n' "$1"; }
did()  { printf '  \033[32m[ok]\033[0m %s\n' "$1"; }
skip() { printf '  \033[90m[--]\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m[!!]\033[0m %s\n' "$1"; }

REMOVED=0
FAILED=()

echo
echo "=== Uninstalling $APP_DISPLAY_NAME ==="
[ "$DRY_RUN" -eq 1 ] && echo "    DRY RUN -- nothing will be removed."

# --------------------------------------------------------------------------- #
# 1. Locate the install. The manifest is authoritative because it is the only
#    thing that knows about a --prefix install.
# --------------------------------------------------------------------------- #
SUPPORT_DIR="$HOME/$SUPPORT_DIRNAME"
for candidate in "$SCRIPT_DIR" "$SUPPORT_DIR"; do
    if [ -f "$candidate/$MANIFEST_FILENAME" ]; then
        # sed rather than a JSON parser: macOS is not guaranteed one in the
        # shell, and depending on python3 inside the uninstaller for the app
        # whose Python is being removed is a dependency worth not having.
        ROOT="$(sed -n 's/.*"install_root"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' \
                "$candidate/$MANIFEST_FILENAME" | head -n1)"
        if [ -n "$ROOT" ]; then
            SUPPORT_DIR="$ROOT"
            gray "manifest: $candidate/$MANIFEST_FILENAME"
            break
        fi
    fi
done
gray "install: $SUPPORT_DIR"

# --------------------------------------------------------------------------- #
# 2. Ask the app where its data actually is. Failing here is expected.
# --------------------------------------------------------------------------- #
DATA_KINDS=(); DATA_FLAGS=(); DATA_LABELS=(); DATA_TARGETS=()
GPLAN="$SUPPORT_DIR/gplan"
[ -x "$GPLAN" ] || GPLAN="$SCRIPT_DIR/gplan"
RESOLVED=0
if [ -x "$GPLAN" ] && PLAN="$("$GPLAN" uninstall-plan 2>/dev/null)"; then
    while IFS=$'\t' read -r kind flags label target; do
        [ -n "${target:-}" ] || continue
        DATA_KINDS+=("$kind"); DATA_FLAGS+=("$flags")
        DATA_LABELS+=("$label"); DATA_TARGETS+=("$target")
    done <<< "$PLAN"
    RESOLVED=1
fi
if [ "$RESOLVED" -eq 0 ]; then
    gray "gplan could not be run -- using the documented default locations"
    DATA_KINDS=("agent" "directory")
    DATA_FLAGS=("-" "irreplaceable,sensitive")
    DATA_LABELS=("LaunchAgent (background refresh)" "All application data")
    DATA_TARGETS=("$LAUNCH_AGENT_LABEL" "$HOME/Library/Application Support/grocery-planner")
fi

# --------------------------------------------------------------------------- #
# 3. Say plainly what will go, then ask.
# --------------------------------------------------------------------------- #
echo
echo "This will remove:"
gray "PROGRAM"
gray "  $SUPPORT_DIR"
gray "  $HOME/$APPLICATIONS_DIRNAME/$APP_BUNDLE_NAME"
gray "  $HOME/$CLI_DIRNAME/gplan"
gray "  LaunchAgent $LAUNCH_AGENT_LABEL (the background refresh)"
if [ "$KEEP_DATA" -eq 1 ]; then
    echo
    printf '  \033[32mYour data will be KEPT (--keep-data):\033[0m\n'
    for i in "${!DATA_TARGETS[@]}"; do
        is_timer "${DATA_KINDS[$i]}" && continue
        gray "  keeping  ${DATA_TARGETS[$i]}"
    done
else
    echo
    printf '\033[33mDATA -- this cannot be undone\033[0m\n'
    for i in "${!DATA_TARGETS[@]}"; do
        # The timer is listed under PROGRAM, not here: it is not data, and
        # padding the irreversible list is how people stop reading it.
        is_timer "${DATA_KINDS[$i]}" && continue
        NOTE=""
        case "${DATA_FLAGS[$i]}" in
            *relocated:*) NOTE="   (moved here by ${DATA_FLAGS[$i]#*relocated:})" ;;
        esac
        gray "  ${DATA_TARGETS[$i]}${NOTE}"
    done
    echo
    printf '\033[33m  Your client records are hand-entered and CANNOT be recovered.\033[0m\n'
    printf '\033[33m  Export them first if you may want them:  gplan export --help\033[0m\n'
    printf '\033[33m  Or run this with --keep-data to remove only the program.\033[0m\n'
fi

if [ "$YES" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    echo
    printf 'Type REMOVE to continue, anything else to cancel: '
    read -r ANSWER
    if [ "$ANSWER" != "REMOVE" ]; then
        echo "Cancelled. Nothing was removed."
        exit 0
    fi
fi

# --------------------------------------------------------------------------- #
# 4. Remove.
# --------------------------------------------------------------------------- #
remove_path() {
    local label="$1" target="$2"
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        skip "$label -- not present"
        return
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  \033[36mwould remove: %s  (%s)\033[0m\n' "$label" "$target"
        return
    fi
    if rm -rf "$target" 2>/dev/null && [ ! -e "$target" ]; then
        REMOVED=$((REMOVED + 1))
        did "$label  ($target)"
    else
        FAILED+=("$target")
        bad "COULD NOT REMOVE $target"
    fi
}

echo
echo "Removing"

# --- the LaunchAgent, FIRST, and booted out BEFORE its file is deleted ------ #
AGENT_PLIST="$HOME/Library/LaunchAgents/$LAUNCH_AGENT_LABEL.plist"
if launchctl print "gui/$(id -u)/$LAUNCH_AGENT_LABEL" >/dev/null 2>&1; then
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  \033[36mwould unload: %s\033[0m\n' "$LAUNCH_AGENT_LABEL"
    elif launchctl bootout "gui/$(id -u)/$LAUNCH_AGENT_LABEL" 2>/dev/null; then
        did "unloaded $LAUNCH_AGENT_LABEL"
    else
        bad "could not unload $LAUNCH_AGENT_LABEL -- it may keep running until you log out"
        FAILED+=("launchctl: $LAUNCH_AGENT_LABEL")
    fi
else
    skip "LaunchAgent $LAUNCH_AGENT_LABEL -- not loaded"
fi
remove_path "LaunchAgent plist" "$AGENT_PLIST"

# --- the app, the symlink, the program directory ---------------------------- #
if pgrep -x "gplan-gui" >/dev/null 2>&1 && [ "$DRY_RUN" -eq 0 ]; then
    bad "$APP_DISPLAY_NAME is running -- quit it and run this again"
    exit 1
fi
remove_path "Application bundle" "$HOME/$APPLICATIONS_DIRNAME/$APP_BUNDLE_NAME"
remove_path "CLI symlink" "$HOME/$CLI_DIRNAME/gplan"
remove_path "Saved application state" "$HOME/Library/Saved Application State/$SAVED_STATE"
remove_path "Program files" "$SUPPORT_DIR"

# The PATH line the installer added. Left in place deliberately if anything
# else is still using ~/.local/bin -- silently rewriting somebody's shell
# profile is a bigger intrusion than leaving one harmless line, and the line is
# marked so they can find it.
PROFILE="$HOME/.zprofile"
if [ -f "$PROFILE" ] && grep -qF "added by Grocery Planner installer" "$PROFILE"; then
    echo
    gray "Your $PROFILE still contains the PATH line this installer added:"
    gray "  # added by Grocery Planner installer"
    gray "Remove it by hand if nothing else needs $HOME/$CLI_DIRNAME."
fi

# --- data ------------------------------------------------------------------- #
if [ "$KEEP_DATA" -eq 1 ]; then
    echo
    skip "application data kept (--keep-data)"
else
    for i in "${!DATA_TARGETS[@]}"; do
        is_timer "${DATA_KINDS[$i]}" && continue           # handled first
        remove_path "${DATA_LABELS[$i]}" "${DATA_TARGETS[$i]}"
    done
fi

# --------------------------------------------------------------------------- #
# 5. Report. "Some files could not be removed" is not good enough when one of
#    them is a credential.
# --------------------------------------------------------------------------- #
echo
if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN complete -- nothing was removed."
    exit 0
fi
if [ "$REMOVED" -eq 0 ] && [ "${#FAILED[@]}" -eq 0 ]; then
    echo "Nothing to remove -- $APP_DISPLAY_NAME is not installed for this user."
    exit 0
fi
printf '\033[32m=== Removed %s item(s) ===\033[0m\n' "$REMOVED"
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo
    printf '\033[31mCOULD NOT REMOVE %s item(s). Delete these by hand:\033[0m\n' "${#FAILED[@]}"
    for path in "${FAILED[@]}"; do printf '  \033[33m%s\033[0m\n' "$path"; done
    echo
    printf '\033[33mAt least one of these may hold a stored credential (a Kroger\033[0m\n'
    printf '\033[33mclient_secret or a Whole Foods session cookie). Leaving them on a\033[0m\n'
    printf '\033[33mmachine that is later resold or repaired is what this warning is for.\033[0m\n'
    printf '\033[33mSee UNINSTALL.md for the full manual removal checklist.\033[0m\n'
    exit 1
fi
echo
echo "$APP_DISPLAY_NAME has been removed."
[ "$KEEP_DATA" -eq 1 ] && gray "Your data was kept and is still where it was."
echo
