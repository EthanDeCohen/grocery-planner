"""The 'what these choices cost' grid (GFP-153).

Two questions the rest of the client page answers separately and neither
answers well: what does VARIETY cost, and what do the PREFERENCES cost.

What the grid carries: the variety penalty is not one number, it depends on
how narrow the preference list is. On the first real client that was 127% with
one category ticked against 20% unconstrained.

WHICH ROW IS DEARER IS NOT A LAW, and a test below inverts it deliberately.
The panel used to assert the direction in its own copy; that claim was wrong
and was removed.

**Every figure comes from bill.week_plan**, the same object the bill and the
chart use. GFP-144 is the standing example of what happens when a panel
computes its own version of a number that appears elsewhere.
"""
from __future__ import annotations

import pytest

from grocery_planner import bill, preferences
from grocery_planner.customers import Customer, CustomerRepository, lb_to_kg


def _client(conn, **kwargs):
    fields = {
        "name": "Grid Client",
        "weight_kg": lb_to_kg(150),
        "desired_weight_kg": lb_to_kg(150),
    }
    fields.update(kwargs)
    return CustomerRepository.save(Customer(id=None, **fields), conn=conn)


def _deal(conn, name, price, category, kind, protein_per_100g=40.0):
    food_id = conn.execute(
        "INSERT INTO foods(name, slug, category, protein_kind, source) "
        "VALUES (?,?,?,?, 'test')",
        (name, name.lower().replace(" ", "-"), category, kind),
    ).lastrowid
    conn.execute(
        "INSERT INTO food_nutrients(food_id, nutrient, amount_per_100g) "
        "VALUES (?, 'protein', ?)", (food_id, protein_per_100g),
    )
    item = f"{name} 16 oz"
    conn.execute(
        "INSERT INTO deals(store, item_name, dollar_price, valid_to, source) "
        "VALUES ('harristeeter',?,?, '2099-01-01', 'scrape')", (item, price),
    )
    conn.execute(
        "INSERT INTO deal_food_match(store, item_name, food_id, confidence, method) "
        "VALUES ('harristeeter',?,?,1.0,'test')", (item, food_id),
    )
    conn.commit()


@pytest.fixture
def conn(env_db):
    """A connection to the SAME database the pane will reach for.

    The shared `conn` fixture opens a temp file WITHOUT redirecting the default
    connection, so a widget calling db.connect() internally would read the
    developer's real database -- which is exactly what happened, and the tests
    passed against Fiona's live numbers instead of the seeded market. env_db
    sets the override; this then connects through it.
    """
    from grocery_planner import db as db_module

    connection = db_module.connect()
    yield connection
    connection.close()


@pytest.fixture
def market(conn):
    """Cheap chicken, dear beef, several of each -- so a narrow preference is
    visibly dearer AND its variety penalty is visibly steeper."""
    for i, price in enumerate((1.00, 1.20, 1.40), start=1):
        _deal(conn, f"Cheap Chicken {i}", price, "chicken", "chicken")
    for i, price in enumerate((5.00, 9.00, 13.00), start=1):
        _deal(conn, f"Dear Beef {i}", price, "beef", "beef")
    return conn


def _qt():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pane(customer_id):
    from grocery_planner.gui.choicescost import ChoicesCostPane

    _qt()
    pane = ChoicesCostPane()
    pane.set_client(customer_id)
    return pane


def _texts(pane) -> list[str]:
    return [label.text() for label in pane._cells]


# --------------------------------------------------------------------------- #
# The grid agrees with the engine
# --------------------------------------------------------------------------- #
def test_every_figure_comes_from_week_plan(market, conn):
    """Not a similar calculation -- the same one. A panel that recomputes a
    number shown elsewhere is how the chart came to contradict the bill."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef"], conn=conn)

    shared = bill.rank_current_deals(conn)
    expected = {
        f"${bill.week_plan(client.id, categories=cats, conn=conn, ranked=shared, selection=bill.Selection(vary_week=vary)).total_cost:,.2f}"
        for cats in (None, [])
        for vary in (False, True)
    }
    shown = set(_texts(_pane(client.id)))
    assert expected <= shown, f"grid does not show week_plan's figures: {expected - shown}"


def test_a_narrow_preference_costs_more_than_no_preference(market, conn):
    """The left column's two rows, which is half of what the grid is for."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef"], conn=conn)

    shared = bill.rank_current_deals(conn)
    theirs = bill.week_plan(client.id, conn=conn, ranked=shared,
                            selection=bill.Selection(vary_week=False))
    unrestricted = bill.week_plan(client.id, categories=[], conn=conn, ranked=shared,
                                  selection=bill.Selection(vary_week=False))
    assert theirs.total_cost > unrestricted.total_cost


def test_the_two_rows_carry_DIFFERENT_variety_penalties(market, conn):
    """WHAT THE GRID ACTUALLY ESTABLISHES, and the correction that produced
    this test.

    On live data the restricted row was much dearer (127% vs 20%), and the
    first version of this asserted that as a rule. It is not one. THIS FIXTURE
    INVERTS IT: three near-identical cheap chickens make varying almost free
    unconstrained, while beef-only must climb 5 -> 9 -> 13. So the
    unconstrained penalty is the higher of the two here.

    The panel's copy was asserting the direction as fact and had to be
    rewritten. What is genuinely true, and all the grid claims, is that the
    two rows differ -- which is why both are shown.
    """
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    shared = bill.rank_current_deals(conn)

    def penalty(cats):
        flat = bill.week_plan(client.id, categories=cats, conn=conn, ranked=shared,
                              selection=bill.Selection(vary_week=False))
        varied = bill.week_plan(client.id, categories=cats, conn=conn, ranked=shared,
                                selection=bill.Selection(vary_week=True))
        return (varied.total_cost - flat.total_cost) / flat.total_cost

    narrow, unrestricted = penalty(["beef"]), penalty([])
    assert narrow != pytest.approx(unrestricted), (
        "if both rows always showed the same penalty the second row would be "
        "decoration"
    )


# --------------------------------------------------------------------------- #
# What it renders
# --------------------------------------------------------------------------- #
def test_the_grid_has_both_rows_and_all_columns(market, conn):
    from grocery_planner.gui.choicescost import HEADERS, THEIRS, UNRESTRICTED

    client = _client(conn)
    preferences.set_preferences(client.id, ["beef"], conn=conn)
    shown = _texts(_pane(client.id))

    for header in HEADERS[1:]:
        assert header in shown
    assert THEIRS in shown and UNRESTRICTED in shown


def test_a_client_with_no_target_says_so_rather_than_showing_zeroes(market, conn):
    """A grid of $0.00 reads as free. savings.py rule 1, in a table cell."""
    weightless = _client(conn, weight_kg=None, desired_weight_kg=None)
    pane = _pane(weightless.id)
    assert "no weight on file" in pane.note.text()
    assert _texts(pane) == []


def test_no_preferences_says_the_rows_are_the_same(market, conn):
    """Both rows price identical food, so the panel explains the duplication
    rather than leaving it looking like a bug."""
    client = _client(conn)
    pane = _pane(client.id)
    assert "No preferences set" in pane.note.text()


def test_clearing_the_client_empties_the_grid(market, conn):
    client = _client(conn)
    pane = _pane(client.id)
    assert _texts(pane)
    pane.clear()
    assert _texts(pane) == []


def test_a_reload_does_not_stack_a_second_grid(market, conn):
    """This pane re-renders on every checkbox toggle. takeAt alone leaves the
    old rows painted underneath -- the bug GFP-52 hit in where-to-buy."""
    client = _client(conn)
    pane = _pane(client.id)
    first = len(_texts(pane))
    pane.reload()
    pane.reload()
    assert len(_texts(pane)) == first


# --------------------------------------------------------------------------- #
# The percentage cell
# --------------------------------------------------------------------------- #
def test_a_zero_penalty_reads_as_words_not_plus_zero(market, conn):
    """"+0%" invites somebody to wonder whether the control is broken."""
    from grocery_planner.gui.choicescost import _penalty

    class _P:
        def __init__(self, cost): self.total_cost = cost

    assert _penalty(_P(10.0), _P(10.0)) == "no extra"
    assert _penalty(_P(10.0), _P(20.0)) == "+100%"


def test_an_unbuildable_plan_shows_a_dash_not_a_zero(market, conn):
    from grocery_planner.gui.choicescost import _money, _penalty

    assert _money(None) == "—"
    assert _penalty(None, None) == "—"


# --------------------------------------------------------------------------- #
# Sharing the ranking is a performance fix, not a behaviour change
# --------------------------------------------------------------------------- #
def test_a_shared_ranking_gives_the_same_answer_as_its_own(market, conn):
    """The grid hoists the ranking out to run four plans at one rank instead
    of four. That must not change any figure."""
    client = _client(conn)
    preferences.set_preferences(client.id, ["beef"], conn=conn)

    own = bill.week_plan(client.id, conn=conn, selection=bill.Selection(vary_week=True))
    shared = bill.week_plan(
        client.id, conn=conn, selection=bill.Selection(vary_week=True),
        ranked=bill.rank_current_deals(conn),
    )
    assert own.total_cost == pytest.approx(shared.total_cost)
    assert [l.item_name for d in own.days for l in d.lines] == \
           [l.item_name for d in shared.days for l in d.lines]
