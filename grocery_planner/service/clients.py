"""Client CRUD, front-end-agnostic (GFP-33).

GFP-28 gave the app a customer *record* (``grocery_planner/customers.py``)
and GFP-36/GFP-51 gave it two half-wired front ends: the roster could add a
client, the biometrics panel could edit one, and the CLI could do neither.
Nothing could remove a client at all. This module is the single place every
client operation goes through, so ``gplan client ...`` and the GUI roster
cannot drift apart the way the formula delete did (GUI-only inline SQL, no
CLI equivalent) -- the mistake the ticket names.

The layering is the one the rest of this package uses: :class:`grocery_planner.
customers.CustomerRepository` still owns the SQL, and this module owns the
*operation* -- validation, unit conversion, delete confirmation, and the
"what does the roster actually need to draw a row" join with
:mod:`grocery_planner.targets`. Front ends call this and never
``CustomerRepository`` or ``db.connect()`` for client data.

**Units (GFP-29's 2.2x bug).** Every write path that takes a user-entered
weight takes ``weight`` + ``weight_unit`` as a pair and converts through
:func:`grocery_planner.customers.to_kg`; ``weight_kg`` is never accepted
from a front end, so no caller can hand this layer a bare number in an
unstated unit. :func:`update_client` additionally refuses to *invent* a unit:
editing the weight of a client who has no unit on file without saying which
unit you mean is an error, not an assumption.

**Deletion.** ``CustomerRepository.delete`` is already a soft delete (the row
survives, ``deleted_at`` is stamped, :func:`restore_client` undoes it). This
layer adds the second half of the protection the ticket asks for: a delete
must be *deliberate*. :func:`delete_client` refuses to act without
``confirm=True`` and, when it refuses, the error names the client so the
front end can put that name in front of the user before anything happens. A
delete that removes an unnamed "record 4" is exactly the misclick this rule
exists to prevent.

**Partial updates.** :func:`update_client` uses an :data:`UNSET` sentinel
rather than ``None`` defaults, because ``None`` is a meaningful value here:
``notes=None`` means "clear the notes", while omitting ``notes`` means
"leave them alone". Conflating the two would make every edit of one field a
silent wipe of the others -- on hand-typed, irreplaceable data.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Final

from .. import db, targets
from ..customers import Customer, CustomerRepository, to_kg
from ..targets import ProteinTarget


class _Unset:
    """Type of :data:`UNSET`; a class of its own so it reprs readably."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"

    def __bool__(self) -> bool:
        return False


#: "This field was not mentioned" -- distinct from ``None`` ("clear it").
UNSET: Final = _Unset()

#: Fields :func:`update_client` will set straight through, no conversion.
#: Weight is deliberately absent: it is the one field with a unit, and so the
#: one field that must never be assigned without going through ``to_kg``.
PLAIN_FIELDS: Final = (
    "name", "height_cm", "age", "sex", "activity_level", "goal",
    "protein_factor", "notes",
)


class ClientError(Exception):
    """Base for every client-operation failure a front end should report."""


class ClientNotFoundError(ClientError):
    """No client matches the given id or name."""


class AmbiguousClientError(ClientError):
    """A name matched more than one client.

    Carries the matches so a CLI can list them instead of picking one --
    guessing which of two clients was meant is how the wrong record gets
    edited or deleted.
    """

    def __init__(self, term: str, matches: list[Customer]) -> None:
        names = ", ".join(f"{c.name} (id {c.id})" for c in matches)
        super().__init__(f"{term!r} matches {len(matches)} clients: {names}")
        self.term = term
        self.matches = matches


class ClientNameRequiredError(ClientError):
    """A client was created or renamed with a blank name.

    The roster identifies clients by name and nothing else, so a nameless
    record is one nobody can find again.
    """


class WeightUnitRequiredError(ClientError):
    """A weight was given (or edited) with no unit to interpret it in.

    The GFP-28/GFP-29 rule, enforced at the boundary: a number with no unit
    is not a weight, and defaulting it to either kg or lb is the 2.2x dosing
    error itself.
    """


class DeleteNotConfirmedError(ClientError):
    """:func:`delete_client` was called without ``confirm=True``.

    ``client`` is the record that would have been removed, so the caller can
    show *who* it is about to delete rather than an id.
    """

    def __init__(self, client: Customer) -> None:
        super().__init__(
            f"Deleting {client.name!r} (id {client.id}) must be confirmed."
        )
        self.client = client


@dataclass(frozen=True)
class ClientSummary:
    """One client plus the number the product is about, for list views.

    The roster row and ``gplan client list`` render the same two facts, so
    they are computed once here rather than each front end joining
    :mod:`grocery_planner.targets` itself (and one of them eventually
    forgetting that a missing weight means *no* target).

    ``target`` is ``None`` when the client has no weight on file. That is the
    honest answer, not a zero -- "absent stays absent, never a guess".
    """

    client: Customer
    target: ProteinTarget | None

    @property
    def has_target(self) -> bool:
        return self.target is not None


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
def list_clients(
    search: str = "",
    include_deleted: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[Customer]:
    """Clients ordered by name, optionally filtered by name substring."""
    own = conn or db.connect()
    return CustomerRepository.list(search=search, include_deleted=include_deleted, conn=own)


def list_client_summaries(
    search: str = "",
    include_deleted: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[ClientSummary]:
    """:func:`list_clients` with each client's protein target attached.

    One connection for the whole list rather than one per row: ``db.connect``
    checks pending migrations on every call, so a per-row connection would
    make a roster of 40 clients 40 schema checks.
    """
    own = conn or db.connect()
    return [
        ClientSummary(client=client, target=targets.protein_target_for(client, conn=own))
        for client in list_clients(search=search, include_deleted=include_deleted, conn=own)
    ]


def get_client(
    customer_id: int,
    include_deleted: bool = True,
    conn: sqlite3.Connection | None = None,
) -> Customer | None:
    """One client by id, or ``None``. Mirrors ``CustomerRepository.get``."""
    own = conn or db.connect()
    return CustomerRepository.get(customer_id, include_deleted=include_deleted, conn=own)


def require_client(
    customer_id: int,
    include_deleted: bool = True,
    conn: sqlite3.Connection | None = None,
) -> Customer:
    """:func:`get_client`, raising :class:`ClientNotFoundError` instead of ``None``."""
    client = get_client(customer_id, include_deleted=include_deleted, conn=conn)
    if client is None:
        raise ClientNotFoundError(f"No client with id {customer_id}.")
    return client


def resolve_client(
    identifier: str | int,
    include_deleted: bool = False,
    conn: sqlite3.Connection | None = None,
) -> Customer:
    """Find one client by id or by name, for front ends that take typed input.

    An all-digits ``identifier`` is an id; anything else is matched against
    names -- case-insensitively exact first, then as a substring. A substring
    that hits more than one client raises :class:`AmbiguousClientError`
    rather than picking the first: the caller may be about to delete it.
    """
    own = conn or db.connect()
    term = str(identifier).strip()
    if not term:
        raise ClientNotFoundError("No client given.")
    if term.isdigit():
        return require_client(int(term), include_deleted=include_deleted, conn=own)

    matches = list_clients(search=term, include_deleted=include_deleted, conn=own)
    exact = [c for c in matches if c.name.strip().lower() == term.lower()]
    if len(exact) == 1:
        return exact[0]
    candidates = exact or matches
    if not candidates:
        raise ClientNotFoundError(f"No client matching {term!r}.")
    if len(candidates) > 1:
        raise AmbiguousClientError(term, candidates)
    return candidates[0]


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #
def _clean_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ClientNameRequiredError("A client needs a name.")
    return cleaned


def create_client(
    name: str,
    *,
    weight: float | None = None,
    weight_unit: str | None = None,
    conn: sqlite3.Connection | None = None,
    **fields: Any,
) -> Customer:
    """Create and save one client, returning the saved record (with its id).

    ``weight``/``weight_unit`` are the user's numbers in the user's unit;
    :meth:`Customer.create` converts to canonical kilograms. A weight with no
    unit raises :class:`WeightUnitRequiredError` -- and a weight of ``None``
    stores no weight *and* no unit, so a weightless client gets no protein
    target rather than one derived from a placeholder.
    """
    own = conn or db.connect()
    cleaned = _clean_name(name)
    if weight is not None and not weight_unit:
        raise WeightUnitRequiredError(
            f"A weight for {cleaned!r} needs a unit: 'kg' or 'lb'."
        )
    try:
        draft = Customer.create(
            cleaned,
            weight=weight,
            weight_unit=weight_unit if weight is not None else None,
            **fields,
        )
    except (TypeError, ValueError) as exc:   # bad unit, unknown field, ...
        raise ClientError(str(exc)) from exc
    return CustomerRepository.save(draft, conn=own)


def update_client(
    customer_id: int,
    *,
    weight: float | None | _Unset = UNSET,
    weight_unit: str | _Unset = UNSET,
    conn: sqlite3.Connection | None = None,
    **fields: Any,
) -> Customer:
    """Edit an existing client. Only the fields you pass change.

    Every keyword defaults to :data:`UNSET` ("not mentioned"), so correcting
    a typo in a name cannot silently blank a weight or a note.

    The weight rules, which are the whole reason this is not a generic
    setter:

    * ``weight=`` **and** ``weight_unit=`` -- the value is read in that unit
      and converted to canonical kilograms.
    * ``weight=`` alone -- read in the unit already on file. If the client has
      no unit on file there is nothing to read it in, so this raises
      :class:`WeightUnitRequiredError` instead of assuming kilograms.
    * ``weight_unit=`` alone -- a *display* change only. The stored mass is
      untouched; the client is simply shown in the other unit from now on.
      Switching a 90 kg client to 'lb' makes them 198.4 lb, not 90 lb.
      Reinterpreting a number requires passing both, deliberately.
    * ``weight=None`` -- clears the weight, and the unit with it (a unit with
      no weight describes nothing). The client then has no protein target,
      which is the correct answer for a client with no weight.
    """
    own = conn or db.connect()
    current = require_client(customer_id, conn=own)

    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, _Unset):
            continue
        if key not in PLAIN_FIELDS:
            raise ClientError(f"Unknown client field {key!r}.")
        updates[key] = _clean_name(value) if key == "name" else value

    if not isinstance(weight, _Unset):
        if weight is None:
            updates["weight_kg"] = None
            updates["weight_unit"] = None
        else:
            unit = current.weight_unit if isinstance(weight_unit, _Unset) else weight_unit
            if not unit:
                raise WeightUnitRequiredError(
                    f"{current.name} has no weight unit on file, so "
                    f"{weight} is ambiguous: pass 'kg' or 'lb'."
                )
            try:
                updates["weight_kg"] = to_kg(weight, unit)
            except ValueError as exc:
                raise ClientError(str(exc)) from exc
            updates["weight_unit"] = unit
    elif not isinstance(weight_unit, _Unset) and weight_unit:
        # Display unit only -- weight_kg deliberately untouched. Validate the
        # unit here so a bad string cannot reach the row.
        try:
            to_kg(0.0, weight_unit)
        except ValueError as exc:
            raise ClientError(str(exc)) from exc
        updates["weight_unit"] = weight_unit.strip().lower()

    if not updates:
        return current
    return CustomerRepository.save(replace(current, **updates), conn=own)


def delete_client(
    customer_id: int,
    *,
    confirm: bool = False,
    conn: sqlite3.Connection | None = None,
) -> Customer:
    """Remove one client -- deliberately, and recoverably.

    Two protections, both required by the ticket and neither sufficient
    alone:

    * ``confirm=True`` is mandatory. Without it this raises
      :class:`DeleteNotConfirmedError`, which carries the :class:`Customer`
      so the front end can name the person ("Remove Jane Doe?") rather than
      an id. A confirmation that does not say what it is removing is not a
      confirmation.
    * The removal underneath is ``CustomerRepository.delete``'s *soft*
      delete, so the record is still on disk and :func:`restore_client` brings
      it back. Client records are hand-typed and cannot be re-scraped; a
      confirmed-but-regretted delete has to be survivable too.

    Returns the client as it was *before* deletion (the caller usually wants
    to say what just went away). Deleting an already-deleted client is a
    no-op, not an error -- it is already in the state asked for.
    """
    own = conn or db.connect()
    client = require_client(customer_id, conn=own)
    if not confirm:
        raise DeleteNotConfirmedError(client)
    CustomerRepository.delete(customer_id, conn=own)
    return client


def restore_client(
    customer_id: int, conn: sqlite3.Connection | None = None
) -> Customer:
    """Undo :func:`delete_client`. Returns the restored client.

    Restoring a client who is not deleted is a no-op rather than an error,
    for the same reason deleting a deleted one is: the end state asked for is
    the end state reached.
    """
    own = conn or db.connect()
    require_client(customer_id, conn=own)
    CustomerRepository.restore(customer_id, conn=own)
    return require_client(customer_id, conn=own)
