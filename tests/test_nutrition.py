"""Tests for grocery_planner.nutrition (GFP-23): reads over foods/food_nutrients."""
from __future__ import annotations

from grocery_planner import nutrition


def test_list_categories_returns_the_six_v1_categories(conn):
    assert nutrition.list_categories(conn=conn) == sorted(
        {"beef", "pork", "chicken", "fish", "tofu", "whey"}
    )


def test_list_foods_returns_food_items_with_protein(conn):
    foods = nutrition.list_foods(conn=conn)
    assert len(foods) >= 20
    assert all(isinstance(f, nutrition.FoodItem) for f in foods)
    assert all(f.protein_per_100g is not None for f in foods)
    # Sorted by name.
    assert [f.name for f in foods] == sorted(f.name for f in foods)


def test_list_foods_filters_by_category(conn):
    chicken = nutrition.list_foods(category="chicken", conn=conn)
    assert chicken
    assert all(f.category == "chicken" for f in chicken)

    unknown = nutrition.list_foods(category="nonexistent-category", conn=conn)
    assert unknown == []


def test_get_food_by_name(conn):
    food = nutrition.get_food("Chicken breast, skinless/boneless, raw", conn=conn)
    assert food is not None
    assert food.category == "chicken"
    assert food.source == "curated"
    assert food.protein_per_100g == 23.0

    assert nutrition.get_food("Not a real food", conn=conn) is None


def test_protein_per_100g_helper(conn):
    assert nutrition.protein_per_100g(
        "Tofu, firm", conn=conn
    ) == 8.0
    assert nutrition.protein_per_100g("Not a real food", conn=conn) is None


def test_food_with_no_nutrient_row_reports_none(conn):
    conn.execute(
        "INSERT INTO foods(name, category, source, source_ref) "
        "VALUES ('Mystery Meat', 'beef', 'curated', 'mystery-meat')"
    )
    conn.commit()

    food = nutrition.get_food("Mystery Meat", conn=conn)
    assert food is not None
    assert food.protein_per_100g is None


def test_list_foods_defaults_to_db_connect(monkeypatch, tmp_path):
    # No conn passed -> falls back to db.connect(), matching the
    # service/deals.py convention (module functions, not a connection-holding
    # class, since SQLite connections can't cross threads).
    monkeypatch.setenv("GROCERY_PLANNER_DB", str(tmp_path / "default.sqlite3"))

    foods = nutrition.list_foods()
    assert len(foods) >= 20
