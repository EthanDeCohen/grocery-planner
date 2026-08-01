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

from . import __version__, db, formulas, importers, service
from .scrapers import SCRAPERS
from .stores import BY_KEY

app = typer.Typer(add_completion=False, help="Local-first grocery price/deal planner.")
formula_app = typer.Typer(help="Manage user-defined formulas.")
profile_app = typer.Typer(help="Set/get profile values (used as formula variables).")
app.add_typer(formula_app, name="formula")
app.add_typer(profile_app, name="profile")


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


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else ""


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
