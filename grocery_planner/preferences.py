"""Per-client protein category preferences (GFP-30).

A nutritionist checks off which protein categories a client should be
eating -- the v1 checkbox list is exactly ``foods.category``'s six starter
values (beef, pork, chicken, fish, tofu, whey), but this module never
hard-codes them. :func:`grocery_planner.nutrition.list_categories` reads
them from the data (``SELECT DISTINCT category FROM foods``), so a seventh
category becomes selectable the moment a food row uses it -- a row of data,
never a UI or code change. :func:`set_preferences` validates against that
same live list, which is what keeps a stored preference from silently
outliving the category it names.

**A preference is not an exclusion.** "I'd rather eat chicken" and "I have a
shellfish allergy" are different kinds of fact -- one is a soft ranking
input, the other is a hard safety constraint. This table stores only the
former: an unchecked category means "no stated preference for it", not "keep
this away from the client". Concretely, that shows up as the next paragraph:
zero rows is a normal, common state, and it must never be read as "avoid
every category" or a health-relevant exclusion. If a future ticket wants
allergies/hard exclusions, that is a genuinely different concept (and
probably a different table with different failure semantics) -- do not
repurpose an empty preference set to mean it.

**Zero preferences means unconstrained, not "an empty basket".** A client
with no rows here has stated no protein preference at all, so ranking must
consider every category, exactly as if this table did not exist for them.
This is the deliberate behaviour GFP-49 depends on: it compares an
unconstrained baseline against a preference-constrained plan, and the
unconstrained side *is* the zero-preference case. :func:`list_preferences`
therefore just returns ``[]`` for that client -- callers (the future
recommendation engine, GFP-31/48/49) are expected to treat an empty list as
"don't filter", not as "match nothing"; that decision belongs to the engine
and is explicitly out of scope here.

Like ``grocery_planner/service/deals.py``, ``nutrition.py`` and
``customers.py``, every function here takes an optional ``conn`` that
defaults to ``db.connect()`` rather than a connection-holding class: SQLite
connections cannot cross threads, and the GUI scrapes on a background
thread.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from . import db, nutrition


def list_preferences(
    customer_id: int, conn: sqlite3.Connection | None = None
) -> list[str]:
    """This customer's preferred protein categories, alphabetical.

    ``[]`` means no preference was ever recorded -- see the module
    docstring: that is "unconstrained", not "prefers nothing".
    """
    own = conn or db.connect()
    return [
        r[0]
        for r in own.execute(
            "SELECT category FROM customer_protein_preferences "
            "WHERE customer_id=? ORDER BY category",
            (customer_id,),
        )
    ]


def set_preferences(
    customer_id: int,
    categories: Iterable[str],
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Replace this customer's full preference set with ``categories``.

    Pass an empty iterable to clear every preference -- that is a valid,
    meaningful state (see the module docstring), not an error.

    Each category must currently appear in the foods catalog
    (:func:`nutrition.list_categories`); anything else raises
    :class:`ValueError` naming the bad value(s), so a typo (or a category
    that has since been removed from the catalog) is caught here rather than
    stored silently. Duplicates in ``categories`` are collapsed.

    Replace-all rather than incremental add/remove because the client page
    renders these as a fixed checkbox list and saves the whole set on
    submit; there is no ordering or incremental-edit requirement to support
    add/remove separately (add one back later if a caller needs it).
    """
    own = conn or db.connect()
    wanted = sorted({c for c in categories})

    valid = set(nutrition.list_categories(conn=own))
    unknown = sorted(c for c in wanted if c not in valid)
    if unknown:
        raise ValueError(
            f"unknown protein category/categories: {unknown!r}; "
            f"valid categories are {sorted(valid)}"
        )

    own.execute(
        "DELETE FROM customer_protein_preferences WHERE customer_id=?",
        (customer_id,),
    )
    own.executemany(
        "INSERT INTO customer_protein_preferences(customer_id, category) "
        "VALUES (?, ?)",
        [(customer_id, category) for category in wanted],
    )
    own.commit()
    return wanted
