"""GFP-40: `gplan trends` — the CLI half of "one definition, two front ends".

The point of these tests is not that the command prints something. It is that
the command and the GUI chart cannot diverge: both go through
``service.price_trend``, so a number quoted from the terminal and a number read
off the chart are the same number. What is tested here is the CLI's own
contract — flag parsing, exit codes, and refusing to print a meaningless series.
"""
from __future__ import annotations

from datetime import date, timedelta

from typer.testing import CliRunner

from grocery_planner import db
from grocery_planner.cli import app

runner = CliRunner()

PROTEIN_PER_100G = 25.0


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _seed(env_db) -> None:
    """One food, one item, two days of falling prices at one store."""
    conn = db.connect(env_db)
    cur = conn.execute(
        "INSERT INTO foods(name, slug, category, source) "
        "VALUES ('Cli chicken', 'gfp40-cli-chicken', 'test', 'usda')"
    )
    food_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, PROTEIN_PER_100G)
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES ('foodlion', 'Chicken Breast 16 oz', ?, 0.9, 'test')", (food_id,)
    )
    for offset, price in ((1, 6.00), (0, 5.00)):
        conn.execute(
            "INSERT INTO price_history"
            "(store, postal_code, item_name, deal_type, dollar_price, source, captured_at) "
            "VALUES ('foodlion', '27401', 'Chicken Breast 16 oz', 'Weekly Ad', ?, 'test', ?)",
            (price, _days_ago(offset)),
        )
    conn.commit()
    conn.close()


def test_trends_reports_a_series_and_its_direction(env_db):
    _seed(env_db)
    result = runner.invoke(app, ["trends"])
    assert result.exit_code == 0, result.stdout
    assert "Food Lion" in result.stdout
    # 6.00 -> 5.00 is a fall, and the sign is the whole point of the column.
    assert "-16.7%" in result.stdout


def test_trends_points_lists_every_day(env_db):
    _seed(env_db)
    result = runner.invoke(app, ["trends", "--points"])
    assert result.exit_code == 0, result.stdout
    assert _days_ago(1) in result.stdout
    assert _days_ago(0) in result.stdout


def test_trends_on_an_empty_database_explains_rather_than_failing(env_db):
    """No history is a normal early state, not an error."""
    result = runner.invoke(app, ["trends"])
    assert result.exit_code == 0
    assert "No protein prices on record yet" in result.stdout


def test_an_unscoped_price_series_exits_two_and_names_the_flags(env_db):
    _seed(env_db)
    result = runner.invoke(app, ["trends", "--metric", "price"])
    assert result.exit_code == 2
    # The CLI must speak in flags, not in the service's Python vocabulary.
    assert "--food" in result.stdout and "--by food" in result.stdout
    assert "Dimension" not in result.stdout


def test_a_price_series_scoped_to_a_food_is_allowed(env_db):
    _seed(env_db)
    result = runner.invoke(
        app, ["trends", "--metric", "price", "--food", "gfp40-cli-chicken"]
    )
    assert result.exit_code == 0, result.stdout
    assert "$5.00" in result.stdout


def test_an_unknown_food_exits_two(env_db):
    _seed(env_db)
    result = runner.invoke(app, ["trends", "--food", "not-a-food"])
    assert result.exit_code == 2
    assert "no food in the catalog" in result.stdout


def test_an_unknown_metric_or_grouping_exits_two(env_db):
    _seed(env_db)
    assert runner.invoke(app, ["trends", "--metric", "protien"]).exit_code == 2
    assert runner.invoke(app, ["trends", "--by", "shop"]).exit_code == 2


def test_by_food_groups_by_food_and_names_the_winning_store(env_db):
    _seed(env_db)
    result = runner.invoke(app, ["trends", "--by", "food", "--points"])
    assert result.exit_code == 0, result.stdout
    assert "Cli chicken" in result.stdout
    # A food series spans stores, so it must say which one the price came from.
    assert "foodlion" in result.stdout
