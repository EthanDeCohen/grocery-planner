# Protein Ledger v1.x Full Shakedown

**Date:** 2026-08-04 · **Audited at:** `main` @ `2f7a663` (v1.1.3 + 0 commits) · **Scope:** full repo, GFP Jira project (162 issues), v1.0.0→v1.1.3 release cycle
**Method:** manual code review (all core modules read in full), Jira pull via REST, git forensics, dependency check against installed versions. Test baseline before review: **1,646 tests, all passing** on Python 3.13/Windows.

---

## Executive Summary

- **The single biggest exposure is by design, not by accident: one shared Kroger `client_secret` ships in plaintext inside every release ZIP** (`release.yml:120,148` → `kroger-env.config` in the archive), lands unprotected in each user's data dir, and is the same live secret sitting in this working tree. GFP-146 accepted this knowingly as a v1 bridge; GFP-147/149 (broker) are the exit. Rotate the secret at the next release regardless.
- **The v1.0.0→v1.1.3 hotfix cycle was 100% packaging/branding, 0% domain engine.** All five releases happened on one day; three of the four post-v1.0 releases fixed fallout from the GFP-158 rename, the rest fixed installer friction (GFP-159/161/162). The optimiser, DB layer, and scrapers produced zero hotfixes. The recurring root cause is guards that assert *spellings* instead of *relationships* across packaging/CI/installer files — the highest-leverage engineering investment is there, not in the domain code.
- **One open bug quietly halves the product:** Food Lion's 297 priced deals are invisible to the optimiser (GFP-121) because `service/ingest.run_scrape` never runs `matching.match_deals()` after a Flipp scrape — only the Kroger and Whole Foods scrapers write their own match rows. One call fixes it.
- **The printed grocery list can contradict the plan on screen.** `service/shopping.py` builds the list from *one day's bill × 7* and accepts no `Selection`, while the client page shows a varied `week_plan` (vary-week is the default since GFP-142, and the measured gap is 20–127%). This is the same class of defect GFP-144 was opened to kill.
- **Every `db.connect()` is doing far too much work**: full migration scan, two checksums per script ×20, an in-memory replay of all 20 scripts (`_adoption_point`) even when `schema_version` is complete, a store upsert, and a commit — and the GUI opens 25+ fresh connections from event handlers, all on the UI thread. Add that no WAL/`busy_timeout` is set while the background refresh task and the GUI are two concurrent writer processes.
- **Security posture is otherwise unusually good for a v1**: parameterised SQL throughout, no `shell=True`, no `verify=False`, timeouts + bounded retries on every HTTP client, a log-redaction filter, a check-only updater that downloads nothing, and a test (`test_shipped_credential.py`) that enforces "secret never tracked, never a constant." The gaps are file permissions, dependency pinning, and release-artifact integrity.
- **Jira hygiene is strong with a specific drift problem**: three shipped tickets are still in Backlog (GFP-159/161/162), two epics with all children done are still open (GFP-20/21), neither fixVersion is marked Released despite five shipped tags, and the one open Bug (GFP-121) carries no fixVersion at all.
- **Kroger's API remains the best data source measured** (GFP-77: 100% price, 100% machine-readable size, 82% protein grams — vs 4.9% sizes via Flipp), and because Ralphs and Food 4 Less are Kroger banners, the same integration is a near-free bridge to a SoCal market if that ever becomes real.
- Test suite is a genuine asset (1,646 tests incl. offscreen GUI, schema guards, CI-cost guards) — but the **single riskiest module has no dedicated tests: `scrapers/base.py`**, the Flipp parser that feeds the entire product.
- **There is no lint or type-check anywhere in CI**, no lockfile, and all dependency versions are floor-only — three cheap, standard controls currently missing.

---

## Task 1 — Codebase Deep Review

*(~21.7k LOC package + ~20.1k LOC tests read/reviewed; severity = risk if left × likelihood. Every finding cites the evidence line.)*

### Critical

**C1. Shared long-lived Kroger secret distributed to every install** — `.github/workflows/release.yml:120,148`, `packaging/install.ps1:203-238`, `packaging/install.sh:161-196`
The OAuth `client_secret` is written into each release ZIP and copied to every user's data dir in plaintext with default ACLs. Anyone with one ZIP holds the credential for all installs; revocation punishes every customer; the 10k calls/day ceiling is shared. Known and ticketed (GFP-146 accepted / GFP-147+149 exit), listed here because it is still the largest live risk in the shipped product.
*Fix: rotate `kg-Zc0Dv…` now; pull GFP-147 (token broker) forward ahead of any wider distribution; until then chmod/icacls-restrict the credential files at install time.*

### High

**H1. Food Lion deals never matched → invisible to the optimiser** — `grocery_planner/service/ingest.py:242-389` (no call to `matching.match_deals`); corroborates open bug GFP-121
Flipp-sourced scrapes insert deals but nothing creates `deal_food_match` rows; the cost-per-gram chain (`savings.py:425`) requires a match, so those deals are excluded from every bill. Kroger (`kroger.py:843`) and Whole Foods write their own match rows, masking the gap.
*Fix: call `matching.match_deals(conn)` at the end of `run_scrape` (or in the GFP-44 IngestManager), + a regression test that a scraped Flipp deal is rankable.*

**H2. Grocery list diverges from the on-screen week plan** — `grocery_planner/service/shopping.py:187-236`; callers `gui/client.py:312`, `cli.py:700`
The list = day-one bill × `days`, ignoring `vary_week` (default ON since GFP-142) and the whole `Selection` (single-store, cover-all, budget objective). The panel beside it prices the real varied week — measured 20–127% dearer with different items. A client shops the list; the plan on screen was the one the nutritionist approved.
*Fix: build the list from `bill.week_plan(...)` aggregated across the 7 days, and thread `categories`+`selection` through `grocery_list_for`.*

**H3. `db.connect()` is heavyweight and runs on every operation** — `grocery_planner/db.py:408,439-460`
Every connect: reads all 20 scripts, computes 2×SHA256 each, queries `schema_version`, then runs `_adoption_point` (replays all 20 scripts into a `:memory:` DB with a fingerprint after each) **even when every script is already recorded**, then upserts 3 stores and commits (a write on every "read"). The GUI opens 25+ fresh connections from event handlers (`gui/billpanel.py:228,275,313`, `gui/client.py:196,243,277,307,312`, …), all synchronously on the UI thread.
*Fix: skip adoption + application entirely when `len(recorded) == len(scripts)` and checksums verify (one cheap early-out); seed stores only when a script was applied; share one connection per window/thread.*

**H4. Two writer processes, no WAL, no busy_timeout** — `grocery_planner/db.py:439-446`
The GFP-102 scheduled task and an open GUI can write concurrently (the refresh lock only serialises *refreshes*, not GUI writes vs a background scrape). Default rollback journal + default 5s timeout means user-visible `database is locked` errors are a matter of time — and the store upsert in `init_db` makes even read paths take write locks.
*Fix: `PRAGMA journal_mode=WAL` + `busy_timeout=5000+` at connect; stop writing on read-only paths (see H3).*

### Medium

**M1. Ranking is an N+1 re-done everywhere** — `savings.rank_by_cost_per_gram_protein` (`savings.py:507-555`) issues ~2 queries/deal (match + food) → ~3,800 queries per ranking over ~1,900 deals (~40ms measured). `bill._build_bill` re-ranks per bill; `budget.weekly_plan` ranks twice (daily + week) and **never passes the `ranked=` pool `week_plan` already accepts**; `budget.advise`/`relaxations` solve ~N+2 weekly plans per invocation → tens of rankings per checkbox click, on the UI thread (`gui/billpanel.py:227-330`).
*Fix: rank once per interaction and thread the pool through (`bill.rank_current_deals` exists for exactly this); longer-term, replace the per-row lookups with one JOIN.*

**M2. Protein-factor clinical band enforced only in the GUI** — `customers.py:84-85` defines MIN/MAX (GFP-133 "hard limits"), but `service/clients.py` and `cli.py:1498/1578` accept any `--factor` (e.g. 5.0) straight into `protein_factor`. In a health tool the band is a safety rail; the codebase's own rule is that shared rules live in the service layer.
*Fix: validate in `service/clients.update_client`/`add_client` (reject outside band unless a custom formula governs).*

**M3. Duplicate `_money` in `cli.py` silently changes behaviour** — `cli.py:501` (`None → "-"`) is shadowed at module load by `cli.py:1648` (`None → ""`), so all earlier call sites (280, 302, 353, 359, 485-493) render missing money as empty instead of the intended dash. Symptom of a wider pattern: `_money` ×6, `_now` ×5, `_store_label` ×3, `_to_float` ×3 across the package.
*Fix: delete the second def; extract a `display.py`/`fmt.py` helper module (candidate addition to the GFP-22 epic).*

**M4. Python 3.12+ sqlite3 date-adapter deprecation** — `records.py:192` emits `DeprecationWarning` (test run confirms); the default adapters are removed in a future Python, at which point inserts with `date` objects raise.
*Fix: pass ISO strings at the call sites (the schema already stores TEXT).*

**M5. Dead/near-dead code and drifted docs** — `bill._category_lookup` (`bill.py:341-358`) has zero callers; `README.md` still lists GFP-4/7/8/10/11 as "planned" and Whole Foods as "manual CSV" despite an 1,130-line scraper; `scripts/verify.ps1` header claims CI no longer runs on push/PR (GFP-95 reversed that).
*Fix: delete the dead function; refresh README's developer half; fix verify.ps1's header (these stale claims actively mislead — the same failure mode as the rename escapes).*

**M6. Module-name collisions across layers** — `formulas.py` vs `gui/formulas.py`, `service/cheapest.py` vs `gui/cheapest.py`, `service/trends.py` vs `gui/trends.py`; plus ~30 function-local imports working around cycles (concentrated in `cli.py`, `gui/app.py`, `nutrition.py`↔`protein_kind.py`).
*Fix: no rename rush (the last rename cost 3 hotfixes) — but adopt a rule for new modules, and untangle the nutrition/protein_kind cycle when touched.*

**M7. `weekly_plan`/`relaxations` recompute unconstrained baselines repeatedly** — `budget.py:150-256`: `advise()` = current plan + unconstrained plan + one plan per relaxable category, each a fresh `weekly_plan` (2 rankings + 8 solves). With H3/M1 this is the whole story behind any UI sluggishness on the client page.
*Fix: covered by M1's rank-once change; also cache `nutrition.food_ids_in` per solve.*

### Low

- **L1.** `background.refresh_lock` stale-takeover race (`background.py:122-128`): two processes can both unlink the stale lock; the loser gets an unhandled `FileExistsError`. Wrap the second `os.open` in a try/except → treat as `AlreadyRunning`.
- **L2.** `scheduler._scrape_job` swallows all exceptions bare (`scheduler.py:186` `except Exception: pass`) — intentional (job row records it), but a `log.exception` here costs nothing and CI-grade SAST will flag it anyway.
- **L3.** `refresh_once` opens a connection before the enabled/schedule checks and never closes it on early returns (`background.py:178`).
- **L4.** `test_installer_scripts.py:471` docstring emits a `SyntaxWarning` (unescaped `\i`) on every run — make it a raw string.
- **L5.** `savings._UNITS` uses the rounded g→oz factor (0.035274) but the exact oz→g constant (28.3495…), so a "500 g" item round-trips to 499.994 g. Harmless today; worth one constant.
- **L6.** `jobs.recent_jobs`/`fetch_deals` interpolate `LIMIT {int(limit)}` — safe (int-cast) but inconsistent with the `?`-placeholder rule used two lines away.
- **L7.** Local clutter: 68 MB `dist/`+`build/`, `GroceryPlanner.xlsm`, `kroger-env.config` in the working tree — all correctly gitignored, but the secret file's presence on a dev box is an exposure (see Task 3).

### Technical debt, ranked (risk if unaddressed ÷ cost to fix now)

| # | Debt | Risk | Cost now | Verdict |
|---|------|------|----------|---------|
| 1 | Ingest doesn't match (H1) | High — product half-blind | ~1 line + test | Fix immediately |
| 2 | Connection lifecycle + connect() weight (H3/H4) | High — perf, lock errors | Small (early-out + pragmas) | Fix before v2 features |
| 3 | Grocery list ≠ week plan (H2) | High — user-facing contradiction | Medium | Fix before next release |
| 4 | Rank-once plumbing (M1/M7) | Medium — UI latency grows with data | Medium | With #2 |
| 5 | No lint/typecheck/lockfile | Medium — drift accumulates silently | Small | One PR |
| 6 | Helper duplication + cli.py god module (M3) | Medium — slows every feature | Medium | Fold into GFP-22 |
| 7 | Packaging triple-assembly drift (GFP-160, confirmed real by hotfix history) | Medium | Medium | Schedule in v2.0-M3 |
| 8 | Name collisions / import cycles (M6) | Low-Med | High (rename risk!) | Rule for new code only |

### Test coverage gaps (critical paths first)

The suite is strong (1,646 tests; offscreen GUI; meta-guards for schema, CI cost, packaging drift, branding). Gaps, in order of blast radius:
1. **`scrapers/base.py` — the Flipp client/parser: no dedicated test file.** This is the supply line for 2 of 3 stores; the GFP-67 guards exist *because* it breaks. Golden-payload fixture tests would catch shape drift before a customer's 6am refresh does.
2. **`service/refresh.py`, `service/shoppingfmt.py`, `service/deals.py`** — no dedicated files (partial indirect coverage).
3. **GUI orchestration**: `gui/app.py`, `billpanel.py`, `roster.py`, `wheretobuy.py`, `selectionpanel.py`, `firstrun.py`, `loadcredential.py` untested directly; the panels compute money figures (billpanel headline) worth asserting.
4. **H2's divergence** was catchable by a single "list totals equal week-plan totals" property test — add it with the fix.
5. Installer/uninstaller scripts execute only in the CI `binary` job (correct call — keep it), but note `test_installer_scripts.py` asserts *spellings*; after the rename lesson, prefer relationship assertions (e.g. "task name in installer == `install_paths` constant").

---

## Task 2 — Jira Backlog & Roadmap Review

**Board state (2026-08-04):** 162 issues — 118 Done, 44 Backlog (18 Story, 16 Task, 9 Epic, 1 Bug). None stale by date (project is ~6 weeks old, descriptions are uniformly substantive — hygiene is well above typical).

### 2.1 Hygiene flags

| Issue | Problem | Action |
|---|---|---|
| GFP-159, GFP-161, GFP-162 | **Shipped but still Backlog** — commits `a5b1dd5`/`2f7a663` merged, released in v1.1.2/v1.1.3 | Transition to Done, set fixVersion v1.1.2/v1.1.3 |
| GFP-20, GFP-21 (epics) | All children Done; epics still open, tagged v1.0 | Close both |
| GFP-1 (epic) | Only GFP-5 still open under it; epic spans both versions | Re-parent GFP-5 (or keep GFP-1 as the v2 scraping epic) and retag |
| fixVersions `v1.0`, `v2.0` | Neither marked **Released** in Jira despite tags v1.0.0–v1.1.3 | Mark v1.0 released (date 2026-08-04); create v1.1 or fold hotfixes into v1.0's release note |
| GFP-121 (only open Bug) | **No fixVersion, no epic** — and it's the H1 finding | Tag v2.0 (or a v1.1.4 hotfix), link to GFP-44 |
| GFP-18, GFP-19, GFP-22 (epics) | Tagged v1.0+v2.0 both; v1.0 shipped | Drop the v1.0 tag from the open remainder |
| GFP-34, GFP-45, GFP-46, GFP-141 | Thinnest descriptions of the open set (no explicit AC) | Add AC before scheduling |
| GFP-88 vs GFP-89 | Complementary but overlapping ("make thresholds configurable" vs "write down what must never be") | Do as one piece of work, GFP-89 first |
| GFP-113 vs GFP-147 | Overlap risk: "grocery list → online order" and "server-side sync" both touch ordering/infra | Cross-link; decide sequencing at v2 planning |

### 2.2 Hotfix cycle root-cause analysis (the high-signal finding)

All five releases shipped on **2026-08-04**:

| Tag | Content | Class |
|---|---|---|
| v1.0.0 | Release + README | — |
| v1.1.0 | GFP-158 rename to Protein Ledger | Branding |
| v1.1.1 | Rename fallout: macOS installer couldn't find the GUI | **Rename escape #1** |
| (unreleased) | Rename fallout ×2: 3 workflow files missed; CI grepped old agent name | **Rename escapes #2, #3** |
| v1.1.2 | GFP-159/161: installer friction (double-click, version "unknown") | Install UX |
| v1.1.3 | GFP-162: strip mark-of-the-web | Install UX |

**Pattern:** zero hotfixes in the optimiser/DB/scraper layers; 100% in packaging/CI/installer identity. Root cause is structural: the app's identity (task names, agent labels, bundle paths, release-folder layout) is spelled out in ≥3 independently-assembled places (GFP-160 documents three), and the guard tests assert the spellings rather than the relationships — so a rename passed every test and broke three times *after* being "done." The same class of failure will recur on the next identity-touching change (e.g. GFP-157 Apple distribution) unless GFP-160 lands first and guards become relational.

### 2.3 v2 roadmap draft (north star: nutritionist platform — per-client cost-per-gram-protein)

**M0 — Close out v1 (days):** transition GFP-159/161/162; fix GFP-121 (+ net-new NT-1 below); mark v1.0 released.
**M1 — Trustworthy data** *(the optimiser is only as good as its supply)*: GFP-121→GFP-44 (IngestManager, matching folded in), GFP-82 (dead-session detection), GFP-27 (size coverage), **decision ticket on GFP-5** (Food Lion shelf prices — the GFP-76 spike showed DataDome blocks every automatable route; either park it explicitly or scope a manual/CSV path; it currently gates GFP-8-class size coverage).
**M2 — Multi-client, multi-ZIP** *(the pivot's core mechanics)*: GFP-53 epic (GFP-56/57/58), GFP-83 (per-ZIP WF sessions), GFP-34 (backup/export — client records are irreplaceable; this is high-value, low-cost), GFP-47 (photos), GFP-154 (family groups).
**M3 — Distribution & credentials** *(what lets strangers install it)*: GFP-147→GFP-149 (broker, then remove shipped secret — sequenced after the GFP-119 ToS answer), GFP-160 (single release-folder assembly — do **before** GFP-157), GFP-157 (Apple distribution), GFP-150 (keep-data/delete-data), GFP-120 (telemetry).
**M4 — Optimiser depth** *(differentiators once the base is sound)*: GFP-141 (max-N-items constraint), GFP-143 (density objective), GFP-126 (monthly view), GFP-114 (chart overlay), GFP-145 (parked on its recorded decision), GFP-140 (formulas panel future — leaning retire-behind-a-flag given M2 priorities).
**Continuous — Engineering hygiene**: GFP-62 (CLAUDE.md), GFP-63 (db_script namespace), GFP-44/45/46 (service restructure), GFP-88+89+90 (config/constants/store identity), plus net-new NT-3/NT-4/NT-5.
**Park candidates** (don't serve the pivot near-term): GFP-113 (online ordering — revisit at M3 with the Task 4 findings), GFP-114 (nice-to-have), GFP-145 (already parked).

### 2.4 Net-new ticket drafts (proposal-only — not created)

**NT-1 — Run food matching as part of every ingest** *(Bug fix, pairs with GFP-121)*
Fold `matching.match_deals(conn)` into `run_scrape` (or IngestManager). AC: after a Flipp scrape of a fixture flyer, a known deal ranks in `rank_by_cost_per_gram_protein`; manual corrections survive (existing `match_source='manual'` guard covered by test).

**NT-2 — Grocery list must be built from the week plan and the active selection** *(Bug)*
`grocery_list_for` takes `selection`; aggregates the 7 `week_plan` days into purchase quantities. AC: list total == week-plan cost within a cent; vary-week ON produces the varied items; single-store ON produces a one-store list; CLI + GUI both pass it through.

**NT-3 — Make `db.connect()` cheap and concurrency-safe** *(Task)*
Early-out when `schema_version` is complete and verified; store-seed only on schema change; `journal_mode=WAL` + `busy_timeout`; share a connection per GUI window. AC: p95 connect < 5ms on a migrated DB; GUI + background scrape running simultaneously produce no `database is locked` in a soak test.

**NT-4 — Lint, types, and dependency audit in CI** *(Task)*
`ruff` (+`ruff format --check`) and `mypy --strict-ish` as a cheap ubuntu PR gate; `pip-audit` nightly; a `constraints.txt` lockfile consumed by release builds so shipped binaries are built from pinned, audited versions. AC: gate runs < 2 min; `test_ci_cost_guards.py` updated to bless the new job (see Task 3 for placement rationale).

**NT-5 — Enforce the protein-factor band in the service layer** *(Task)*
Reject `protein_factor` outside `[MIN, MAX]` in `service/clients` (both add and update), with the same worded error the GUI shows. AC: `gplan client set --factor 5` fails; GUI unchanged; a stored out-of-band legacy value still displays (read ≠ write validation).

*(Also worth filing when convenient: rotate-shipped-secret ops ticket; `display.py` helper consolidation under GFP-22; golden-payload tests for `scrapers/base.py`.)*

---

## Task 3 — Security Review

*(Manual review against OWASP Top 10 categories, re-interpreted for a local-first desktop app: no server, no multi-user auth — the equivalents are credential storage, the update/distribution channel, the scraper surface, and client PII at rest.)*

### Findings

**S1 — Cryptographic failures / sensitive data at rest (High).** Every secret and all client PII sit in plaintext with default ACLs in the user data dir: `kroger-env.config`, `wholefoods_session.json`, `licence.json`, `broker-cache.json` (**cached bearer tokens** — `broker.py:151-159`), and `grocery_planner.sqlite3` (name/weight/height/age/sex/notes per `0008_GFP-28.ddl`). No `chmod 0600`/`icacls` anywhere on write (`credentials.py:501`, `wholefoods.py:500`, `broker.py:155`). On a shared family machine this is readable by other local users. *Fix: restrict ACLs on the data dir at install + on every credential write; document that the DB holds health-adjacent PII (also strengthens GFP-150).*

**S2 — The shipped shared secret (Critical — same as C1).** Distribution channel doubles as credential channel. Compounding factor: `install.ps1:56-68,244` strips mark-of-the-web from unsigned binaries — necessary for UX (GFP-162) but it means the ZIP is both unauthenticated *and* de-quarantined. *Fix: rotate now; broker (GFP-147); and publish SHA-256 sums with each release so installers can verify before unblocking (cheap partial mitigation while code-signing remains unfunded — see install-friction notes).*

**S3 — Secrets on argv (Medium).** `gplan credentials --set-licence <key>` (`cli.py:1021-1046`) puts the licence key into shell history and the process table. *Fix: prompt interactively / read stdin; accept the flag but warn.*

**S4 — Log redaction has PII/traceback gaps (Medium).** `logs.py:72-84` redacts credential-shaped patterns (good, applied to both handlers) but has no patterns for client names/biometrics, and `jobs.py:165` `log.exception` writes full tracebacks — a future frame around `customers.py`/`bill.py` can carry client data past the regex. File handler is DEBUG-level. *Fix: keep tracebacks (they're the point) but add name-scrubbing when customer objects enter scrape/optimise paths, and consider INFO for the file handler by default.*

**S5 — Supply chain: floor-only pins, no lockfile (Medium).** All deps are `>=` with no ceiling and no lock; the installed set today is current (simpleeval **1.0.7** — clears CVE-2026-32640, the pre-1.0.5 sandbox escape via attribute chains; httpx 0.28.1; PySide6 6.11.1; APScheduler 3.11.3), but nothing prevents a release build resolving something older/newer and unaudited. **PySide6/QtWebEngine ships a full Chromium** — the largest CVE surface in the product, patched only as fast as PySide6 releases and your rebuilds. The minting dialog (`gui/wholefoods.py`) navigates a live third-party site with JS enabled in-process (off-the-record profile and single-cookie capture are good mitigations; no URL allowlist on the view). *Fix: NT-4's constraints/lockfile + pip-audit; rebuild+re-release on QtWebEngine security advisories; consider `setUrlRequestInterceptor` allowlisting `*.wholefoodsmarket.com`.*

**S6 — Uninstall deletion targets come from env vars (Low).** `uninstall.py:219-237` emits a TSV of resolved paths (relocatable via `GROCERY_PLANNER_DB/_CONFIG/_LOG_DIR/...`) that `uninstall.ps1`/`.sh` then `Remove-Item -Recurse`/`rm -rf`. A hostile env var at uninstall time deletes an arbitrary tree. Local-attacker-only, but cheap to bound. *Fix: refuse targets outside the user profile/data roots in the Python plan builder.*

**S7 — Injection surfaces: clean (Informational).** SQL parameterised throughout — the only interpolations are module constants and code-built clauses (`kroger.py:843-851`, `importers.py:116-130`, `db.py:229` names-from-`sqlite_master`, placeholder expansion in `nutrition.py:106` etc.). User formulas run through `simpleeval`, never `eval` (`formulas.py:48`, `targets.py:239`, `savings.py:602`); the `names` dict passes only floats — no attribute-chain fodder even on old simpleeval. Subprocess: list-form only, no `shell=True`, fixed commands from `install_paths` constants. Transport: httpx everywhere, no `verify=False`, explicit timeouts (5–45s) and a bounded retry with jitter honouring `Retry-After`. Updates: check-only by design (`updates.py:13-19`) and a test asserts it contains no download/exec primitives. This is a strong baseline worth preserving with the CI gate below.

### What a SAST/DAST scan would likely flag (to validate against a real run)

- **True positives to expect:** plaintext credential writes without mode bits (Semgrep `insecure-file-permissions` family won't fire on Windows-only code paths — expect it from manual rules or SonarQube "make sure permissions are sufficient"); `except Exception: pass` (`scheduler.py:186`); argv secret (Bandit B105-adjacent heuristics are hit-and-miss — may need a custom rule); unpinned requirements (pip-audit/Snyk will flag transitively once a lockfile exists to scan).
- **Expected false positives / accepted-risk noise:** every f-string SQL site listed in S7 (Bandit B608 / Semgrep `sqlalchemy-injection` analogues) — pre-annotate with `# nosec`/`nosemgrep` + a comment, or the report drowns; `subprocess` usage (B603/B607) — list-form and constant, accept; `hashlib.sha256` fine; the `_run` PowerShell folder-delete (`background.py:396-409`) will look like command construction — it interpolates a constant.
- **DAST is largely N/A** (no listening service). The closest equivalents worth doing instead: a QtWebEngine version check in CI against known-Chromium advisories, and a scripted verify of release-ZIP contents (no unexpected secrets beyond the sanctioned credential — and after GFP-149, none at all).

### Where scanning belongs in the pipeline

Respecting the GFP-94/95 cost posture (`test_ci_cost_guards.py` parses the workflows — any new job must be blessed there):
1. **PR gate (cheap, ubuntu-latest, ~1-2 min):** `ruff` + `mypy` + Semgrep (curated ruleset, baselined so only new findings fail). These catch the M3/L2-class issues at author time.
2. **Nightly (scheduled, not per-PR):** `pip-audit` against the lockfile + QtWebEngine/Chromium advisory check. Nightly because CVE feeds change daily independent of commits, and per-PR would waste the budget GFP-94 exists to protect.
3. **Pre-release (on tag, inside `release.yml`):** re-run pip-audit hard-fail + release-ZIP content verification + SHA-256 sum generation. The tag job already gates on version match; this extends the same "the artifact is what we think it is" stance.

---

## Task 4 — Store / Grocery API Expansion

### 4.1 Current integrations and where the friction actually was

| Source | Path | Auth | Friction (from spikes GFP-70/76/77 + tickets) |
|---|---|---|---|
| Food Lion, Harris Teeter (weekly ads) | Flipp (`scrapers/base.py`) | None (undocumented endpoint) | Shape drift risk (GFP-67 guards exist for it); **no sizes** (4.9%) → gates cost-per-unit (GFP-5/8 gap); no shelf prices |
| Harris Teeter (shelf) | Kroger public API (`scrapers/kroger.py`) | OAuth2 client_credentials via credential seam | Credential distribution (GFP-101/146→147/149), 10k calls/day per credential, ToS question open (**GFP-119 verdict pending — this blocks scaling customers on one key**) |
| Whole Foods | Session-cookie scrape (`scrapers/wholefoods.py` + QtWebEngine minting) | Hand-minted `wfm_store_d8` cookie per install | Cookie mortality (GFP-74 measured), per-ZIP sessions needed (GFP-83), dead-session UX (GFP-82); Amazon ToS risk uninvestigated — the WF equivalent of GFP-119 does not exist yet |
| Food Lion (shelf) | **Blocked** | — | DataDome rejects every automatable route incl. headed Playwright (GFP-76). Data quality behind the wall is excellent — but there is no compliant automated path. Recommend: park GFP-5 formally; revisit only if Ahold/PDL ships an API |

### 4.2 Kroger public Developer API — evaluation

**Verdict: it's already your best integration; deepen it rather than diversify first.** The GFP-77 spike (verified live) found Harris Teeter as chain `HART` with the best payload of any measured source: 100% priced, 100% machine-readable size, 82% protein grams — protein figures *from the store payload* bypass USDA matching entirely, which is precisely the app's hardest data problem (H1!).
Real constraints to plan around:
- **Rate limit:** Products API ~10,000 calls/day *per credential* (confirmed current). A full store scrape ≈ 20 calls → one credential supports ~500 store-scrapes/day theoretical, minus token exchanges and retries; per-customer metering only becomes real with the broker (GFP-147's design already counts per licence key).
- **Cart:** the public tier is effectively **write-only add** (`PUT /cart/add`) — no read-back, no remove/update without a partner-tier relationship. So "push the grocery list into the customer's Kroger cart" is feasible as a *one-shot add* (needs per-user OAuth authorization-code flow — a per-customer Kroger login, which changes the credential story), but full cart management is not available at this tier. Validate against the current portal docs before designing GFP-113 around it.
- **Coverage:** the same credential reaches every Kroger banner by location query — which is the SoCal story below, and also means Ralphs/Food 4 Less/Fred Meyer/King Soopers/Fry's/QFC etc. are one `filter.zipCode.near` away.
- **ToS (GFP-119)** remains the gating fact for the broker/customer model. Nothing in this section is worth building past spike level until that verdict is written down.

### 4.3 SoCal (Ralphs / Food 4 Less) — what it would actually take

Both banners are Kroger subsidiaries (both HQ'd in Compton, CA), so **the existing `scrapers/kroger.py` already speaks their protocol** — this genuinely is a two-for-one (three-for-one counting Harris Teeter). What's actually between here and a working LA market:
1. **Locations resolution per banner** — same lesson as GFP-77's `HART` red herring: don't trust `/v1/chains` names; query `/v1/locations?filter.zipCode.near=<LA ZIP>` and read `chain` back (expect `RALPHS`, `FOODSCO`/`FOOD4LESS` codes — verify empirically).
2. **Multi-ZIP mechanics** — already the GFP-53 epic (per-client ZIPs, pooled scraping, per-(store,ZIP) scheduling). SoCal adds no new engineering, only ZIP pool entries — the epic is the prerequisite either way.
3. **Store registry** — GFP-90 (store identity lives in three places) should land first; adding two banners today means touching all three.
4. **Rate budget** — two more banners × ZIPs draws the same 10k/day pool; fine for pilot scale, another argument for broker metering.
5. **The non-Kroger flank** — LA's Whole Foods works with the existing cookie flow (GFP-70 verified ZIP 90210 pins correctly!). No Food Lion equivalent gap: the discount flank would be Food 4 Less itself, via the same API.
**Effort estimate: days, not weeks, once GFP-53 lands** — a genuinely cheap market expansion if a SoCal client base materialises.

### 4.4 Alternative / complementary APIs

| Option | Auth complexity | Coverage | Cart/checkout write | Fit |
|---|---|---|---|---|
| **Instacart Developer Platform** | API key for shopping-list/recipe deep-links; full Connect fulfillment = partner agreement + OAuth2 | ~85k stores, 1,500+ banners (incl. Food Lion!) | **Deep-link handoff**: build a list, open Instacart to checkout — no true cart write at self-serve tier | Best near-term "make the list shoppable" play: turn the GFP-112 list into an Instacart link (their prices ≠ shelf prices — display honesty needed). Candidate implementation for GFP-113 without any partner contract |
| **Walmart (affiliate/Developer APIs)** | Affiliate program + API keys (Impact-mediated) | Walmart Supercenters — big overlap with a value-focused client base | Product/pricing lookup yes; no consumer cart write at affiliate tier | Worth a GFP-77-style spike purely as a *fourth price source* (their API exposes price + size); ordering not realistic |
| **Ahold/Peapod (Food Lion parent)** | No public API | — | — | Watch-list only; would unblock GFP-5 properly if it ever opens |
| *(Existing)* Flipp | None | Weekly ads for most US grocers | None | Keep as the deals/loss-leader layer it already is; its role shrinks as structured sources grow |

**Recommendation order:** 1) deepen Kroger (broker + GFP-119 + multi-banner), 2) Instacart deep-links for list handoff, 3) Walmart price-source spike. Nothing else earns a slot yet.

### 4.5 Abstraction layer — build on what exists, don't invent

The seams are already right; the work is finishing them, not designing new ones:
- **`scrapers.SCRAPERS` registry + `StoreConfig`** already separate "module" from "store" from "source" (`store_key_for`/`source_for`, GFP-98). Formalise the implicit scraper protocol (today: `scrape()`, optional `readiness()`, `DEFAULT_POSTAL_CODE`) into a documented `Protocol` class so integration #4 is a checklist, not archaeology.
- **`credentials.CredentialSpec` + provider seam** already abstracts auth (file/shipped/broker, env-overridable). New store auth = new spec entry, zero engine change — proven by Kroger.
- **The row contract is the real interface**: `importers.DEAL_COLUMNS` + the GFP-98/111/152 columns (`sold_by`, `price_per_unit*`, `weight_basis`, `product_identifier*`). Document it as *the* contract a scraper must emit (the Kroger module's "fold the priced size into `item_name`" discipline is load-bearing and currently lives only in docstrings).
- **GFP-44 (IngestManager) is the missing keystone**: one entry point that scrapes → guards → persists → **matches (NT-1)** → records → prunes. That's also where per-source quirks (match-row-writing scrapers vs Flipp) stop leaking into `service/ingest`.
- **Add an explicit `capabilities` declaration** per scraper (has_shelf_prices, has_sizes, has_protein, has_product_ids, needs_session) so UI/reporting can say *why* a store's data is thinner instead of implying the engine failed — the GFP-121 bug stayed invisible partly because nothing states which sources produce matchable rows.

---

## If I could only fix 5 things before the next release

1. **Rotate the Kroger secret and restrict credential-file ACLs** (C1/S1) — the only finding with off-machine blast radius; one ops action + ~20 lines.
2. **Make ingest match: `matching.match_deals()` inside `run_scrape`** (H1/GFP-121) — one line + test; un-blinds the optimiser to 297 Food Lion deals.
3. **Rebuild the grocery list from the week plan + selection** (H2) — the product's one hand-to-a-client artifact currently contradicts the screen it came from.
4. **`db.connect()` early-out + WAL + busy_timeout** (H3/H4) — removes the per-click migration replay, the UI stalls, and the looming two-process lock errors in one small PR.
5. **CI gate: ruff + mypy + lockfile + pip-audit** (NT-4) — the cheapest permanent floor under everything above; also the precondition for trusting the next dependency bump.

*Honourable mention: transition GFP-159/161/162 and mark v1.0 released — five minutes of Jira hygiene that makes every future report truthful.*

---

*Full working notes: Jira pull + analysis tables in the session scratchpad; test baseline `pytest -q` all-green at `2f7a663`.*
