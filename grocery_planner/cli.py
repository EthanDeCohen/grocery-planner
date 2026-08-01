"""Typer CLI entry point for grocery-planner (`gp`).

Local-first commands over the SQLite store:
    gplan import [PATH]     import data/<store>/*.csv into the DB
    gplan scrape STORE      fetch fresh deals (Food Lion implemented)
    gplan list deals|prices query stored rows
    gplan stores            show tracked stores + row counts
    gplan db-path           print the database location
    gplan formula ...       manage user-defined formulas
    gplan profile ...       set/get profile values used by formulas
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path

import typer

from . import __version__, db, formulas, importers, jobs, scheduler, service, usda
from .scrapers import SCRAPERS
from .stores import BY_KEY

app = typer.Typer(add_completion=False, help="Local-first grocery price/deal planner.")
formula_app = typer.Typer(help="Manage user-defined formulas.")
profile_app = typer.Typer(help="Set/get profile values (used as formula variables).")
schedule_app = typer.Typer(help="Automatic background refresh (GFP-7).")
nutrition_app = typer.Typer(help="Nutrition catalog (protein data): GFP-23/GFP-24.")
app.add_typer(formula_app, name="formula")
app.add_typer(profile_app, name="profile")
app.add_typer(schedule_app, name="schedule")
app.add_typer(nutrition_app, name="nutrition")


class Kind(str, Enum):
    deals = "deals"
    prices = "prices"


def _repo_data_dir() -> Path:
    """Default to the repo's data/ folder (next to this package)."""
    return Path(__file__).resolve().parents[1] / "data"


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"grocery-planner {__version__}")


@app.command("db-path")
def db_path_cmd() -> None:
    """Print the SQLite database path."""
    from .paths import db_path
    typer.echo(str(db_path()))


@app.command("import")
def import_cmd(
    path: Path = typer.Argument(None, help="data/ directory (default: repo data/)."),
) -> None:
    """Import CSVs (data/<store>/{prices,deals}.csv) into the database."""
    data_dir = path or _repo_data_dir()
    if not data_dir.is_dir():
        typer.secho(f"No data directory at {data_dir}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    conn = db.connect()
    results = importers.import_dir(conn, data_dir)
    if not results:
        typer.secho(f"No known store folders found under {data_dir}", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    for r in results:
        typer.echo(f"  {BY_KEY[r.store].display_name:14} deals={r.deals:<5} prices={r.prices:<5}"
                   + (f"  (skipped: {', '.join(r.skipped)})" if r.skipped else ""))
    total = sum(r.deals + r.prices for r in results)
    typer.secho(f"Imported {total} rows from {data_dir}", fg=typer.colors.GREEN)


@app.command()
def scrape(
    store: str = typer.Argument(..., help="Store key, e.g. 'foodlion' or 'harristeeter'."),
    postal_code: str = typer.Option(
        None, "--postal-code", "-z", help="ZIP for the flyer lookup (default: store's own)."
    ),
) -> None:
    """Scrape fresh deals for a store and store them in the DB."""
    scraper = SCRAPERS.get(store)
    if scraper is None:
        typer.secho(
            f"No scraper for {store!r}. Available: {', '.join(service.available_scrapers())}. "
            "Whole Foods is GFP-4.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(2)

    zip_code = postal_code or scraper.DEFAULT_POSTAL_CODE
    typer.echo(f"Scraping {scraper.MERCHANT} weekly ad for {zip_code} ...")
    result = service.run_scrape(store, postal_code=postal_code)
    flyer, stats = result["flyer"], result["stats"]
    typer.secho(
        f"Flyer {flyer.get('name')} ({flyer.get('id')}), valid {stats['valid_from']} to "
        f"{stats['valid_to']}: stored {stats['total']} deals — "
        f"{stats['weekly_ad']} weekly ad ({stats['no_price']} without a listed price), "
        f"{stats['digital_coupons']} digital coupons ({stats['bogo']} BOGO)"
        + (f"; skipped {stats['expired_items']} expired items" if stats["expired_items"] else "")
        + ".",
        fg=typer.colors.GREEN,
    )
    if stats["flyer_status"] != "active":
        typer.secho(
            f"Note: no active weekly ad right now — this flyer is {stats['flyer_status']}.",
            fg=typer.colors.YELLOW,
        )


@app.command("list")
def list_cmd(
    kind: Kind = typer.Argument(Kind.deals, help="deals or prices."),
    store: str = typer.Option(None, "--store", "-s", help="Filter by store key."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (0 = all)."),
    on_sale: bool = typer.Option(False, "--on-sale", help="Deals: only rows with a sale price."),
    hide_expired: bool = typer.Option(
        False, "--hide-expired", help="Deals: drop rows whose valid_to has passed."
    ),
    category: str = typer.Option(
        None, "--category", "-c", help="Deals: filter by sub-category (see `gplan categories`)."
    ),
    deal_type: str = typer.Option(
        "all", "--type", "-t", help="Deals: all | weekly | coupon | bogo."
    ),
    search: str = typer.Option(
        "", "--search", "-q", help="Deals: match item name or description."
    ),
    loyalty_only: bool = typer.Option(
        False, "--loyalty", help="Deals: only rows requiring a loyalty card."
    ),
    valid_on: str = typer.Option(
        None, "--valid-on", help="Deals: only rows on offer on YYYY-MM-DD."
    ),
) -> None:
    """List stored deals or prices."""
    conn = db.connect()
    if store and store not in BY_KEY:
        typer.secho(f"Unknown store {store!r}. Known: {', '.join(BY_KEY)}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if deal_type not in service.DEAL_TYPE_GROUPS:
        typer.secho(f"Unknown --type {deal_type!r}. Choose: "
                    f"{', '.join(service.DEAL_TYPE_GROUPS)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if valid_on and not _is_iso_date(valid_on):
        typer.secho(f"--valid-on expects YYYY-MM-DD, got {valid_on!r}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if kind is Kind.deals:
        # Freshness + filtering live in the service layer so the GUI matches.
        filters = dict(store=store, on_sale=on_sale, hide_expired=hide_expired,
                       category=category, deal_type=deal_type, search=search,
                       loyalty_only=loyalty_only, valid_on=valid_on)
        rows = service.fetch_deals(limit=limit, conn=conn, **filters)
        total = service.count_deals(conn=conn, **filters)
        _print_table(["store", "item", "sub_category", "sale", "price", "valid_to"],
                     [(BY_KEY[r["store"]].display_name, r["item_name"], r["sub_category"],
                       _money(r["sale_price"]), _money(r["dollar_price"]),
                       _valid_to(r["valid_to"], r["expired"]))
                      for r in rows])
        stale = sum(1 for r in rows if r["expired"])
        if stale:
            typer.secho(
                f"\n{stale} of the rows shown {'is' if stale == 1 else 'are'} EXPIRED — "
                f"re-run `gplan scrape {store or 'STORE'}`"
                " or pass --hide-expired.",
                fg=typer.colors.YELLOW,
            )
    else:
        clause, params = ("", [])
        if store:
            clause, params = " WHERE store=?", [store]
        lim = "" if limit == 0 else f" LIMIT {int(limit)}"
        sql = ("SELECT store, item_name, category, regular_price, sale_price, unit "
               f"FROM prices{clause} ORDER BY store, item_name{lim}")
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM prices{clause}", params).fetchone()[0]
        _print_table(["store", "item", "category", "regular", "sale", "unit"],
                     [(BY_KEY[r["store"]].display_name, r["item_name"], r["category"],
                       _money(r["regular_price"]), _money(r["sale_price"]), r["unit"] or "")
                      for r in rows])

    typer.secho(f"\n{len(rows)} shown of {total} {kind.value}.", fg=typer.colors.BLUE)


@app.command()
def best(
    store: str = typer.Option(None, "--store", "-s", help="Filter by store key."),
    category: str = typer.Option(None, "--category", "-c", help="Filter by sub-category."),
    search: str = typer.Option("", "--search", "-q", help="Match item name or description."),
    deal_type: str = typer.Option("all", "--type", "-t", help="all | weekly | coupon | bogo."),
    unit: str = typer.Option(
        None, "--unit", "-u", help="Compare within one base unit: oz, 'fl oz' or each."
    ),
    score_with: str = typer.Option(
        None, "--score", help="Rank by a saved formula instead of cost per unit."
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (0 = all)."),
    include_expired: bool = typer.Option(
        False, "--include-expired", help="Also rank deals that have already lapsed."
    ),
) -> None:
    """Rank deals by value — cheapest per ounce/each, or by your own formula.

    Deals whose size cannot be read from the ad copy are left out: there is no
    honest cost-per-unit for "Eggo Frozen Waffles".
    """
    if deal_type not in service.DEAL_TYPE_GROUPS:
        typer.secho(f"Unknown --type {deal_type!r}. Choose: "
                    f"{', '.join(service.DEAL_TYPE_GROUPS)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    filters = dict(store=store, category=category, search=search,
                   deal_type=deal_type, hide_expired=not include_expired)
    try:
        rows = service.best_deals(limit=0, score_with=score_with, **filters)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    matching = service.count_deals(**filters)

    if unit:
        rows = [r for r in rows if r["unit"] == unit]
        for position, row in enumerate(rows, start=1):
            row["rank"] = position  # re-number so the shown list reads 1..N
    shown = rows[:limit] if limit else rows

    if score_with:
        _print_table(
            ["#", "store", "item", "size", "price", "per unit", "score"],
            [(str(r["rank"]), BY_KEY[r["store"]].display_name, r["item_name"], r["size"],
              _money(r["price"]), _per_unit(r), f"{r['score']:.4g}") for r in shown],
        )
    else:
        _print_table(
            ["#", "store", "item", "size", "price", "per unit"],
            [(str(r["rank"]), BY_KEY[r["store"]].display_name, r["item_name"], r["size"],
              _money(r["price"]), _per_unit(r)) for r in shown],
        )

    # A $/oz and a $/fl oz cannot be ordered against each other — say so whether
    # the ranking came from unit price or from a formula that used it.
    units = sorted({r["unit"] for r in shown if r["unit"]})
    if len(units) > 1:
        typer.secho(
            f"\nMixing units ({', '.join(units)}) — only rows sharing a unit are "
            "comparable. Narrow with --unit or --category.",
            fg=typer.colors.YELLOW,
        )
    typer.secho(f"\n{len(shown)} ranked of {len(rows)} comparable deals.",
                fg=typer.colors.BLUE)
    # Never let a short ranking imply the ad was short — say what was excluded.
    skipped = matching - len(rows)
    if skipped > 0:
        typer.secho(
            f"{skipped} of the {matching} matching deals carry no readable size in the "
            "ad copy, so they cannot be priced per unit. Weekly-ad names often omit "
            "sizes entirely (\"Ben & Jerry's Ice Cream\"); shelf-price capture (GFP-5) "
            "is what closes that gap.",
            fg=typer.colors.YELLOW,
        )


@app.command()
def export(
    path: Path = typer.Argument(..., help="Destination .csv file."),
    store: str = typer.Option(None, "--store", "-s", help="Filter by store key."),
    category: str = typer.Option(None, "--category", "-c", help="Filter by sub-category."),
    search: str = typer.Option("", "--search", "-q", help="Match item name or description."),
    deal_type: str = typer.Option("all", "--type", "-t", help="all | weekly | coupon | bogo."),
    include_expired: bool = typer.Option(
        False, "--include-expired", help="Also export deals that have lapsed."
    ),
) -> None:
    """Export the matching deals to CSV (opens in Excel, Sheets or Numbers)."""
    if deal_type not in service.DEAL_TYPE_GROUPS:
        typer.secho(f"Unknown --type {deal_type!r}. Choose: "
                    f"{', '.join(service.DEAL_TYPE_GROUPS)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    written = service.export_deals(
        path, store=store, category=category, search=search, deal_type=deal_type,
        hide_expired=not include_expired,
    )
    typer.secho(f"Wrote {written} deals to {path}.", fg=typer.colors.GREEN)


@app.command()
def categories(
    store: str = typer.Option(None, "--store", "-s", help="Limit to one store key."),
) -> None:
    """List the deal sub-categories available to `list deals --category`."""
    conn = db.connect()
    names = service.deal_categories(store=store, conn=conn)
    _print_table(
        ["category", "deals"],
        [(name, str(service.count_deals(store=store, category=name, conn=conn)))
         for name in names],
    )


@app.command()
def stores() -> None:
    """Show tracked stores and row counts."""
    conn = db.connect()
    rows = []
    for r in conn.execute("SELECT key, display_name FROM stores ORDER BY display_name"):
        d = conn.execute("SELECT COUNT(*) FROM deals WHERE store=?", (r["key"],)).fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM prices WHERE store=?", (r["key"],)).fetchone()[0]
        rows.append((r["key"], r["display_name"], str(d), str(p)))
    _print_table(["key", "store", "deals", "prices"], rows)


@schedule_app.command("set")
def schedule_set(
    store: str = typer.Argument(..., help="Store key to refresh automatically."),
    every: str = typer.Option(None, "--every", "-e", help="Interval, e.g. 30m, 6h, 2d."),
    cron: str = typer.Option(None, "--cron", help="Cron expression, e.g. \"0 6 * * *\"."),
) -> None:
    """Refresh a store on a cadence: gplan schedule set foodlion --every 12h."""
    if bool(every) == bool(cron):
        typer.secho("Pass exactly one of --every or --cron.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    kind = scheduler.INTERVAL if every else scheduler.CRON
    expression = every or cron
    try:
        scheduler.set_schedule(db.connect(), store, kind, expression)
    except service.UnknownStoreError:
        typer.secho(f"No scraper for {store!r}. Available: "
                    f"{', '.join(service.available_scrapers())}.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    except scheduler.ScheduleError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    upcoming = scheduler.next_run(kind, expression)
    typer.secho(
        f"{store} will refresh {scheduler.describe(kind, expression)}"
        + (f"; next run {upcoming:%Y-%m-%d %H:%M}." if upcoming else "."),
        fg=typer.colors.GREEN,
    )
    typer.echo("Run `gplan schedule run` to start the scheduler.")


@schedule_app.command("list")
def schedule_list() -> None:
    """Show the configured refresh cadences."""
    conn = db.connect()
    rows = scheduler.list_schedules(conn)
    table = []
    for r in rows:
        upcoming = scheduler.next_run(r["kind"], r["expression"])
        last = jobs.last_success(conn, r["store"])
        table.append((
            r["store"],
            scheduler.describe(r["kind"], r["expression"]),
            "yes" if r["enabled"] else "no",
            f"{upcoming:%Y-%m-%d %H:%M}" if upcoming else "-",
            f"{last:%Y-%m-%d %H:%M}" if last else "never",
        ))
    _print_table(["store", "cadence", "enabled", "next run", "last success"], table)


@schedule_app.command("remove")
def schedule_remove(store: str = typer.Argument(..., help="Store key to stop refreshing.")) -> None:
    """Delete a store's refresh cadence."""
    if not scheduler.remove_schedule(db.connect(), store):
        typer.secho(f"No schedule for {store!r}.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
    typer.secho(f"Removed the schedule for {store}.", fg=typer.colors.GREEN)


@schedule_app.command("run")
def schedule_run(
    once: bool = typer.Option(
        False, "--once", help="Do the catch-up pass and exit instead of waiting."
    ),
) -> None:
    """Run the background scheduler (Ctrl-C to stop).

    Catches up anything overdue first — a machine that was asleep through its
    window refreshes now rather than waiting for the next one.
    """
    conn = db.connect()
    if not scheduler.list_schedules(conn, enabled_only=True):
        typer.secho("No schedules configured. Try: gplan schedule set foodlion --every 12h",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    summary = scheduler.run_catch_up(conn, on_event=lambda m: typer.echo(f"  {m}"))
    typer.secho(
        f"Catch-up: {len(summary['ran'])} scraped, {len(summary['failed'])} failed, "
        f"{summary['reaped']} interrupted job(s) reaped.",
        fg=typer.colors.BLUE,
    )
    if once:
        raise typer.Exit(1 if summary["failed"] else 0)

    engine = scheduler.build_scheduler(conn, blocking=True)
    for job in engine.get_jobs():
        typer.echo(f"  scheduled: {job.name}")
    typer.secho("Scheduler running — press Ctrl-C to stop.", fg=typer.colors.GREEN)
    try:
        engine.start()
    except (KeyboardInterrupt, SystemExit):
        typer.secho("\nScheduler stopped.", fg=typer.colors.YELLOW)


@app.command("jobs")
def jobs_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (0 = all)."),
    store: str = typer.Option(None, "--store", "-s", help="Filter by store key."),
) -> None:
    """Show the history of automatic scrape runs."""
    rows = jobs.recent_jobs(db.connect(), limit=limit, store=store)
    _print_table(
        ["id", "store", "status", "started", "finished", "message"],
        [(str(r["id"]), r["source"], r["status"], (r["started_at"] or "")[:16],
          (r["finished_at"] or "")[:16], r["message"] or r["last_checkpoint"] or "")
         for r in rows],
    )


@formula_app.command("set")
def formula_set(name: str, expression: str, description: str = typer.Option("", "--desc")) -> None:
    """Define or update a formula: gplan formula set target_protein "weight * 1.6"."""
    formulas.set_formula(db.connect(), name, expression, description)
    typer.secho(f"Saved formula {name!r}.", fg=typer.colors.GREEN)


@formula_app.command("list")
def formula_list() -> None:
    """List stored formulas."""
    rows = formulas.list_formulas(db.connect())
    _print_table(["name", "expression", "description"],
                 [(r["name"], r["expression"], r["description"] or "") for r in rows])


@formula_app.command("eval")
def formula_eval(
    name: str,
    var: list[str] = typer.Option(None, "--var", "-v", help="Extra var, key=value (repeatable)."),
) -> None:
    """Evaluate a formula against profile values (+ optional --var overrides)."""
    extra: dict[str, float | str] = {}
    for item in var or []:
        if "=" not in item:
            typer.secho(f"Bad --var {item!r}; expected key=value", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        k, v = item.split("=", 1)
        try:
            extra[k] = float(v)
        except ValueError:
            extra[k] = v
    try:
        result = formulas.evaluate(db.connect(), name, extra)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"{name} = {result}")


@profile_app.command("set")
def profile_set(key: str, value: str) -> None:
    """Set a profile value: gplan profile set weight 82."""
    conn = db.connect()
    conn.execute("INSERT INTO profile(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    typer.secho(f"profile[{key}] = {value}", fg=typer.colors.GREEN)


@profile_app.command("list")
def profile_list() -> None:
    """List profile values."""
    rows = db.connect().execute("SELECT key, value FROM profile ORDER BY key").fetchall()
    _print_table(["key", "value"], [(r["key"], r["value"]) for r in rows])


@nutrition_app.command("sync")
def nutrition_sync() -> None:
    """Load the vendored USDA FoodData Central snapshot (GFP-24).

    Supersedes curated (hand-entered, source='curated') protein figures with
    sourced USDA ones (source='usda', source_ref=FDC id) wherever the
    snapshot has a match; leaves anything else curated and clearly marked.
    Reads a small vendored JSON file, not the network -- offline-capable by
    design. Safe to run more than once.
    """
    try:
        result = usda.sync()
    except usda.SnapshotError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    typer.secho(
        f"USDA sync: {result.superseded} curated food(s) superseded with sourced "
        f"data, {result.already_usda} already sourced, {result.inserted} new "
        f"USDA food(s) added.",
        fg=typer.colors.GREEN,
    )
    counts = usda.source_counts()
    typer.echo(
        f"foods table now: {counts.get('usda', 0)} sourced (usda), "
        f"{counts.get('curated', 0)} estimated (curated)."
    )
    if result.unmatched_curated:
        typer.secho(
            f"{len(result.unmatched_curated)} curated food(s) have no USDA match "
            f"in the vendored snapshot and remain estimates: "
            f"{', '.join(result.unmatched_curated)}.",
            fg=typer.colors.YELLOW,
        )


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else ""


def _per_unit(row) -> str:
    """Render a cost-per-unit with enough precision for cheap-per-ounce items."""
    value = row.get("unit_price")
    if value is None:
        return ""
    return f"${value:.3f}/{row['unit']}" if value < 1 else f"${value:.2f}/{row['unit']}"


def _valid_to(value: str | None, expired: int) -> str:
    """Render a deal's end date, marking it when the deal is already stale."""
    if not value:
        return ""
    return f"{value} (expired)" if expired else value


def _print_table(headers: list[str], rows: list[tuple]) -> None:
    if not rows:
        typer.secho("(no rows)", fg=typer.colors.YELLOW)
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = min(max(widths[i], len(str(cell))), 48)
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    typer.secho(fmt.format(*headers), bold=True)
    typer.echo(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        typer.echo(fmt.format(*(str(c)[:48] for c in row)))


if __name__ == "__main__":
    app()
