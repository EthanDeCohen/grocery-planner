# Installing Protein Ledger

You need the ZIP for your operating system, unzipped somewhere you can find it
— your Downloads folder is fine. Everything below runs as **you**, not as an
administrator, and installs only for your user account.

---

## Windows

1. Unzip the download.
2. Right-click **install.ps1** → **Run with PowerShell**.

If Windows refuses to run the script, open PowerShell in the unzipped folder
and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

That restriction is Windows' default protection against scripts downloaded from
the internet. You can read `install.ps1` first — it is plain text, and it is
meant to be read.

**Where things go**

| What | Where |
| --- | --- |
| Program files | `%LOCALAPPDATA%\Programs\ProteinLedger` |
| Start Menu shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Protein Ledger` |
| Add/Remove Programs entry | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\ProteinLedger` |
| Your data (database, settings, logs) | `%LOCALAPPDATA%\grocery-planner\grocery-planner` |

Your data is deliberately **not** in the program folder, so reinstalling or
upgrading never touches your clients.

---

## macOS

1. Unzip the download.
2. Open Terminal, `cd` into the unzipped folder, and run:

```bash
./install.sh
```

**Where things go**

| What | Where |
| --- | --- |
| The app | `~/Applications/Protein Ledger.app` |
| The `gplan` command | `~/.local/bin/gplan` |
| Program files | `~/.local/share/protein-ledger` |
| Your data (database, settings, logs) | `~/Library/Application Support/grocery-planner` |

### macOS will refuse to open the app the first time

This build is not signed by Apple, so Gatekeeper quarantines it and says the
app "cannot be opened because the developer cannot be verified". Two ways past
it:

* **Right-click the app in Finder and choose Open**, then confirm. You only do
  this once.
* Or re-run the installer with `./install.sh --clear-quarantine`.

The installer does **not** remove that quarantine flag on its own. Silently
disabling a security check is a habit worth not teaching, and you should be the
one who decides. The real fix is Apple notarisation, which is not in place yet.

---

## First run

```bash
gplan config set postal_code 27401     # your ZIP, so prices are local to you
```

Then open the app. It fetches the day's prices by itself on first run and again
on each new day; you do not have to remember to refresh.

From the terminal instead:

```bash
gplan config              # every setting, and where each value came from
gplan scrape harristeeter # pull prices now
gplan cheapest            # cheapest protein per store, right now
```

---

## Installing again, and upgrading

Run the installer again. It is idempotent: it replaces the program files,
leaves your data alone, and does not create duplicate shortcuts or PATH
entries. To upgrade, unzip the new version and run its installer.

Close the app first — Windows will not let the installer replace a running
program, and it will tell you so rather than half-installing.

## Options

Both installers accept:

| Option | What it does |
| --- | --- |
| `-DryRun` / `--dry-run` | Print every action without doing any of it |
| `-Prefix` / `--prefix` | Install somewhere else, for testing |
| `-NoIntegrate` / `--no-integrate` | Copy files only; no PATH, shortcut or registry entry |
| `--clear-quarantine` (macOS) | Remove the Gatekeeper quarantine flag |

## Uninstalling

See **UNINSTALL.md**, which is installed alongside the program so it is still
there when you need it.
