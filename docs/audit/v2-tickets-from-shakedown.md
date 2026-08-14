# v2 backlog from the v1.x shakedown — filed

**Filed in Jira 2026-08-04. Jira is the source of truth; this page is the index.**
Source: `docs/audit/v1-shakedown-2026-08.md` (Confluence 11829249), `main` @ `2f7a663` (v1.1.3).

52 issues created: **GFP-163 … GFP-214**, all tagged fixVersion `v2.0 - Multi-ZIP, multi-store, distributable`.

An earlier draft of this page used `NT-` numbers as placeholders (net-new ticket) because the items had no GFP keys yet. **That numbering is retired** — everything below has a real key.

---

## Epics

| Key | Epic |
| --- | --- |
| [GFP-163](https://decohen-partners.atlassian.net/browse/GFP-163) | Hardening: security, performance, and the engineering floor |
| [GFP-164](https://decohen-partners.atlassian.net/browse/GFP-164) | decohen-partners.com: hosted data service so no API key ever ships |
| [GFP-165](https://decohen-partners.atlassian.net/browse/GFP-165) | Market expansion: every reachable grocery API in LA, Philadelphia, North Carolina |
| [GFP-166](https://decohen-partners.atlassian.net/browse/GFP-166) | Proximity: deterministic distance in miles |
| [GFP-167](https://decohen-partners.atlassian.net/browse/GFP-167) | Invite-only web app (starts parked, behind a decision) |

---

## GFP-163 — Hardening

| Key | Summary | Priority |
| --- | --- | --- |
| GFP-168 | Make `db.connect()` cheap and concurrency-safe | Highest |
| GFP-170 | Rank once per interaction instead of tens of times | High |
| GFP-172 | Restrict file permissions on credentials and the client PII database | High |
| GFP-177 | Add lint, type checking, and a dependency lockfile | High |
| GFP-179 | Guards must assert relationships, not spellings | High |
| GFP-182 | Golden-payload tests for the Flipp parser | High |
| GFP-174 | Close the PII gaps in log redaction | Medium |
| GFP-176 | Fence the Whole Foods minting webview | Medium |
| GFP-178 | Security scanning in CI: PR, nightly, on-tag | Medium |
| GFP-180 | Small-correctness tail: date adapters, lock race, leaked connection | Medium |
| GFP-183 | Cover the untested service modules and GUI money paths | Medium |
| GFP-173 | Stop accepting the licence key as a command-line argument | Low |
| GFP-175 | Bound the uninstaller's deletion targets | Low |
| GFP-181 | Delete what is dead and fix the docs that lie | Low |

Filed under other epics because they belong there:

| Key | Summary | Parent |
| --- | --- | --- |
| GFP-169 | Grocery list must be built from the week plan and the active selection (Bug) | GFP-22 |
| GFP-184 | Declare what each scraper can actually produce | GFP-22 |
| GFP-185 | Enforce the protein-factor band in the service layer | GFP-19 |
| GFP-171 | Rotate the shipped Kroger client_secret (ops) | GFP-78 |

**GFP-121** (the only open Bug) now has fixVersion v2.0, parent GFP-22, and acceptance criteria in a comment: `service/ingest.run_scrape` never calls `matching.match_deals`, so 297 priced Food Lion deals never reach the optimiser. One line plus a regression test.

---

## GFP-164 — Hosted data service

Existing **GFP-147** (host the key server-side) and **GFP-149** (remove the shipped credential) were re-parented here.

| Key | Summary | Priority |
| --- | --- | --- |
| GFP-186 | Buy decohen-partners.com, stand up DNS and TLS | Highest |
| GFP-187 | AWS account baseline: IaC and cost guardrails | Highest |
| GFP-188 | The Kroger credential lives only in AWS, never in a client | Highest |
| GFP-189 | Server-side scheduled ingest: one scrape per (store, ZIP) per day | Highest |
| GFP-190 | Sync API: installs pull deals for their ZIPs | Highest |
| GFP-191 | Client-side sync provider replaces local scraping | High |
| GFP-192 | **QuotaPool**: generalised daily call budget for any rate-limited source | High |
| GFP-193 | Aggregate ZIP demand across all installs into one schedule | High |
| GFP-194 | Install identity and invite keys | High |
| GFP-195 | Observability: quota burn, sync health, what got skipped | Medium |
| GFP-196 | Write down the ToS position for the hosted model | Medium |

Two design constraints run through all of these:
* **No PII goes up.** ZIP codes and an install identity, never client names, weights or plans. That keeps the desktop product's privacy property intact even once a server exists.
* **Pool from day one.** 10,000 calls/day is *per credential*, and Harris Teeter, Ralphs and Food 4 Less all draw on the same pool. GFP-101 recommended a credential pool; GFP-119's closure makes it more necessary, not less — with no revenue there's no paid tier to escape to.

---

## GFP-165 — Market expansion

| Key | Summary | Priority |
| --- | --- | --- |
| GFP-197 | Triage matrix: every chain in all three metros | Highest |
| GFP-198 | LA Kroger banners: Ralphs and Food 4 Less | High |
| GFP-199 | Albertsons family: Vons/Pavilions (LA) + ACME (Philly) | High |
| GFP-201 | **Aldi**: find a route to price data | High |
| GFP-202 | **Lidl**: find a route to price data | High |
| GFP-207 | Onboarding runbook: triaged chain → store keyed by ZIP | High |
| GFP-200 | Ahold Delhaize: Giant (Philly), and whether it reopens Food Lion | Medium |
| GFP-203 | Philadelphia regionals: ShopRite, Wegmans, Weis | Medium |
| GFP-204 | North Carolina regionals: Publix, Lowes Foods, Ingles | Medium |
| GFP-205 | Los Angeles regionals and independents | Low |
| GFP-206 | National specialty: Sprouts, Trader Joe's, Whole Foods coverage | Low |
| GFP-208 | Spike: Walmart as a fourth price source | Low |

Triage before building — GFP-197 gates the rest. Each chain gets four columns from GFP-77's method: markets, API/Flipp/blocked, and the three that actually decide viability — **% priced, % machine-readable size, % protein grams**. Flipp gives sizes for 4.9% of items, which is why cost-per-unit covers so little today.

Hard prerequisites, both already on the board: **GFP-90** (store identity lives in three places) and **GFP-53** (per-client ZIP, pooled scraping).

---

## GFP-166 — Proximity

| Key | Summary | Priority |
| --- | --- | --- |
| GFP-209 | Ship ZIP centroid coordinates as static data | High |
| GFP-210 | Persist store coordinates from the source payloads | High |
| GFP-211 | Compute distance in miles and show it where a store is offered | Medium |
| GFP-212 | Selection constraint: maximum store distance in miles | Medium |

Deterministic means: ZIP centroids ship with the app, store coordinates come from the payload (Kroger's locations endpoint returns lat/long directly and it's currently discarded), haversine computed locally, **no network call on the distance path, ever**. Straight-line, not driving distance — road distance needs a routing service, which is a live dependency and non-deterministic by nature. The UI must say straight-line rather than implying drive time.

---

## GFP-167 — Invite-only web app (parked)

| Key | Summary | Priority |
| --- | --- | --- |
| GFP-213 | **DECISION**: does the product move to an invite-only website? | High |
| GFP-214 | Invite and account model (parked pending the decision) | Low |

The decision is not really desktop-vs-web. It's whether **client PII leaves the machine** — name, weight, height, age, sex, notes per `0008_GFP-28.ddl`, about third parties who agreed to nothing. GFP-213 names a third option worth evaluating on its own terms: web for the nutritionist's account and the deals, with client records staying local or end-to-end encrypted.

---

## Still outstanding — board hygiene

Not filed as tickets; these are transitions someone has to make.

| Action | Issues |
| --- | --- |
| Transition to Done, set fixVersion | GFP-159, GFP-161 → v1.1.2 · GFP-162 → v1.1.3 |
| Close — all children Done | GFP-20, GFP-21 |
| Mark fixVersion **v1.0 Released**, date 2026-08-04 | — |
| Create fixVersion v1.1, or fold the hotfixes into v1.0's note | — |
| Drop the shipped v1.0 tag from the open remainder | GFP-1, GFP-18, GFP-19, GFP-22 |
| Record the decision and park it, or scope a manual/CSV path | GFP-5 (GFP-27 is gated on it and should say so) |
| Comment the Instacart finding — deep-link handoff works at the self-serve tier, true cart write does not | GFP-113 |
| Add ACs before scheduling | GFP-34, GFP-45, GFP-46, GFP-141 |
| Do as one piece of work, GFP-89 first | GFP-88 + GFP-89 |

---

## Suggested order

1. **GFP-171** — rotate the secret (ops, today, no code)
2. **GFP-121** — one line plus a test; un-blinds 297 Food Lion deals
3. **GFP-169** — the list must match the plan
4. **GFP-168** — connect() early-out + WAL + busy_timeout
5. **GFP-177** — the CI floor everything else stands on

Then **GFP-186/187/188** to start the service, **GFP-197** to make the market epic schedulable, and **GFP-179** before any work on GFP-157.
