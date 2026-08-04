# Uninstalling Protein Ledger

> **Read this first.** Your client records are hand-entered and exist nowhere
> else. Uninstalling deletes them permanently. If there is any chance you want
> them later, export first:
>
> ```
> gplan export --help
> ```
>
> Or uninstall the program and **keep your data** — see "Keeping your data"
> below.

---

## Windows

Either use **Settings → Apps → Protein Ledger → Uninstall**, or run the script
directly:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\ProteinLedger\uninstall.ps1"
```

## macOS

```bash
~/.local/share/protein-ledger/uninstall.sh
```

Both ask you to type `REMOVE` before anything is deleted, and both print every
path they removed — and every path they could **not**.

### Options

| Option | What it does |
| --- | --- |
| `-DryRun` / `--dry-run` | Show everything that would go. Remove nothing. |
| `-KeepData` / `--keep-data` | Remove the program, keep the database, settings and credentials. |
| `-Yes` / `--yes` | Skip the confirmation prompt. |

### Keeping your data

```powershell
.\uninstall.ps1 -KeepData      # Windows
```
```bash
./uninstall.sh --keep-data     # macOS
```

This is what you want when reinstalling or upgrading by hand. Your clients,
settings and stored prices stay exactly where they are, and a later install
picks them up again.

---

# If the uninstaller failed, or was never run

Everything below can be done by hand. **Do the background timer first** — it is
the one thing that keeps *acting* after the app is gone: it will keep firing,
keep hitting the network, and keep failing against a program that no longer
exists.

## Windows, by hand

**1. The scheduled task — do this first**

```powershell
Unregister-ScheduledTask -TaskPath "\ProteinLedger\" -TaskName "Refresh" -Confirm:$false
```

Then delete the now-empty **ProteinLedger** folder in the Task Scheduler
window. `schtasks` cannot remove folders, and an empty folder left behind reads
as a failed uninstall.

Check it is gone:

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like "*rocery*" }   # must print nothing
```

**2. All application data — delete the whole folder**

```
%LOCALAPPDATA%\grocery-planner\grocery-planner\
```

Delete the **folder**, not selected files. It accumulates things no list can
predict: SQLite's `-wal` and `-shm` files, and any backups you made by hand.

**3. The program, and its traces**

| What | Where |
| --- | --- |
| Program files | `%LOCALAPPDATA%\Programs\ProteinLedger` |
| Start Menu | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Protein Ledger` |
| Add/Remove Programs | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ProteinLedger` |
| PATH | Remove the `...\Programs\ProteinLedger` entry from your **user** PATH |

## macOS, by hand

**1. The LaunchAgent — do this first, and unload it *before* deleting the file.**
Deleting a loaded agent leaves it running until you log out.

```bash
launchctl bootout gui/$(id -u)/com.proteinledger.refresh
rm -f ~/Library/LaunchAgents/com.proteinledger.refresh.plist
launchctl list | grep grocery      # must print nothing
```

**2. All application data — delete the whole folder**

```bash
rm -rf ~/Library/Application\ Support/grocery-planner
```

**3. The program, and its traces**

| What | Where |
| --- | --- |
| The app | `~/Applications/Protein Ledger.app` |
| The `gplan` command | `~/.local/bin/gplan` |
| Program files | `~/.local/share/protein-ledger` |
| Saved window state | `~/Library/Saved Application State/com.proteinledger.gui.savedState` |

```bash
rm -rf ~/Applications/Grocery\ Planner.app
rm -f  ~/.local/bin/gplan
rm -rf ~/.local/share/protein-ledger
rm -rf ~/Library/Saved\ Application\ State/com.proteinledger.gui.savedState
```

If your `~/.zprofile` has a line marked `# added by Protein Ledger installer`,
delete it — unless something else on your machine needs `~/.local/bin`.

**Not applicable, so nobody wastes time looking:** there is no preferences
plist. The app writes nothing to `~/Library/Preferences`.

---

## Credentials — the part that actually matters

The data folder holds **real secrets**:

- `kroger-env.config` — an OAuth2 `client_id` and `client_secret`
- `wholefoods_session.json` — a live session cookie

If an uninstall half-fails, these are what gets left behind on a machine that
may later be sold, repaired or handed on. **"Some files could not be removed"
is not good enough when one of them is a credential** — so if the uninstaller
listed anything it could not remove, delete those paths by hand now.

## If you moved a file with an environment variable

Any of these relocates a file **out** of the data folder, and deleting the
folder then misses it:

```
GROCERY_PLANNER_DB
GROCERY_PLANNER_CONFIG
GROCERY_PLANNER_LOG_DIR
GROCERY_PLANNER_KROGER_CONFIG
GROCERY_PLANNER_WHOLEFOODS_SESSION
```

The uninstaller resolves these automatically and names the real paths. To see
them yourself, before or after:

```
gplan uninstall-plan       # every path an uninstall would remove
gplan credentials          # where each credential is, without printing it
```
