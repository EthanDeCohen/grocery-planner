"""Typer CLI entry point for grocery-planner (`gp`).

Local-first commands over the SQLite store:
    gplan import [PATH]     import data/<store>/*.csv into the DB
    gplan scrape STORE      fetch fresh deals (Food Lion implemented)
    gplan list deals|prices query stored rows
    gplan stores            show tracked stores + row counts
    gplan records           all-time record low/high per item (GFP-75)
    gplan trends            price / $/g protein over time (GFP-40)
    gplan config            show global settings and where each came from
    gplan logs              where the logs are, and the tail of the current one
    gplan db-path           print the database location
    gplan client ...        add/edit/remove clients (GFP-33)
    gplan formula ...       manage user-defined formulas
    gplan profile ...       set/get profile values used by formulas
"""
from __future__ import annotations

import os
from datetime import date
from enum import Enum
from pathlib import Path

import typer

from . import (
    __version__,
    credentials,
    db,
    config as app_config,
    formulas,
    logs,
    importers,
    install_paths,
    jobs,
    nutrition,
    scheduler,
    service,
    usda,
)
from . import records as rec
from .scrapers import SCRAPERS
from .service import clients as client_service
from .stores import BY_KEY

app = typer.Typer(add_completion=False, help="Local-first grocery price/deal planner.")


@app.callback()
def _main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Turn console logging up to DEBUG for this run (GFP-87).",
    ),
) -> None:
    """Runs before every command.

    GFP-86 configures logging once, as the process starts: idempotent, never
    raises, and degrades to console-only if the log directory cannot be
    created -- diagnostics must not be a reason the app fails to run.

    GFP-87: --verbose is what makes "please reproduce it with logging turned
    up" a thing a user can actually be asked to do. The FILE always keeps
    DEBUG; this only changes what reaches the console, because the file is the
    forensic record and the console is for the person watching.
    """
    import logging

    level = logging.DEBUG if verbose else getattr(
        logging, app_config.log_level(), logging.WARNING
    )
    logs.setup(level=level)
formula_app = typer.Typer(help="Manage user-defined formulas.")
profile_app = typer.Typer(help="Set/get profile values (used as formula variables).")
schedule_app = typer.Typer(help="Automatic background refresh (GFP-7).")
nutrition_app = typer.Typer(help="Nutrition catalog (protein data): GFP-23/GFP-24.")
client_app = typer.Typer(help="Clients: add, edit, remove, restore (GFP-33).")
app.add_typer(formula_app, name="formula")
app.add_typer(profile_app, name="profile")
app.add_typer(schedule_app, name="schedule")
app.add_typer(nutrition_app, name="nutrition")
app.add_typer(client_app, name="client")


class Kind(str, Enum):
    deals = "deals"
    prices = "prices"


def _repo_data_dir() -> Path:
    """Default to the repo's data/ folder (next to this package)."""
    return Path(__file__).resolve().parents[1] / "data"


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"{install_paths.APP_DISPLAY_NAME} {__version__}")


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
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Scrape and report what WOULD be written, without touching the database.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help=(
            "Bypass the GFP-67 replace-guards and accept a zero-row or "
            "implausibly-small scrape anyway. Only pass this once you've "
            "independently confirmed the low count is real (e.g. via "
            "flipp.com) and not upstream (Flipp) breakage."
        ),
    ),
    limit: int = typer.Option(
        None, "--limit", "-n", min=1,
        help=(
            "Fetch at most N products. Only for catalogue scrapers that crawl "
            "product pages (aldi-storefront, lidl-catalogue, sprouts-storefront, "
            "traderjoes); a bounded run is how you sample a source that is "
            "rate-policed. The bound is reported in the run's stats."
        ),
    ),
) -> None:
    """Scrape fresh deals for a store and store them in the DB."""
    scraper = SCRAPERS.get(store)
    if scraper is None:
        typer.secho(
            f"No scraper for {store!r}. Registered: {', '.join(service.all_scrapers())}.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(2)

    # GFP-4: registered and ready are different questions (Whole Foods needs
    # a hand-minted session cookie before a scrape can do anything useful --
    # see scrapers/wholefoods.py). Check and report this BEFORE attempting
    # anything, so a missing/dead cookie is a clean, expected exit rather
    # than an unhandled traceback partway through run_scrape().
    status = service.scraper_status(store)
    if not status.ready:
        typer.secho(
            f"{scraper.MERCHANT} is registered but not ready to scrape: {status.reason}",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(2)

    # Checked HERE, not inside run_scrape, for the same reason the readiness
    # check above is: a --limit the scraper cannot honour is a typo, and a typo
    # must not open a scraping_jobs row and then mark it failed. jobs.last_success
    # reads those rows, and a fake failure is worse than no row at all.
    if limit is not None and not service.supports_limit(scraper):
        typer.secho(
            f"{scraper.MERCHANT} does not take a --limit; it fetches whatever "
            f"its source publishes. Scrapers that do: "
            f"{', '.join(service.scrapers_supporting_limit())}.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(2)

    zip_code = postal_code or scraper.DEFAULT_POSTAL_CODE
    bound = f" (at most {limit} products)" if limit is not None else ""
    typer.echo(f"Scraping {scraper.MERCHANT} weekly ad for {zip_code}{bound} ...")
    try:
        # GFP-86/GFP-105: the TRACKED path, not service.run_scrape directly.
        # Two things followed from the CLI bypassing it: nothing was logged, and
        # -- worse -- no scraping_jobs row was written, so `gplan scrape` was
        # invisible to jobs.last_success. The app would then decide a refresh
        # was still due and scrape the same store AGAIN on next launch, which
        # is precisely the double-scrape GFP-105 exists to prevent. A scrape is
        # a scrape whoever started it.
        if dry_run:
            # Not tracked as a job: a dry run did not scrape FOR the database,
            # so recording it would make jobs.last_success claim a refresh that
            # never landed -- and GFP-105 would then skip a real one.
            result = service.run_scrape(
                store, postal_code=postal_code, force=force, dry_run=True, limit=limit
            )
        else:
            result = jobs.run_tracked_scrape(
                store, postal_code=postal_code, force=force, limit=limit
            )
    except service.UnsupportedLimitError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(2)
    except service.ScrapeGuardError as exc:
        # GFP-71: without --force, a guard tripping here used to be a dead
        # end -- nothing user-facing could pass force=True to run_scrape(),
        # so a genuinely tiny (or empty) ad week left the store permanently
        # unscrapeable. str(exc) already explains what happened and why;
        # this just adds the concrete way out.
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.secho(
            "Retry with --force once you've confirmed this is real, not "
            "upstream breakage.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(1)
    if result.get("dry_run"):
        typer.secho(
            f"DRY RUN — nothing was written. Would have replaced "
            f"{result['would_replace']} stored row(s) with {result['would_write']} "
            f"scraped row(s) for {zip_code}.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit()

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
def records(
    store: str = typer.Option(None, "--store", "-s", help="Filter by store key."),
    postal_code: str = typer.Option(None, "--postal-code", "-z", help="Filter by ZIP."),
    search: str = typer.Option(None, "--search", "-q", help="Match item name."),
    by: str = typer.Option("cpgp", "--by", help="Rank by 'cpgp' or 'price'."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (0 = all)."),
    window: int = typer.Option(
        0, "--window", "-w", help="Also show a rolling low/high over N days (e.g. 30)."
    ),
    backfill: bool = typer.Option(
        False, "--backfill", help="Seed records from existing price history, then show them."
    ),
) -> None:
    """All-time record low/high per item (GFP-75).

    Records are kept on write and never pruned, so they answer "is this
    genuinely a good price, ever?" long after the observation behind them has
    been retired by the retention policy.
    """
    conn = db.connect()

    if backfill:
        totals = rec.backfill_from_history(conn)
        typer.echo(
            f"Backfilled {totals['days']} store-days of history: "
            f"{totals['created']} items created, {totals['updated']} updated."
        )

    summary = rec.count_records(conn)
    if summary["items"] == 0:
        typer.echo(
            "No records yet. They accumulate as scrapes run — or seed them from "
            "the history already captured with `gplan records --backfill`."
        )
        raise typer.Exit()

    typer.echo(
        f"{summary['items']} items tracked · {summary['with_cpgp']} with a $/g protein "
        f"record · {summary['established']} with 3+ observations"
    )

    try:
        found = rec.fetch_records(
            conn, store=store, postal_code=postal_code, search=search,
            order_by=by, limit=limit,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=2)

    headers = ["item", "store", "low", "on", "high", "$/g low", "obs"]
    if window:
        headers += [f"{window}d low", f"{window}d high"]

    rows = []
    for r in found:
        # A record built on one or two sightings is today's price wearing a
        # hat; mark it rather than let it read as authoritative.
        marker = " ?" if r.is_thin else ""
        row = [
            r.item_name[:44],
            r.store,
            _money(r.record_low_price),
            (r.record_low_price_at or "-"),
            _money(r.record_high_price),
            f"{r.record_low_cpgp:.4f}" if r.record_low_cpgp is not None else "-",
            f"{r.observations}{marker}",
        ]
        if window:
            w = rec.rolling_window(conn, r.store, r.postal_code, r.item_name, days=window)
            row += [_money(w.low_price), _money(w.high_price)]
        rows.append(tuple(row))

    _print_table(headers, rows)
    if any(r.is_thin for r in found):
        typer.echo("? = fewer than 3 observations, so the 'record' is barely a record yet.")


def _money(value: float | None) -> str:
    return f"${value:.2f}" if value is not None else "-"


@app.command()
def trends(
    metric: str = typer.Option(
        "protein", "--metric", "-m", help="'protein' ($/g protein) or 'price' ($)."
    ),
    by: str = typer.Option("store", "--by", "-b", help="Series per 'store' or per 'food'."),
    days: int = typer.Option(
        service.DEFAULT_WINDOW_DAYS, "--days", "-d", help="Window length in days."
    ),
    store: str = typer.Option(None, "--store", "-s", help="Limit to one store key."),
    food: str = typer.Option(None, "--food", "-f", help="Limit to one food (name or slug)."),
    postal_code: str = typer.Option(None, "--postal-code", "-z", help="Limit to one ZIP."),
    meat: bool = typer.Option(
        False, "--meat",
        help="Animal protein only (meat and seafood), the chart's Animal protein tab.",
    ),
    points: bool = typer.Option(
        False, "--points", "-p", help="Print every day's value, not just the summary."
    ),
) -> None:
    """Price and $/g protein over time, from captured history (GFP-40).

    The same ``service.price_trend`` the GUI's chart draws, so a number quoted
    from this command and a number read off the chart cannot disagree.

    A missing day is a gap, never a zero: a week with no scrape simply
    contributes no points. A plain ``--metric price`` series needs a food to be
    about (``--food`` or ``--by food``) — the cheapest item in a whole weekly
    ad measures package size, not price.
    """
    try:
        chosen_metric = service.Metric(metric.lower())
    except ValueError:
        typer.echo(f"Error: unknown metric {metric!r}. Use 'protein' or 'price'.")
        raise typer.Exit(code=2)
    try:
        dimension = service.Dimension(by.lower())
    except ValueError:
        typer.echo(f"Error: unknown grouping {by!r}. Use 'store' or 'food'.")
        raise typer.Exit(code=2)

    conn = db.connect()
    try:
        trend = service.price_trend(
            metric=chosen_metric, dimension=dimension, days=days, store=store,
            food=food, postal_code=postal_code, meat_only=meat, conn=conn,
        )
    except service.UnscopedPriceTrendError:
        # The service states this in its own vocabulary (`food=`, `Dimension`);
        # a CLI user types flags, so the front end says it in flags.
        typer.echo(
            "Error: a price series needs a food to be about — add --food NAME "
            "or --by food. The cheapest item in a whole weekly ad measures "
            "package size, not price; --metric protein compares across foods."
        )
        raise typer.Exit(code=2)
    except service.UnknownFoodError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(code=2)

    unit = "$/g protein" if chosen_metric is service.Metric.PROTEIN else "$"

    def fmt(value: float) -> str:
        return f"{value:.4f}" if chosen_metric is service.Metric.PROTEIN else _money(value)

    if not trend.series:
        # Not an error: an empty window is a real, common answer early on.
        typer.echo(trend.reason)
        raise typer.Exit()

    # Says WHICH subset is being ranked. The chart names it in a tab (GFP-109);
    # a CLI that printed the same header for both would be the quieter lie.
    scope = "animal protein" if meat else "all protein"
    typer.echo(
        f"Cheapest {unit} per day by {dimension.value} ({scope}), "
        f"last {trend.days} days · {trend.observed_days} day(s) observed"
    )
    if not trend.is_plottable:
        typer.echo(trend.reason)

    if points:
        # For Metric.PRICE the value IS the price, so a second column would
        # just repeat it. A food series spans stores, so it must say which one
        # won the day -- that is the answer a "where do I buy this?" needs.
        show_price = chosen_metric is not service.Metric.PRICE
        show_store = dimension is service.Dimension.FOOD
        headers = (
            ["day", unit]
            + (["price"] if show_price else [])
            + (["store"] if show_store else [])
            + ["item"]
        )
        for series in trend.series:
            typer.echo(f"\n{series.label} ({series.key})")
            _print_table(headers, [
                (p.day, fmt(p.value))
                + ((_money(p.price),) if show_price else ())
                + ((p.store,) if show_store else ())
                + (p.item_name[:44],)
                for p in series.points
            ])
        return

    # `change` is last minus first over the points that EXIST, so a gap narrows
    # the span it describes rather than inventing values to fill it.
    rows = []
    for series in trend.series:
        first, last = series.points[0], series.points[-1]
        change = "-" if first is last else f"{(last.value - first.value) / first.value:+.1%}"
        rows.append((
            series.label,
            fmt(last.value),
            change,
            f"{len(series.points)}",
            f"{first.day}..{last.day}",
            last.item_name[:36],
        ))
    _print_table(["series", f"latest {unit}", "change", "days", "span", "cheapest item"], rows)


@app.command()
def cheapest(
    all_protein: bool = typer.Option(
        False, "--all-protein",
        help="Include non-meat protein (whey, tofu, plant). Meat only by default.",
    ),
    postal_code: str = typer.Option(None, "--postal-code", "-z", help="Limit to one ZIP."),
) -> None:
    """The cheapest protein on offer at each store right now (GFP-107).

    The same query behind the app's bottom strip, so the two cannot disagree.
    Reads current offers, not history: a historical low nobody can buy today is
    the wrong number to shop from — `gplan records` answers that question.

    Expired offers are excluded outright. Sending someone to a shop for an offer
    that ended is worse than sending them nowhere.
    """
    items = service.cheapest_protein_by_store(
        meat_only=not all_protein, postal_code=postal_code, conn=db.connect()
    )
    if not items:
        typer.echo(
            "Nothing to rank yet — no protein with a usable size in the current "
            "offers. Try `gplan scrape <store>`."
        )
        raise typer.Exit()

    scope = "all protein" if all_protein else "animal protein"
    typer.echo(f"Cheapest {scope} on offer, per store:")
    _print_table(
        ["store", "$/g protein", "kind", "price", "item"],
        [(
            item.label,
            f"{item.cost_per_gram_protein:.4f}",
            item.kind or "-",
            # GFP-98: a WEIGHT item's price buys one POUND, not the package.
            (f"{_money(item.price)}/{item.price_per_unit_uom}"
             if item.sold_by == "WEIGHT" and item.price_per_unit_uom
             else _money(item.price)),
            item.item_name[:44],
        ) for item in items],
    )


@client_app.command("groceries")
def client_groceries(
    who: str = typer.Argument(..., help="Client id or name."),
    days: int = typer.Option(
        service.DEFAULT_DAYS, "--days", "-d", help="How many days to shop for."
    ),
    fmt: str = typer.Option(
        "text", "--format", "-f", help="text | csv | html. html has clickable links."
    ),
    out: Path = typer.Option(
        None, "--out", "-o", help="Write to a file instead of printing."
    ),
) -> None:
    """Build a grocery list for a client (GFP-112).

    Turns the amortised daily bill into whole packages someone can actually
    buy: quantities round UP (you cannot buy 0.4 of a packet, and rounding down
    would silently miss the target), and a per-weight item is bought by weight
    rather than by package.

    `--format html` is the one with genuinely clickable product links, and it
    prints properly from any browser on macOS or Windows.
    """
    conn = db.connect()
    try:
        customer = client_service.resolve_client(who, conn=conn)
    except client_service.ClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    try:
        glist = service.grocery_list_for(customer, days=days, conn=conn)
    except ValueError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if glist is None:
        typer.secho(
            f"{customer.name} has no weight on file, so there is no protein "
            "target to shop for. Set one with `gplan client edit`.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    try:
        rendered = service.render_grocery_list(glist, fmt)
    except ValueError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if out is None:
        typer.echo(rendered)
        return
    written = service.write_grocery_list(glist, out, fmt)
    typer.secho(
        f"Wrote {len(glist.items)} item(s) for {glist.days} days to {written}",
        fg=typer.colors.GREEN,
    )


@app.command("logs")
def logs_cmd(
    tail: int = typer.Option(20, "--tail", "-n", help="Show the last N lines (0 = none)."),
    path_only: bool = typer.Option(False, "--path", help="Print the log file path only."),
) -> None:
    """Where the logs are, and the tail of the current one (GFP-86).

    Logs prune themselves -- nobody administers this machine -- so this is also
    the honest place to see how much history actually exists.
    """
    target = logs.log_path()
    if path_only:
        typer.echo(str(target))
        return

    typer.echo(f"Log file: {target}")
    typer.echo(f"Retention: {logs.retention_days()} days "
               f"(config `log_retention_days`), rotating at "
               f"{logs.MAX_BYTES // 1024 // 1024} MB x {logs.BACKUP_COUNT} files")
    if not target.exists():
        typer.echo("No log file yet - it is created the first time something logs.")
        return

    rotated = sorted(p for p in target.parent.glob(f"{target.name}*") if p != target)
    total = target.stat().st_size + sum(p.stat().st_size for p in rotated)
    typer.echo(f"{len(rotated) + 1} file(s), {total / 1024:.0f} KB total")

    if tail:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        typer.echo("")
        for line in lines[-tail:]:
            typer.echo(f"  {line}")


config_app = typer.Typer(
    help="Global settings (GFP-85).", no_args_is_help=False
)
app.add_typer(config_app, name="config")


@config_app.callback(invoke_without_command=True)
def config_cmd(
    ctx: typer.Context,
    write: bool = typer.Option(
        False, "--write", help="Create config.json with the defaults if absent."
    ),
) -> None:
    """Show global settings, where each value came from, and any problems (GFP-85).

    Origin matters as much as value: "why is it using that ZIP" is answered by
    knowing whether it came from the environment, the file, or a built-in
    default.

    ``invoke_without_command`` keeps ``gplan config`` printing the table it
    always has, now that ``gplan config set`` exists beneath it.
    """
    if ctx.invoked_subcommand is not None:
        return
    if write:
        written = app_config.write_defaults()
        typer.secho(f"Config file: {written}", fg=typer.colors.GREEN)

    resolved = app_config.load()
    typer.echo(f"Config file: {resolved.source}"
               + ("" if resolved.source.exists() else "  (not created yet — defaults apply)"))
    _print_table(
        ["setting", "value", "from", "what it does"],
        [(key, str(value), origin, describe)
         for key, value, origin, describe in app_config.describe()],
    )
    for problem in resolved.problems:
        # A problem never stops the app -- it degraded to a default and says so.
        typer.secho(f"  ! {problem}", fg=typer.colors.YELLOW)
    if resolved.problems:
        raise typer.Exit(code=1)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Setting name, e.g. 'postal_code'."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Change one setting and save it (GFP-91).

    The installer hands a brand-new user straight to this command, so its
    failure messages have to be usable by someone who has never seen the app
    before: an unknown key lists the real ones, and a bad value says what was
    expected rather than 'invalid'.
    """
    try:
        parsed = app_config.set_value(key, value)
    except KeyError:
        typer.secho(f"There is no setting called '{key}'.", fg=typer.colors.RED)
        typer.echo("\nSettings you can change:")
        for setting in app_config.SETTINGS:
            typer.echo(f"  {setting.key:<20} {setting.describe}")
        raise typer.Exit(code=1)
    except app_config.SettingError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"{key} = {parsed}", fg=typer.colors.GREEN)
    typer.echo(f"Saved to {app_config.path()}")

    # An environment override silently wins over the file, so a value that was
    # just "saved" may not be the value the app uses. Saying nothing here is
    # how someone spends an afternoon on a ZIP code that never took effect.
    override = os.environ.get(app_config.BY_KEY[key].env_var)
    if override is not None:
        typer.secho(
            f"  ! {app_config.BY_KEY[key].env_var} is set to {override!r} in your "
            "environment and takes precedence, so this change has no effect "
            "until you unset it.",
            fg=typer.colors.YELLOW,
        )


timer_app = typer.Typer(
    help="Refresh prices in the background, without the app open (GFP-102)."
)
app.add_typer(timer_app, name="timer")


@timer_app.command("install")
def timer_install(
    at: str = typer.Option(
        None, "--at", help="Local time to run, HH:MM. Default 06:00.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the command that would register it."
    ),
) -> None:
    """Register the daily background refresh with the OS.

    A Scheduled Task on Windows, a LaunchAgent on macOS. Both run as you, need
    no administrator rights, and invoke `gplan schedule run --once`.

    Idempotent: running it again replaces the timer rather than adding a
    second one.
    """
    from . import background

    try:
        outcome = background.install(at or background.DEFAULT_TIME, dry_run=dry_run)
    except background.TimerError as exc:
        typer.secho(f"Could not register the background refresh:\n{exc}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if dry_run:
        typer.echo(outcome)
        return
    typer.secho(
        f"Background refresh {outcome}.", fg=typer.colors.GREEN
    )
    typer.echo(f"  runs daily at {at or background.DEFAULT_TIME}")
    typer.echo(f"  known to the OS as: {background.identifier()}")
    if not app_config.get("background_refresh"):
        # Registering a timer whose runs will all decline to do anything is
        # exactly the sort of thing that wastes an afternoon.
        typer.secho(
            "  ! config `background_refresh` is false, so the timer will fire "
            "and then do nothing. Turn it on with:\n"
            "      gplan config set background_refresh true",
            fg=typer.colors.YELLOW,
        )


@timer_app.command("remove")
def timer_remove(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the command that would remove it."
    ),
) -> None:
    """Unregister the daily background refresh. Idempotent."""
    from . import background

    try:
        outcome = background.remove(dry_run=dry_run)
    except background.TimerError as exc:
        typer.secho(f"Could not remove the background refresh:\n{exc}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(outcome if dry_run else f"Background refresh {outcome}.")


@timer_app.command("status")
def timer_status() -> None:
    """Is the background refresh registered, and under what name.

    Prints the OS identifier whether or not it is registered: GFP-102's manual
    removal checklist is only usable if the thing to remove can be named, and
    the moment someone needs that name is the moment the timer is misbehaving.
    """
    from . import background

    state = background.status()
    typer.echo(f"Identifier: {state.identifier}")
    if not state.supported:
        typer.secho(state.detail, fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    typer.secho(
        "Registered" if state.registered else "Not registered",
        fg=typer.colors.GREEN if state.registered else typer.colors.YELLOW,
    )
    typer.echo(f"Setting:    background_refresh = {app_config.get('background_refresh')}")
    typer.echo(f"Runs:       {background.scheduled_command()}")
    if state.detail and not state.registered:
        typer.echo(f"  {state.detail.splitlines()[0] if state.detail else ''}")


@app.command("update")
def update_cmd(
    check_only: bool = typer.Option(
        True, "--check/--no-check", hidden=True,
        help="Reserved. This command only ever checks.",
    ),
) -> None:
    """Is a newer version available (GFP-96).

    Checks now, ignoring the once-a-day gate -- someone typing this is asking
    on purpose. It still honours `update_check` in the config: a user who
    turned this off means it, and doing it anyway because they typed the
    command would be the wrong lesson.

    NEVER downloads or installs anything. It prints the version and the page.
    """
    from . import updates

    if not app_config.get("update_check"):
        typer.secho(
            "Update checks are turned off (config `update_check`).",
            fg=typer.colors.YELLOW,
        )
        typer.echo(f"You are running grocery-planner {__version__}.")
        typer.echo(f"Releases: {updates.RELEASES_PAGE}")
        raise typer.Exit(0)

    typer.echo(f"You are running grocery-planner {__version__}.")
    found = updates.check(force=True)
    if found is None:
        # Deliberately one message for "up to date" and "could not reach
        # GitHub". Distinguishing them would mean reporting a network error to
        # someone who asked a question about versions, and the ticket is
        # explicit that a check must fail silently.
        typer.secho("No newer version found.", fg=typer.colors.GREEN)
        typer.echo(f"Releases: {updates.RELEASES_PAGE}")
        raise typer.Exit(0)

    typer.secho(found.message, fg=typer.colors.CYAN)
    typer.echo(f"Download it from: {found.url}")
    typer.echo("Nothing has been downloaded or installed.")


@app.command("uninstall-plan")
def uninstall_plan_cmd(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Everything an uninstall would remove, with paths resolved (GFP-92).

    Read by uninstall.ps1 / uninstall.sh so the two platforms cannot drift, and
    useful on its own: "what is this app actually holding, and where" is a
    question worth being able to answer without uninstalling anything.

    Resolves environment overrides rather than assuming defaults. Any of
    GROCERY_PLANNER_DB, _CONFIG, _LOG_DIR, _KROGER_CONFIG or
    _WHOLEFOODS_SESSION moves a file out of the data directory, at which point
    "delete the folder" would silently leave a credential behind.
    """
    from . import uninstall as uninstall_service

    items = uninstall_service.plan()
    if as_json:
        typer.echo(uninstall_service.to_json(items))
        return
    typer.echo(uninstall_service.to_lines(items))


@app.command()
def stores() -> None:
    """Show tracked stores, row counts, and scraper readiness (GFP-4)."""
    conn = db.connect()
    rows = []
    for r in conn.execute("SELECT key, display_name FROM stores ORDER BY display_name"):
        d = conn.execute("SELECT COUNT(*) FROM deals WHERE store=?", (r["key"],)).fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM prices WHERE store=?", (r["key"],)).fetchone()[0]
        rows.append((r["key"], r["display_name"], str(d), str(p), _scraper_status_label(r["key"])))
    _print_table(["key", "store", "deals", "prices", "scraper"], rows)


@app.command("credentials")
def credentials_cmd(
    set_licence: str = typer.Option(
        None, "--set-licence",
        help="Store this install's licence key for the credential broker.",
    ),
    refresh: bool = typer.Option(
        False, "--refresh",
        help="Drop cached brokered credentials so the next scrape refetches.",
    ),
) -> None:
    """Show which credentials are configured, and where (GFP-97).

    Deliberately prints presence and location only, never a value. This is the
    command to run when supporting someone else's install: it answers "is the
    Kroger key set up, and which file is it reading?" without anyone having to
    read a client_secret out loud.

    ``--refresh`` is what makes an upstream rotation take effect NOW rather
    than whenever the cache happens to expire (GFP-101). It is also the honest
    answer to "it says my key is fine but the scrape 401s".
    """
    if set_licence is not None:
        from . import broker

        target = broker.set_licence_key(set_licence)
        # Not echoing the key back. See the closing line of this command.
        typer.echo(f"Licence key stored at {target}")
        typer.echo("Cached broker credentials dropped, so the next scrape refetches.")

    if refresh:
        from . import broker

        broker.forget()
        typer.echo("Cached broker credentials dropped.")

    entries = credentials.status()
    _print_table(
        ["credential", "status", "source"],
        [(s.name,
          "configured" if s.configured else "MISSING",
          s.origin + (" (overridden)" if s.overridden else ""))
         for s in entries],
    )
    # Locations go on their own lines: _print_table truncates a cell at 48
    # characters, and a half-printed path is useless for the one question this
    # command exists to answer -- which file is it actually reading?
    typer.echo("")
    for s in entries:
        typer.echo(f"{s.name}: {s.location}")
        if not s.configured:
            typer.echo(f"    {s.obtain_hint}")
    if all(s.configured for s in entries):
        typer.echo("\nAll known credentials are configured.")
    typer.echo(
        "\nValues are never printed. A secret in a terminal is a secret in a "
        "scrollback buffer."
    )


def _scraper_status_label(store_key: str) -> str:
    """"-" (no scraper at all, CSV-only), "ready", or "needs setup: <why>".

    GFP-4: a store can be registered without being usable yet (Whole Foods,
    before its session cookie is hand-minted -- see scrapers/wholefoods.py).
    This is where a user finds that out ahead of `gplan scrape` failing, per
    the same ticket's "surface it where a user can act on it" requirement.
    """
    if store_key not in SCRAPERS:
        return "-"
    status = service.scraper_status(store_key)
    return "ready" if status.ready else f"needs setup: {status.reason}"


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
        # GFP-4: this store may be registered but not READY (e.g. Whole
        # Foods before its session cookie is minted) rather than genuinely
        # unregistered -- give the more specific message when that's why.
        if store in SCRAPERS:
            reason = service.scraper_status(store).reason
            typer.secho(
                f"{SCRAPERS[store].MERCHANT} is registered but not ready to "
                f"schedule: {reason}", fg=typer.colors.RED, err=True,
            )
        else:
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
    from . import background

    conn = db.connect()
    if not scheduler.list_schedules(conn, enabled_only=True):
        typer.secho("No schedules configured. Try: gplan schedule set foodlion --every 12h",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    # GFP-102: the catch-up pass goes through background.refresh_once, which is
    # also what the OS timer runs. One implementation, so the kill switch and
    # the single-refresh lock cannot be honoured here and forgotten there.
    code = background.refresh_once(on_event=lambda m: typer.echo(f"  {m}"))
    if once:
        raise typer.Exit(code)
    if code and not once:
        typer.secho("Catch-up had failures; continuing to the scheduler.",
                    fg=typer.colors.YELLOW)

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


@nutrition_app.command("classify")
def nutrition_classify(
    reclassify: bool = typer.Option(
        False, "--reclassify",
        help="Redo every food, not just unclassified ones. Use after editing the rules.",
    ),
    show: str = typer.Option(
        None, "--show", "-s", help="List the foods classified as this kind."
    ),
) -> None:
    """Work out which animal each protein food is (GFP-106).

    Fills ``foods.protein_kind`` — chicken, beef, pork, turkey, lamb, fish,
    shellfish — plus 'other' for anything that is not meat and 'unknown' where
    the kind genuinely cannot be told. An unknown is never a guess: a
    mislabelled cut is worse than an unlabelled one, because the label is what
    gets acted on.

    Cheap to re-run: only rows never classified are looked at.
    """
    from . import protein_kind as pk

    conn = db.connect()
    written = pk.classify_all(conn, reclassify=reclassify)
    if written:
        typer.secho(
            f"Classified {sum(written.values())} food(s).", fg=typer.colors.GREEN
        )
    else:
        typer.echo("Nothing new to classify.")

    stats = pk.coverage(conn)
    typer.echo(
        f"{stats['total']} foods: {stats['meat']} meat, {stats['other']} not meat, "
        f"{stats['unknown']} kind unknown, {stats['unclassified']} unclassified."
    )
    _print_table(
        ["kind", "foods"],
        [(kind, str(n)) for kind, n in sorted(
            stats["by_kind"].items(), key=lambda kv: (-kv[1], kv[0])
        )],
    )

    if show:
        foods = nutrition.list_foods(kind=show, conn=conn)
        if not foods:
            typer.echo(f"No foods classified as {show!r}.")
            raise typer.Exit()
        _print_table(
            ["food", "category", "protein/100g"],
            [(f.name[:52], f.category or "-",
              f"{f.protein_per_100g:.1f}" if f.protein_per_100g is not None else "-")
             for f in foods[:40]],
        )


# --------------------------------------------------------------------------- #
# Clients (GFP-33)
#
# Every command here goes through grocery_planner.service.clients -- the same
# functions the GUI roster calls -- so the two front ends cannot disagree about
# what "add a client" or "delete a client" means. No SQL and no
# CustomerRepository call lives in this file.
# --------------------------------------------------------------------------- #
class WeightUnit(str, Enum):
    """The unit a typed weight is in. Never defaulted -- see GFP-28/GFP-29."""

    kg = "kg"
    lb = "lb"


def _client_or_exit(identifier: str, include_deleted: bool = False):
    """Resolve a client by id or name, or exit 1 with the reason.

    Shared by every command that takes a client, so "no such client" and
    "that name matches two people" read the same everywhere.
    """
    try:
        return client_service.resolve_client(identifier, include_deleted=include_deleted)
    except client_service.ClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


# The weight and target cells come from the service layer rather than being
# formatted here, so "150 lb" and "no weight on file" read identically in
# `gplan client list` and in the GUI roster's delete confirmation.
_weight_cell = client_service.weight_label
_target_cell = client_service.target_label


@client_app.command("list")
def client_list(
    search: str = typer.Option("", "--search", "-q", help="Filter by name substring."),
    include_deleted: bool = typer.Option(
        False, "--all", "-a", help="Include removed clients (they are recoverable)."
    ),
) -> None:
    """List clients with their weight and daily protein target."""
    summaries = client_service.list_client_summaries(
        search=search, include_deleted=include_deleted
    )
    _print_table(
        ["id", "name", "weight", "target/day", "status"],
        [
            (
                str(s.client.id),
                s.client.name,
                _weight_cell(s.client),
                _target_cell(s.target),
                "removed" if s.client.is_deleted else "active",
            )
            for s in summaries
        ],
    )
    typer.echo(f"{len(summaries)} client(s).")


@client_app.command("show")
def client_show(
    client: str = typer.Argument(..., help="Client id or name."),
) -> None:
    """Show one client's full record."""
    record = _client_or_exit(client, include_deleted=True)
    target = client_service.client_target(record)
    rows = [
        ("id", str(record.id)),
        ("name", record.name),
        ("weight", _weight_cell(record)),
        ("weight (canonical)", f"{record.weight_kg:g} kg" if record.weight_kg else "-"),
        ("protein factor", f"{record.protein_factor:g}"),
        ("daily target", _target_cell(target)),
        ("weekly target",
         f"{target.weekly_grams:.0f} {target.weekly_unit}" if target else "no target"),
        ("height (cm)", f"{record.height_cm:g}" if record.height_cm else "-"),
        ("age", str(record.age) if record.age else "-"),
        ("sex", record.sex or "-"),
        ("activity", record.activity_level or "-"),
        ("goal", record.goal or "-"),
        ("notes", record.notes or "-"),
        ("added", record.created_at or "-"),
        ("updated", record.updated_at or "-"),
        ("removed", record.deleted_at or "-"),
    ]
    _print_table(["field", "value"], rows)
    if target is None:
        # GFP-29's rule, said out loud rather than left as a blank cell.
        typer.secho(
            "No weight on file, so there is no protein target to compute.",
            fg=typer.colors.YELLOW,
        )


@client_app.command("add")
def client_add(
    name: str = typer.Argument(..., help="The client's full name."),
    weight: float = typer.Option(None, "--weight", "-w", help="Body weight (needs --unit)."),
    desired_weight: float = typer.Option(
        None, "--desired-weight",
        help="Goal weight the protein target is computed from (uses --unit).",
    ),
    weekly_budget: float = typer.Option(
        None, "--weekly-budget",
        help="Dollars per week. Reported against, never used to pick the plan.",
    ),
    unit: WeightUnit = typer.Option(
        None, "--unit", "-u", help="Unit of --weight: kg or lb. Never assumed."
    ),
    factor: float = typer.Option(
        None, "--factor", "-f",
        help="Protein g per POUND of desired weight per day (0.8-1.0, GFP-132)."
    ),
    height_cm: float = typer.Option(None, "--height-cm", help="Height in centimetres."),
    age: int = typer.Option(None, "--age"),
    sex: str = typer.Option(None, "--sex"),
    activity: str = typer.Option(None, "--activity", help="Activity level."),
    goal: str = typer.Option(None, "--goal"),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Add a client: gplan client add "Jane Doe" --weight 150 --unit lb.

    A weight is optional, but a weight without a unit is refused rather than
    guessed at -- 150 lb read as 150 kg is a 2.2x protein-target error.
    """
    fields = {
        "height_cm": height_cm, "age": age, "sex": sex,
        "activity_level": activity, "goal": goal, "notes": notes,
    }
    if factor is not None:
        fields["protein_factor"] = factor
    if weekly_budget is not None:
        fields["weekly_budget"] = weekly_budget
    if desired_weight is not None:
        # GFP-132: the target is grams per pound of DESIRED weight. Converted
        # here through the same helper --weight uses, so a goal weight cannot
        # be stored in different units from the current one -- and refused
        # without a unit for the same reason a bare --weight is: 150 lb read
        # as 150 kg is a 2.2x error, and it is no less wrong for a goal.
        if unit is None:
            typer.secho(
                "--desired-weight needs --unit too. A goal weight without a "
                "unit is a guess, and 150 lb read as 150 kg is a 2.2x "
                "protein-target error.",
                fg=typer.colors.RED, err=True,
            )
            raise typer.Exit(2)
        from .customers import lb_to_kg
        fields["desired_weight_kg"] = (
            lb_to_kg(desired_weight) if unit.value == "lb" else desired_weight
        )
    try:
        saved = client_service.create_client(
            name,
            weight=weight,
            weight_unit=unit.value if unit else None,
            **{k: v for k, v in fields.items() if v is not None},
        )
    except client_service.ClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(f"Added {saved.name} (id {saved.id}).", fg=typer.colors.GREEN)
    typer.echo(f"  weight: {_weight_cell(saved)}   "
               f"target: {_target_cell(client_service.client_target(saved))}")


@client_app.command("edit")
def client_edit(
    client: str = typer.Argument(..., help="Client id or name."),
    name: str = typer.Option(None, "--name", help="New name."),
    weight: float = typer.Option(
        None, "--weight", "-w",
        help="New body weight, read in --unit (or the unit already on file).",
    ),
    unit: WeightUnit = typer.Option(
        None, "--unit", "-u",
        help="Unit for --weight. Alone, it only changes how the weight is shown.",
    ),
    clear_weight: bool = typer.Option(
        False, "--clear-weight", help="Remove the weight (and so the protein target)."
    ),
    factor: float = typer.Option(
        None, "--factor", "-f",
        help="Protein g per POUND of desired weight per day (0.8-1.0).",
    ),
    height_cm: float = typer.Option(None, "--height-cm"),
    age: int = typer.Option(None, "--age"),
    sex: str = typer.Option(None, "--sex"),
    activity: str = typer.Option(None, "--activity"),
    goal: str = typer.Option(None, "--goal"),
    notes: str = typer.Option(None, "--notes", help='Pass "" to clear.'),
) -> None:
    """Change one client's details. Only the options you pass are touched.

    Unmentioned fields keep their value -- fixing a typo in a name never
    silently blanks a weight. ``--unit`` on its own re-displays the same body
    weight in the other unit (90 kg becomes 198.4 lb); to say "that number
    was actually pounds", pass ``--weight`` and ``--unit`` together.
    """
    record = _client_or_exit(client)
    if clear_weight and weight is not None:
        typer.secho("Pass either --weight or --clear-weight, not both.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    fields = {
        "name": name, "height_cm": height_cm, "age": age, "sex": sex,
        "activity_level": activity, "goal": goal, "notes": notes,
    }
    if factor is not None:
        fields["protein_factor"] = factor
    changes = {k: v for k, v in fields.items() if v is not None}

    weight_arg = None if clear_weight else (
        client_service.UNSET if weight is None else weight
    )
    unit_arg = client_service.UNSET if unit is None else unit.value
    try:
        saved = client_service.update_client(
            record.id, weight=weight_arg, weight_unit=unit_arg, **changes
        )
    except client_service.ClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(f"Updated {saved.name} (id {saved.id}).", fg=typer.colors.GREEN)
    typer.echo(f"  weight: {_weight_cell(saved)}   "
               f"target: {_target_cell(client_service.client_target(saved))}")


@client_app.command("delete")
def client_delete(
    client: str = typer.Argument(..., help="Client id or name."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the prompt (for scripts). Still recoverable."
    ),
) -> None:
    """Remove a client, after confirming exactly who is being removed.

    Client records are hand-typed during an intake conversation and cannot be
    re-scraped like a price, so this asks first and names the person, and the
    removal itself is recoverable with ``gplan client restore``.
    """
    record = _client_or_exit(client)
    # The same sentence the GUI's confirmation shows -- service-owned, so the
    # two front ends cannot describe the same deletion differently.
    typer.echo(
        "About to remove: "
        + client_service.describe_client(record, client_service.client_target(record))
    )
    if not yes:
        # The default is No: a stray Enter must not delete an irreplaceable
        # record. Aborting exits 1 and nothing is written.
        typer.confirm("Remove this client?", default=False, abort=True)
    try:
        removed = client_service.delete_client(record.id, confirm=True)
    except client_service.ClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(f"Removed {removed.name} (id {removed.id}).", fg=typer.colors.YELLOW)
    typer.echo(f"  Recoverable: gplan client restore {removed.id}")


@client_app.command("restore")
def client_restore(
    client: str = typer.Argument(..., help="Client id or name of a removed client."),
) -> None:
    """Bring back a removed client (the other half of delete safety)."""
    record = _client_or_exit(client, include_deleted=True)
    restored = client_service.restore_client(record.id)
    typer.secho(f"Restored {restored.name} (id {restored.id}).", fg=typer.colors.GREEN)


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
