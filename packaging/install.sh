#!/bin/bash
# ===========================================================================
# Install Protein Ledger for the current user on macOS (GFP-91).
# ===========================================================================
# The macOS counterpart of install.ps1, and it makes the same three decisions
# for the same reasons: per-user so no administrator password is needed, no
# third-party installer toolchain, and it writes a manifest the uninstaller
# (GFP-92) reads rather than guessing at.
#
# WHERE THINGS GO, and why it is three directories rather than one:
#   ~/.local/share/protein-ledger/   the CLI binary, this manifest, uninstall
#   ~/.local/bin/gplan                a symlink, so `gplan` is on PATH
#   ~/Applications/Protein Ledger.app  the bundle, where macOS can launch it
# A .app must be in an Applications folder to be launchable and a CLI must be
# on PATH, so a single directory was never available. The manifest records the
# real path of every file, which is what keeps the uninstaller honest.
#
# GATEKEEPER: this build is not signed or notarised, so macOS quarantines it.
# See the --clear-quarantine note below -- the script does NOT strip that
# attribute unless explicitly asked, because silently disabling a security
# check on the user's behalf is not the installer's call to make.
#
# IDEMPOTENT, as the ticket requires: every step is "make it so", not "add
# one". Files are copied over, the symlink is replaced, and the PATH line is
# added only if its marker is not already in the profile.
#
# Usage:
#   ./install.sh
#   ./install.sh --dry-run
#   ./install.sh --prefix /tmp/gp-test --no-integrate
#   ./install.sh --no-timer          # skip the daily background refresh
#   ./install.sh --clear-quarantine
# ===========================================================================
set -euo pipefail

# --------------------------------------------------------------------------- #
# Pinned identifiers -- GFP-102 rule 1. Mirrored in
# grocery_planner/install_paths.py; tests/test_installer_scripts.py fails if
# they ever disagree.
# --------------------------------------------------------------------------- #
APP_DISPLAY_NAME="Protein Ledger"
APP_BUNDLE_NAME="Protein Ledger.app"
SUPPORT_DIRNAME=".local/share/protein-ledger"
CLI_DIRNAME=".local/bin"
APPLICATIONS_DIRNAME="Applications"
MANIFEST_FILENAME="install-manifest.json"
MANIFEST_SCHEMA=1

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=""
NO_INTEGRATE=0
NO_TIMER=0
DRY_RUN=0
CLEAR_QUARANTINE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)            PREFIX="$2"; shift 2 ;;
        --no-integrate)      NO_INTEGRATE=1; shift ;;
        --no-timer)          NO_TIMER=1; shift ;;
        --dry-run)           DRY_RUN=1; shift ;;
        --clear-quarantine)  CLEAR_QUARANTINE=1; shift ;;
        -h|--help)           sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

SUPPORT_DIR="${PREFIX:-$HOME/$SUPPORT_DIRNAME}"
BIN_DIR="$HOME/$CLI_DIRNAME"
APPS_DIR="$HOME/$APPLICATIONS_DIRNAME"
if [ -n "$PREFIX" ]; then
    # A custom prefix keeps EVERYTHING inside it. A test install that scattered
    # a symlink into ~/.local/bin and a bundle into ~/Applications would not be
    # a test install, it would be an install.
    BIN_DIR="$PREFIX/bin"
    APPS_DIR="$PREFIX/Applications"
fi

green() { printf '  \033[32m[ok]\033[0m %s\n' "$1"; }
gray()  { printf '  %s\n' "$1"; }
skip()  { printf '  \033[90m[--]\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m[!!]\033[0m %s\n' "$1"; }

# Every mutation goes through this so --dry-run cannot drift out of sync with
# what a real run does.
INSTALLED_FILES=()
run() {
    local describe="$1"; shift
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  \033[36mwould: %s\033[0m\n' "$describe"
        return 0
    fi
    "$@"
    green "$describe"
}

echo
echo "=== Installing $APP_DISPLAY_NAME ==="
[ "$DRY_RUN" -eq 1 ] && echo "    DRY RUN -- nothing will be changed."
echo
gray "source:  $SOURCE_DIR"
gray "target:  $SUPPORT_DIR"
echo

# --------------------------------------------------------------------------- #
# 1. Find the payload. The GUI is optional: a CLI-only release is a legitimate
#    thing to ship, and refusing to install one would be worse.
# --------------------------------------------------------------------------- #
CLI_SRC="$SOURCE_DIR/gplan"
APP_SRC="$SOURCE_DIR/$APP_BUNDLE_NAME"
[ -d "$APP_SRC" ] || APP_SRC="$SOURCE_DIR/gplan-gui.app"

if [ ! -f "$CLI_SRC" ]; then
    echo "No gplan binary found next to this script." >&2
    echo "Expected: $CLI_SRC" >&2
    echo "Run install.sh from inside the unzipped release folder." >&2
    exit 1
fi
gray "found gplan            $(du -h "$CLI_SRC" | cut -f1)"
if [ -d "$APP_SRC" ]; then
    gray "found $(basename "$APP_SRC")  $(du -sh "$APP_SRC" | cut -f1)"
else
    skip "no .app in this release -- installing the CLI only"
fi

# --------------------------------------------------------------------------- #
# 2. Refuse to overwrite a running app.
#
# Unlike Windows, macOS will happily let you replace the executable of a
# running process -- and the result is a running app whose code no longer
# matches its file, which crashes in ways nobody can diagnose afterwards.
# Failing first with an instruction is much better.
# --------------------------------------------------------------------------- #
if [ "$DRY_RUN" -eq 0 ] && pgrep -x "gplan-gui" >/dev/null 2>&1; then
    echo >&2
    echo "$APP_DISPLAY_NAME is currently running. Quit it and run this again." >&2
    exit 1
fi

# --------------------------------------------------------------------------- #
# 3. Copy the payload.
# --------------------------------------------------------------------------- #
echo
echo "Files"
run "create $SUPPORT_DIR" mkdir -p "$SUPPORT_DIR"
run "install gplan" cp -f "$CLI_SRC" "$SUPPORT_DIR/gplan"
run "make gplan executable" chmod +x "$SUPPORT_DIR/gplan"
INSTALLED_FILES+=("$SUPPORT_DIR/gplan")

for doc in INSTALL.md UNINSTALL.md uninstall.sh; do
    # The uninstall checklist ships WITH the install, not only in the ZIP:
    # GFP-102 requires that a user whose uninstall failed can read the manual
    # removal steps, and by then the ZIP is usually long gone.
    [ -f "$SOURCE_DIR/$doc" ] || continue
    run "install $doc" cp -f "$SOURCE_DIR/$doc" "$SUPPORT_DIR/$doc"
    INSTALLED_FILES+=("$SUPPORT_DIR/$doc")
done
[ "$DRY_RUN" -eq 0 ] && [ -f "$SUPPORT_DIR/uninstall.sh" ] && chmod +x "$SUPPORT_DIR/uninstall.sh"

# --------------------------------------------------------------------------- #
# 3b. The bundled Kroger credential (GFP-146).
#
# v1 ships with the API key so a customer does not have to register their own
# application at developer.kroger.com. It goes into the app's USER-DATA dir --
# never next to the binary and never as a constant inside it. That placement is
# the point: a key in a binary is recoverable either way, but a key in a file
# can be ROTATED with a file swap, while a key in a binary needs a rebuild, a
# re-release, and every user to notice and act. Kroger's 10,000 calls/day
# ceiling is per credential, so one extracted key is one revocation for
# everybody. GFP-147 replaces this with a hosted key.
#
# NOTE THE PATH. This is platformdirs' macOS user-data dir, which is NOT
# $SUPPORT_DIR -- the binaries install under ~/.local/share, but the app reads
# its data from ~/Library/Application Support. Putting the credential beside
# the binary would leave the app looking somewhere else and reporting no
# credentials at all.
# --------------------------------------------------------------------------- #
CREDENTIAL_NAME="kroger-env.config"
DATA_DIR="$HOME/Library/Application Support/grocery-planner"
if [ ! -f "$SOURCE_DIR/$CREDENTIAL_NAME" ]; then
    # A credential-free build is legitimate; the app already explains what is
    # needed via `gplan credentials`.
    skip "no Kroger credential bundled -- run 'gplan credentials' to see what is needed"
elif [ -n "$PREFIX" ]; then
    # A custom prefix means a test install, and the data dir is outside it. A
    # test install that dropped a shared credential into the real profile would
    # not be a test install.
    skip "Kroger credential skipped (--prefix given; it would land outside the prefix)"
elif [ -f "$DATA_DIR/$CREDENTIAL_NAME" ]; then
    # NEVER OVERWRITE. Replacing an operator's own key with the shared one
    # would move them onto a quota pool they did not ask to share, silently.
    skip "Kroger credential already present -- keeping yours"
else
    run "create $DATA_DIR" mkdir -p "$DATA_DIR"
    run "install Kroger credential" \
        cp -f "$SOURCE_DIR/$CREDENTIAL_NAME" "$DATA_DIR/$CREDENTIAL_NAME"
fi

if [ -d "$APP_SRC" ]; then
    run "create $APPS_DIR" mkdir -p "$APPS_DIR"
    # rm first: cp -R onto an existing bundle MERGES rather than replaces, so
    # an upgrade that drops a file would leave the old one behind inside the
    # new bundle. That is exactly the kind of thing that works until it does
    # not.
    run "remove any previous bundle" rm -rf "$APPS_DIR/$APP_BUNDLE_NAME"
    run "install $APP_BUNDLE_NAME" cp -R "$APP_SRC" "$APPS_DIR/$APP_BUNDLE_NAME"
    INSTALLED_FILES+=("$APPS_DIR/$APP_BUNDLE_NAME")
fi

# --------------------------------------------------------------------------- #
# 4. Prove the thing that was installed actually runs.
#
# A copy that succeeded is not an install that worked. A binary built for the
# wrong architecture -- entirely possible now that Apple ships two -- copies
# perfectly and then fails on first launch, at which point the user blames the
# app rather than the install.
# --------------------------------------------------------------------------- #
VERSION="unknown"
if [ "$DRY_RUN" -eq 0 ]; then
    echo
    echo "Verifying"
    if ! OUTPUT="$("$SUPPORT_DIR/gplan" version 2>&1)"; then
        echo "The installed binary does not run. Install aborted." >&2
        echo "$OUTPUT" >&2
        # A quarantined binary fails here with 'killed'. Say so, because the
        # generic message sends people looking for a build problem.
        if xattr -p com.apple.quarantine "$SUPPORT_DIR/gplan" >/dev/null 2>&1; then
            echo >&2
            echo "This binary is quarantined by macOS (it was downloaded from the" >&2
            echo "internet and is not signed). Re-run with --clear-quarantine to" >&2
            echo "remove that attribute, after satisfying yourself the download is" >&2
            echo "the one you meant to get." >&2
        fi
        exit 1
    fi
    VERSION="$(printf '%s' "$OUTPUT" | sed -n 's/^grocery-planner[[:space:]]\{1,\}\(.*\)$/\1/p')"
    [ -n "$VERSION" ] || VERSION="unknown"
    green "gplan runs: $OUTPUT"
fi

# --------------------------------------------------------------------------- #
# 5. Gatekeeper.
#
# Not stripped by default and not stripped silently. Removing com.apple.
# quarantine disables a real security check, and an installer that does that
# without being asked teaches users that installers do that -- which is the
# habit every malicious package relies on. The honest fix is signing and
# notarisation (an Apple Developer account); until then this is an explicit,
# informed opt-in.
# --------------------------------------------------------------------------- #
if [ -d "$APPS_DIR/$APP_BUNDLE_NAME" ] && [ "$DRY_RUN" -eq 0 ]; then
    if xattr -pr com.apple.quarantine "$APPS_DIR/$APP_BUNDLE_NAME" >/dev/null 2>&1; then
        if [ "$CLEAR_QUARANTINE" -eq 1 ]; then
            run "clear the quarantine attribute (--clear-quarantine)" \
                xattr -dr com.apple.quarantine "$APPS_DIR/$APP_BUNDLE_NAME"
        else
            echo
            warn "macOS has quarantined this app because it is not signed."
            gray "The first launch will be refused. To allow it, either:"
            gray "  * right-click the app in Finder and choose Open, once, or"
            gray "  * re-run this installer with --clear-quarantine"
        fi
    fi
fi

# --------------------------------------------------------------------------- #
# 6. Integration: the PATH symlink.
# --------------------------------------------------------------------------- #
INTEGRATIONS_BIN=""
INTEGRATIONS_PROFILE=""
if [ "$NO_INTEGRATE" -eq 1 ]; then
    echo
    skip "PATH symlink and shell profile skipped (--no-integrate)"
else
    echo
    echo "Integration"
    run "create $BIN_DIR" mkdir -p "$BIN_DIR"
    # ln -sf on an existing SYMLINK replaces it; on an existing DIRECTORY it
    # would create a link inside it. rm first so a reinstall cannot end up with
    # ~/.local/bin/gplan/gplan.
    run "link $BIN_DIR/gplan" bash -c 'rm -f "$1" && ln -s "$2" "$1"' _ \
        "$BIN_DIR/gplan" "$SUPPORT_DIR/gplan"
    INTEGRATIONS_BIN="$BIN_DIR/gplan"

    # Only touch a profile if the directory is genuinely not on PATH. Most
    # macOS setups already have ~/.local/bin, and appending a redundant line to
    # someone's shell profile is a small rudeness that accumulates.
    case ":$PATH:" in
        *":$BIN_DIR:"*) skip "$BIN_DIR is already on your PATH" ;;
        *)
            PROFILE="$HOME/.zprofile"
            MARKER="# added by Protein Ledger installer"
            if [ -f "$PROFILE" ] && grep -qF "$MARKER" "$PROFILE"; then
                skip "$PROFILE already has the PATH line"
                INTEGRATIONS_PROFILE="$PROFILE"
            else
                run "add $BIN_DIR to PATH in $PROFILE" \
                    bash -c 'printf "\n%s\nexport PATH=\"%s:\$PATH\"\n" "$1" "$2" >> "$3"' _ \
                    "$MARKER" "$BIN_DIR" "$PROFILE"
                INTEGRATIONS_PROFILE="$PROFILE"
                warn "open a new terminal before \`gplan\` is on your PATH"
            fi
            ;;
    esac
fi

# --------------------------------------------------------------------------- #
# 6b. The background refresh (GFP-102).
#
# Registered by the installer, because price history only accumulates going
# forward and cannot be backfilled: every day the machine does not scrape is a
# permanent hole in the trends chart. Opt-OUT rather than opt-in for that
# reason, with two ways out -- --no-timer here, and the `background_refresh`
# setting at any time afterwards.
#
# Not fatal if it fails. An install that works but does not refresh by itself
# is a much better outcome than no install.
# --------------------------------------------------------------------------- #
INTEGRATIONS_TIMER=""
if [ "$NO_TIMER" -eq 1 ] || [ "$NO_INTEGRATE" -eq 1 ]; then
    skip "background refresh not registered"
elif [ "$DRY_RUN" -eq 1 ]; then
    printf '  [36mwould: register the daily background refresh[0m
'
else
    if "$SUPPORT_DIR/gplan" timer install >/dev/null 2>&1; then
        green "background refresh registered (daily)"
        INTEGRATIONS_TIMER="com.proteinledger.refresh"
    else
        warn "could not register the background refresh -- the app still works"
        gray "retry any time with:  gplan timer install"
    fi
fi

# --------------------------------------------------------------------------- #
# 7. The manifest. Written LAST, so its presence means the install completed.
#
# Hand-built JSON rather than a here-doc template, because the file list is
# variable and a malformed manifest is worse than none -- the uninstaller would
# fall back to defaults and miss a --prefix install entirely.
# --------------------------------------------------------------------------- #
MANIFEST="$SUPPORT_DIR/$MANIFEST_FILENAME"
if [ "$DRY_RUN" -eq 1 ]; then
    printf '  \033[36mwould: write %s\033[0m\n' "$MANIFEST"
else
    {
        printf '{\n'
        printf '  "schema": %s,\n' "$MANIFEST_SCHEMA"
        printf '  "app": "%s",\n' "$APP_DISPLAY_NAME"
        printf '  "version": "%s",\n' "$VERSION"
        printf '  "platform": "macos",\n'
        printf '  "install_root": "%s",\n' "$SUPPORT_DIR"
        printf '  "installed_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '  "files": [\n'
        for i in "${!INSTALLED_FILES[@]}"; do
            sep=","
            [ "$i" -eq $(( ${#INSTALLED_FILES[@]} - 1 )) ] && sep=""
            printf '    "%s"%s\n' "${INSTALLED_FILES[$i]}" "$sep"
        done
        printf '  ],\n'
        printf '  "integrations": {\n'
        printf '    "symlink": "%s",\n' "$INTEGRATIONS_BIN"
        printf '    "profile": "%s",\n' "$INTEGRATIONS_PROFILE"
        printf '    "timer": "%s"\n' "$INTEGRATIONS_TIMER"
        printf '  }\n'
        printf '}\n'
    } > "$MANIFEST"
    green "write $MANIFEST_FILENAME"
fi

# --------------------------------------------------------------------------- #
# 8. Hand over to a working first run.
# --------------------------------------------------------------------------- #
echo
if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN complete -- nothing was changed."
    exit 0
fi
echo "=== $APP_DISPLAY_NAME $VERSION installed ==="
echo
echo "Next steps"
gray "1. Set your ZIP code so prices are local to you:"
printf '     gplan config set postal_code 27401\n'
gray "2. Open $APP_DISPLAY_NAME from ~/Applications -- it fetches today's"
gray "   prices on first run."
gray "   Or from a terminal:  gplan scrape harristeeter"
echo
gray "To uninstall: $SUPPORT_DIR/uninstall.sh"
echo
