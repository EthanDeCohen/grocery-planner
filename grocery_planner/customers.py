"""Customer domain (GFP-28): the client record.

Every table before this one -- ``deals``, ``prices``, ``foods``, ... -- is
re-scrapable or re-importable; a bad scrape or import is annoying but never
fatal, because it can just run again. A customer row is the opposite: it is
hand-typed by a nutritionist during an intake conversation, and nothing else
in this database can reconstruct it if it's lost. That drives the two things
this module is careful about.

**Units.** The existing protein formula (``weight * 1.6`` --
``grocery_planner/formulas.py``) is grams per *kilogram*. A nutritionist
typing "90" meaning pounds, stored as-is and fed into that formula later
(GFP-29), silently produces 144 g/day of protein instead of ~65 g/day -- a
2.2x dosing error in a health tool. So a customer's weight is split across
two columns rather than one:

* ``weight_kg`` -- canonical, ALWAYS kilograms. This is the only column
  GFP-29's protein-target math may read. It is named ``weight_kg`` rather
  than the bare ``weight`` a first pass might reach for specifically so it
  cannot be misread as "whatever unit the user typed" -- an unambiguous name
  is part of what makes the 2.2x mistake hard to repeat.
* ``weight_unit`` -- ``'kg'`` or ``'lb'``, exactly as the user chose when
  entering the value. Never read for math, only so a UI can redisplay the
  weight in the unit the customer actually thinks in (:attr:`Customer.
  weight_display`) instead of silently normalizing everything to kg on
  screen.

:func:`to_kg`/:func:`from_kg` are the only place the lb<->kg conversion
happens; :meth:`Customer.create` routes every new customer through
:func:`to_kg` at construction time so a caller cannot forget to convert.

**Deletion.** Unlike every other table in this database, a customer record
cannot be re-derived from anything -- there's no source CSV or scrape to
re-run. :meth:`CustomerRepository.delete` is therefore a *soft* delete
(stamps ``deleted_at`` rather than removing the row) instead of the
alternative the ticket allowed, a required confirmation flag. Soft-delete
was chosen over a confirm flag because a flag only protects against a
literal accidental click -- it does nothing once the caller *did* mean to
delete and only later realizes that was wrong (the more likely real-world
case for irreplaceable data). A soft-deleted row keeps the data recoverable
on disk indefinitely; nothing in GFP-28 purges it. :meth:`CustomerRepository.
restore` undoes a delete.

Like ``grocery_planner/service/deals.py`` and ``grocery_planner/
nutrition.py``, every :class:`CustomerRepository` method takes an optional
``conn`` and defaults to ``db.connect()`` rather than the repository holding
a connection on itself: SQLite connections cannot cross threads, and the GUI
scrapes on a background thread, so a connection-holding repository instance
would break the first time it was touched from two threads.

Out of scope here (see the GFP-28 ticket): protein target calculation
(GFP-29), per-client protein preferences (GFP-30), ``postal_code`` on the
customer (GFP-57), any GUI/CLI wiring (GFP-33), photos (GFP-47).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from . import db

KG = "kg"
LB = "lb"
_VALID_UNITS = {KG, LB}

#: Grams of protein per POUND of DESIRED body weight, per day (GFP-132).
#:
#: From the nutritionist: "0.8 or 1 g of protein per pound of desired body
#: weight. So if you want to weigh 150 you will eat 120-150 g a day."
#:
#: WAS 1.6, meant as grams per KILOGRAM of CURRENT weight -- wrong unit and
#: wrong weight. 1.6 g/kg is 0.73 g/lb, below the bottom of her range, so the
#: app under-prescribed every client by 9-27%.
#:
#: The default is the CONSERVATIVE end of her range. A tool that computes what
#: somebody eats should not pick the top of a professional's band on their
#: behalf.
DEFAULT_PROTEIN_FACTOR = 0.8

#: The band the nutritionist prescribed. Hard limits, per GFP-133 -- the slider
#: spans exactly this and the text box refuses anything outside it.
MIN_PROTEIN_FACTOR = 0.8
MAX_PROTEIN_FACTOR = 1.0

# Exact international avoirdupois pound (the 1959 international
# yard-and-pound agreement definition). Deliberately its own constant,
# separate from grocery_planner/savings.py's oz-based lb/kg factor (1 kg =
# 35.274 oz there): that one is tuned for reading product-label sizes off
# flyer text, where the last decimal doesn't matter; this one feeds a health
# calculation (GFP-29's protein target), where it does.
KG_PER_LB = 0.45359237

# GFP-66: decimal places kept by Customer.weight_display (and by
# grocery_planner.service.clients.restate_weight, which rounds the same way
# for the same reason). Far finer than
# anyone types a body weight to (nobody enters "150.000001 lb"), so rounding
# to this can only remove float round-trip noise (e.g. 150 lb -> kg -> lb
# landing on 149.99999999999997), never a value a user actually entered.
WEIGHT_DISPLAY_DECIMALS = 6


def _normalize_unit(unit: str) -> str:
    normalized = (unit or "").strip().lower()
    if normalized not in _VALID_UNITS:
        raise ValueError(f"weight_unit must be 'kg' or 'lb', got {unit!r}")
    return normalized


def lb_to_kg(pounds: float) -> float:
    """Pounds -> kilograms, exact to the international pound definition."""
    return pounds * KG_PER_LB


def kg_to_lb(kilograms: float) -> float:
    """Kilograms -> pounds, the exact inverse of :func:`lb_to_kg`."""
    return kilograms / KG_PER_LB


def to_kg(value: float, unit: str) -> float:
    """Normalize a user-entered weight to the canonical ``weight_kg`` column.

    ``unit`` must be ``'kg'`` or ``'lb'`` (case-insensitive, whitespace
    tolerant) -- anything else raises :class:`ValueError` rather than
    silently defaulting to one or the other, since a wrong default here is
    exactly the 2.2x dosing bug this module exists to prevent.
    """
    return value if _normalize_unit(unit) == KG else lb_to_kg(value)


def from_kg(value_kg: float, unit: str) -> float:
    """Inverse of :func:`to_kg`: canonical kg -> a display unit.

    Used to echo a customer's weight back in the unit they originally
    entered it in (see :attr:`Customer.weight_display`) rather than
    silently showing kilograms to someone who typed pounds.
    """
    return value_kg if _normalize_unit(unit) == KG else kg_to_lb(value_kg)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _like(term: str) -> str:
    """Wrap a search term for LIKE, neutralizing wildcards a user may type.

    Mirrors grocery_planner/service/deals.py's ``_like``.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass
class Customer:
    """One ``customers`` row.

    A dataclass like :class:`grocery_planner.nutrition.FoodItem` /
    :class:`grocery_planner.savings.Size` / :class:`grocery_planner.
    stores.Store` / :class:`grocery_planner.scrapers.base.StoreConfig` --
    but not frozen, since :meth:`CustomerRepository.save` needs to hand back
    a copy with ``id``/``created_at``/``updated_at`` filled in after an
    insert.

    ``weight_kg`` is the canonical value; ``weight_unit`` is display-only
    (see the module docstring). Prefer :meth:`create` over calling this
    constructor directly with a raw user-entered weight -- it does the
    lb->kg conversion for you so there's no separate step to remember.
    """

    id: int | None
    name: str
    weight_kg: float | None = None
    weight_unit: str | None = None
    height_cm: float | None = None
    age: int | None = None
    sex: str | None = None
    activity_level: str | None = None
    goal: str | None = None
    #: GFP-132. Nullable: a client whose goal weight has not been discussed is
    #: a normal state, not a broken record. targets.py falls back to
    #: ``weight_kg`` and says which it used.
    desired_weight_kg: float | None = None
    protein_factor: float = DEFAULT_PROTEIN_FACTOR
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None

    @classmethod
    def create(
        cls,
        name: str,
        *,
        weight: float | None = None,
        weight_unit: str | None = None,
        **kwargs: Any,
    ) -> "Customer":
        """Build a brand-new (unsaved, ``id=None``) customer.

        Takes the weight in whatever unit the user entered it
        (``weight=90, weight_unit="lb"``) and converts to the canonical
        ``weight_kg`` right here, so a caller building a :class:`Customer`
        never has to remember the conversion step themselves. Pass
        ``weight_kg=``/``weight_unit=`` directly via ``kwargs`` instead if
        the value is already canonical (e.g. round-tripping a row read back
        out of the repository).
        """
        weight_kg = kwargs.pop("weight_kg", None)
        if weight is not None:
            if weight_kg is not None:
                raise ValueError("pass either weight= or weight_kg=, not both")
            if not weight_unit:
                raise ValueError("weight_unit is required when weight is given")
            weight_kg = to_kg(weight, weight_unit)
        if weight_unit is not None:
            weight_unit = _normalize_unit(weight_unit)
        return cls(id=None, name=name, weight_kg=weight_kg, weight_unit=weight_unit, **kwargs)

    @property
    def weight_display(self) -> float | None:
        """``weight_kg`` converted back to ``weight_unit`` for display.

        ``None`` if either half is missing -- a weight with no known unit is
        not safe to guess at.

        Rounded to :data:`WEIGHT_DISPLAY_DECIMALS` places (GFP-66): the
        lb<->kg round trip through :func:`to_kg`/:func:`from_kg` is exact
        math but not exact *float* arithmetic -- e.g. 150 lb -> kg -> lb
        comes back as ``149.99999999999997``, not ``150``. That epsilon is
        display noise, not information: nobody enters body weight to six
        decimal places, so rounding it away can't hide a real value, only
        the floating-point artifact. ``from_kg`` itself is left exact (it
        is a general kg<->unit converter, not display-specific) --
        only this property, whose entire job is to be shown to a person,
        rounds.
        """
        if self.weight_kg is None or self.weight_unit is None:
            return None
        return round(from_kg(self.weight_kg, self.weight_unit), WEIGHT_DISPLAY_DECIMALS)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


_COLUMNS = (
    "id, name, weight_kg, weight_unit, height_cm, age, sex, activity_level, "
    "goal, desired_weight_kg, protein_factor, notes, created_at, updated_at, deleted_at"
)


def _row_to_customer(row: sqlite3.Row) -> Customer:
    return Customer(
        id=row["id"],
        name=row["name"],
        weight_kg=row["weight_kg"],
        weight_unit=row["weight_unit"],
        height_cm=row["height_cm"],
        age=row["age"],
        sex=row["sex"],
        activity_level=row["activity_level"],
        goal=row["goal"],
        desired_weight_kg=row["desired_weight_kg"],
        protein_factor=row["protein_factor"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )


class CustomerRepository:
    """CRUD over ``customers``. See the module docstring for the connection
    and soft-delete conventions this follows.

    Every method is a ``staticmethod`` taking ``conn`` as its last
    parameter -- there is no instance state, so ``CustomerRepository`` is
    used as a namespace (``CustomerRepository.get(1, conn=conn)``), not
    instantiated.
    """

    @staticmethod
    def list(
        search: str = "",
        include_deleted: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> list[Customer]:
        """All customers ordered by name, optionally name-filtered.

        Soft-deleted customers are excluded unless ``include_deleted=True``
        -- callers browsing the client list should never see a deleted
        record by accident.
        """
        own = conn or db.connect()
        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if search:
            clauses.append("name LIKE ? ESCAPE '\\'")
            params.append(_like(search))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT {_COLUMNS} FROM customers{where} ORDER BY name"
        return [_row_to_customer(r) for r in own.execute(sql, params)]

    @staticmethod
    def get(
        customer_id: int,
        include_deleted: bool = True,
        conn: sqlite3.Connection | None = None,
    ) -> Customer | None:
        """A single customer by id, or ``None`` if there isn't one.

        Defaults to ``include_deleted=True`` (unlike :meth:`list`) so that
        looking a customer up by id -- e.g. to show/restore a just-deleted
        record -- doesn't require a separate code path.
        """
        own = conn or db.connect()
        sql = f"SELECT {_COLUMNS} FROM customers WHERE id=?"
        params: list[Any] = [customer_id]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = own.execute(sql, params).fetchone()
        return _row_to_customer(row) if row else None

    @staticmethod
    def save(customer: Customer, conn: sqlite3.Connection | None = None) -> Customer:
        """Insert (``customer.id is None``) or update an existing customer.

        Returns a new :class:`Customer` with ``id``/``created_at``/
        ``updated_at`` filled in; ``customer`` itself is left untouched.
        """
        own = conn or db.connect()
        if customer.weight_unit is not None:
            _normalize_unit(customer.weight_unit)  # fail before it ever reaches SQL
        now = _now()

        if customer.id is None:
            cur = own.execute(
                "INSERT INTO customers("
                "name, weight_kg, weight_unit, height_cm, age, sex, "
                "activity_level, goal, desired_weight_kg, protein_factor, notes, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    customer.name, customer.weight_kg, customer.weight_unit,
                    customer.height_cm, customer.age, customer.sex,
                    customer.activity_level, customer.goal, customer.desired_weight_kg,
                    customer.protein_factor,
                    customer.notes, now, now,
                ),
            )
            own.commit()
            return replace(customer, id=int(cur.lastrowid), created_at=now, updated_at=now)

        own.execute(
            "UPDATE customers SET name=?, weight_kg=?, weight_unit=?, height_cm=?, "
            "age=?, sex=?, activity_level=?, goal=?, desired_weight_kg=?, protein_factor=?, notes=?, "
            "updated_at=? WHERE id=?",
            (
                customer.name, customer.weight_kg, customer.weight_unit,
                customer.height_cm, customer.age, customer.sex,
                customer.activity_level, customer.goal, customer.desired_weight_kg,
                customer.protein_factor,
                customer.notes, now, customer.id,
            ),
        )
        own.commit()
        return replace(customer, updated_at=now)

    @staticmethod
    def delete(customer_id: int, conn: sqlite3.Connection | None = None) -> bool:
        """Soft-delete: stamps ``deleted_at`` rather than removing the row.

        See the module docstring for why. Returns ``True`` if an active
        customer with this id existed and was marked deleted; ``False`` if
        no such id exists, or it was already deleted (idempotent -- calling
        delete twice does not clobber the original ``deleted_at``).
        """
        own = conn or db.connect()
        cur = own.execute(
            "UPDATE customers SET deleted_at=?, updated_at=? "
            "WHERE id=? AND deleted_at IS NULL",
            (_now(), _now(), customer_id),
        )
        own.commit()
        return cur.rowcount > 0

    @staticmethod
    def restore(customer_id: int, conn: sqlite3.Connection | None = None) -> bool:
        """Undo :meth:`delete`: clears ``deleted_at``.

        The other half of the soft-delete safety net -- makes an accidental
        delete recoverable without touching SQLite directly.
        """
        own = conn or db.connect()
        cur = own.execute(
            "UPDATE customers SET deleted_at=NULL, updated_at=? "
            "WHERE id=? AND deleted_at IS NOT NULL",
            (_now(), customer_id),
        )
        own.commit()
        return cur.rowcount > 0
