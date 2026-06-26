"""Typer CLI entry point for grocery-planner (`gp`).

Local-first commands over the SQLite store:
    gp import [PATH]        import data/<store>/*.csv into the DB
    gp scrape STORE         fetch fresh deals (Food Lion implemented)
    gp list deals|prices    query stored rows
    gp stores               show tracked stores + row counts
    gp db-path              print the database location
    gp formula ...          manage user-defined formulas
    gp profile ...          set/get profile values used by formulas
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import typer

from . import __version__, db, formulas, importers
from .scrapers import foodlion
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
    store: str = typer.Argument(..., help="Store key, e.g. 'foodlion'."),
    postal_code: str = typer.Option(foodlion.DEFAULT_POSTAL_CODE, "--postal-code", "-z"),
) -> None:
    """Scrape fresh deals for a store and store them in the DB."""
    if store != foodlion.STORE_KEY:
        typer.secho(
            f"Only '{foodlion.STORE_KEY}' is implemented so far (got {store!r}). "
            "Harris Teeter / Whole Foods are GFP-3 / GFP-4.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(2)

    typer.echo(f"Scraping Food Lion weekly ad for {postal_code} ...")
    rows, flyer = foodlion.scrape(postal_code=postal_code)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = db.connect()
    cols = importers.DEAL_COLUMNS
    conn.execute("DELETE FROM deals WHERE store=? AND source=?", (store, "scrape"))
    conn.executemany(
        f"INSERT INTO deals(store, {', '.join(cols)}, source, imported_at) "
        f"VALUES (:store, {', '.join(':' + c for c in cols)}, :source, :imported_at)",
        [{**r, "store": store, "source": "scrape", "imported_at": now} for r in rows],
    )
    conn.commit()
    no_price = sum(1 for r in rows if r["sale_price"] is None)
    typer.secho(
        f"Flyer {flyer.get('name')} ({flyer.get('id')}): stored {len(rows)} deals "
        f"({no_price} without a listed price).",
        fg=typer.colors.GREEN,
    )


@app.command("list")
def list_cmd(
    kind: Kind = typer.Argument(Kind.deals, help="deals or prices."),
    store: str = typer.Option(None, "--store", "-s", help="Filter by store key."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (0 = all)."),
    on_sale: bool = typer.Option(False, "--on-sale", help="Deals: only rows with a sale price."),
) -> None:
    """List stored deals or prices."""
    conn = db.connect()
    where, params = [], []
    if store:
        if store not in BY_KEY:
            typer.secho(f"Unknown store {store!r}. Known: {', '.join(BY_KEY)}",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        where.append("store=?")
        params.append(store)
    if kind is Kind.deals and on_sale:
        where.append("sale_price IS NOT NULL")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    lim = "" if limit == 0 else f" LIMIT {int(limit)}"

    if kind is Kind.deals:
        sql = ("SELECT store, item_name, sub_category, sale_price, valid_to "
               f"FROM deals{clause} ORDER BY store, item_name{lim}")
        rows = conn.execute(sql, params).fetchall()
        _print_table(["store", "item", "sub_category", "sale", "valid_to"],
                     [(BY_KEY[r["store"]].display_name, r["item_name"], r["sub_category"],
                       _money(r["sale_price"]), r["valid_to"] or "") for r in rows])
    else:
        sql = ("SELECT store, item_name, category, regular_price, sale_price, unit "
               f"FROM prices{clause} ORDER BY store, item_name{lim}")
        rows = conn.execute(sql, params).fetchall()
        _print_table(["store", "item", "category", "regular", "sale", "unit"],
                     [(BY_KEY[r["store"]].display_name, r["item_name"], r["category"],
                       _money(r["regular_price"]), _money(r["sale_price"]), r["unit"] or "")
                      for r in rows])

    total = conn.execute(f"SELECT COUNT(*) FROM {kind.value}{clause}", params).fetchone()[0]
    typer.secho(f"\n{len(rows)} shown of {total} {kind.value}.", fg=typer.colors.BLUE)


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
    """Define or update a formula: gp formula set target_protein "weight * 1.6"."""
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
    """Set a profile value: gp profile set weight 82."""
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


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else ""


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
