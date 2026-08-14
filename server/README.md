# server/ — the v2 hosted service, simulated locally (GFP-164)

Two containers that stand in for what decohen-partners.com would run. **Nothing
here ships to a customer** — the deployment note in the project's decisions is
that there are no containers customer-side, because of the WSL barrier on
Windows. This is server-side only, and the desktop app talks to it over HTTP.

```
  desktop app  ──HTTP──>  broker  ──Kroger API──>  Kroger        (credentialled)
                            │
                            └────>  renderer  ──Playwright──>  JS-only stores
```

## Why two containers and not one

They fail differently and they scale differently.

- **broker** is cheap, stateless-ish, and holds the one thing that must never
  ship: the Kroger `client_secret`. It is the only component that needs a
  secret store, and it is the component whose quota is a hard ceiling
  (10,000 calls/day *per credential*, GFP-192).
- **renderer** is expensive — a real Chromium per page — and holds no secret at
  all. It exists because the 2026-08-09 probing found that Publix, Wegmans,
  Lowes Foods, ShopRite and Albertsons all serve a JavaScript shell to plain
  HTTP and render their catalogue in the browser. It is also the component we
  would most plausibly *replace with a paid service* when volume justifies it.

Keeping them separate is what makes that swap a configuration change rather
than a rewrite: the broker talks to `RENDERER_URL`, and does not care whether
that is our container or somebody's API.

## Pooling

Both pools that matter live in the broker, because both are properties of the
service rather than of an install:

- **ZIP demand.** Many installs in one ZIP produce ONE upstream scrape
  (GFP-56). The broker caches by `(store, zip)` and serves every install from
  the same fetch.
- **Credential quota.** Every Kroger banner draws on the same daily budget, so
  the ceiling is per credential and not per store (GFP-192). The broker is the
  only place that can see the whole draw and therefore the only place that can
  ration it.

## Running it

```
docker compose -f server/docker-compose.yml up --build
curl localhost:8081/health          # renderer
curl localhost:8080/health          # broker
curl "localhost:8080/deals?store=harristeeter-api&zip=27401"
curl localhost:8080/quota
```

## What this is NOT

- Not production infrastructure. No auth, no TLS, no persistence beyond a
  process-lifetime cache. GFP-186/187 own the real thing.
- Not a path for client data. The desktop app sends a **store and a ZIP**, and
  nothing else. No client name, weight, target or plan leaves the machine —
  the constraint that runs through the whole hosted design.
