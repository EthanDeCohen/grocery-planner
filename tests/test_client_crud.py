"""Client CRUD through the service layer, with both front ends on it (GFP-33).

Three things are guarded here, in the order the ticket cares about them:

1. **kg/lb never becomes a guess.** GFP-29's 2.2x dosing error (90 lb read as
   90 kg -> 144 g/day instead of 65) can only be prevented at the moment a
   weight is written, so every write path -- create, edit, unit switch, the CLI
   flags, the GUI dialog -- is round-tripped through pounds here.
2. **A delete is deliberate and says what it removes.** The service refuses an
   unconfirmed delete; the CLI prompts and names the client; the GUI needs a
   selection, a confirmation dialog labelled with the name, and defaults to
   Cancel. All of it is recoverable.
3. **The CLI and the GUI call the same functions.** Not "produce the same
   result" -- literally the same service functions, asserted by replacing them
   and watching both front ends hit the replacement. That is the ticket's
   acceptance criterion, and the only version of it that keeps holding when
   somebody adds a fourth field next year.

GUI cases use the shared ``window`` fixture (conftest) and are skipped wherever
the optional ``gui`` extra is absent, matching test_gui.py.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from grocery_planner import customers
from grocery_planner.cli import app
from grocery_planner.customers import kg_to_lb, KG_PER_LB, Customer, CustomerRepository
from grocery_planner.service import clients as client_service

runner = CliRunner()


def _add(name, weight=None, unit=None, **kwargs):
    """A client straight through the service layer -- the path under test."""
    return client_service.create_client(name, weight=weight, weight_unit=unit, **kwargs)


# --------------------------------------------------------------------------- #
# 1. Weight units: the 2.2x rule (GFP-28/GFP-29)
# --------------------------------------------------------------------------- #
def test_pounds_are_stored_as_kilograms_and_read_back_as_pounds(env_db):
    """The round trip, on the number from the ticket: 90 lb, not 90 kg."""
    saved = _add("Ana Ruiz", 90.0, "lb")

    # Stored canonically...
    assert saved.weight_kg == pytest.approx(90 * KG_PER_LB)   # 40.82 kg
    assert saved.weight_kg == pytest.approx(40.823313)
    assert saved.weight_unit == "lb"
    # ...and echoed back in the unit the nutritionist typed, exactly.
    assert saved.weight_display == pytest.approx(90.0)

    # And the number the product is about is the 72 a 90 lb client gets, not
    # the 159 a pounds-as-kilograms read would produce (GFP-132: 0.8 g per
    # POUND of desired weight; 90 lb x 0.8 = 72).
    target = client_service.client_target(saved)
    assert target.daily_grams == pytest.approx(90 * 0.8)
    assert 70 < target.daily_grams < 75

    reread = client_service.get_client(saved.id)
    assert reread.weight_kg == pytest.approx(saved.weight_kg)
    assert reread.weight_display == pytest.approx(90.0)


def test_the_same_number_in_kg_and_lb_are_different_clients(env_db):
    """The bug this rule exists for, stated as a difference of 2.2x."""
    in_kg = _add("Kilo Person", 90.0, "kg")
    in_lb = _add("Pound Person", 90.0, "lb")

    assert in_kg.weight_kg == pytest.approx(90.0)
    assert in_lb.weight_kg == pytest.approx(40.823313)
    assert in_kg.weight_kg / in_lb.weight_kg == pytest.approx(1 / KG_PER_LB)

    kg_target = client_service.client_target(in_kg).daily_grams
    lb_target = client_service.client_target(in_lb).daily_grams
    assert kg_target == pytest.approx(kg_to_lb(90) * 0.8)
    assert lb_target == pytest.approx(72.0, abs=0.1)


def test_editing_a_weight_converts_exactly_as_create_does(env_db):
    """The edit path is the one that could drift; it must not."""
    client = _add("Ben Okafor", 195.0, "lb")
    edited = client_service.update_client(client.id, weight=180.0, weight_unit="lb")

    reference = Customer.create("reference", weight=180.0, weight_unit="lb")
    assert edited.weight_kg == pytest.approx(reference.weight_kg)
    assert edited.weight_kg == pytest.approx(customers.to_kg(180.0, "lb"))
    assert edited.weight_display == pytest.approx(180.0)


def test_editing_a_weight_uses_the_unit_already_on_file(env_db):
    """--weight alone on a pounds client means pounds, not kilograms."""
    client = _add("Ben Okafor", 195.0, "lb")
    edited = client_service.update_client(client.id, weight=180.0)
    assert edited.weight_unit == "lb"
    assert edited.weight_kg == pytest.approx(customers.to_kg(180.0, "lb"))


def test_editing_a_weight_with_no_unit_anywhere_is_refused(env_db):
    """Absent stays absent: an unlabelled number is not a weight."""
    client = _add("Dev Patel")
    with pytest.raises(client_service.WeightUnitRequiredError):
        client_service.update_client(client.id, weight=90.0)
    assert client_service.get_client(client.id).weight_kg is None


def test_creating_with_a_weight_and_no_unit_is_refused(env_db):
    with pytest.raises(client_service.WeightUnitRequiredError):
        client_service.create_client("Nameless Unit", weight=90.0)
    assert client_service.list_clients() == []


def test_changing_only_the_unit_restates_the_weight_it_does_not_reinterpret_it(env_db):
    """90 kg shown in lb is 198.4 lb. The client did not change."""
    client = _add("Ana Ruiz", 90.0, "kg")
    switched = client_service.update_client(client.id, weight_unit="lb")

    assert switched.weight_kg == pytest.approx(90.0)      # untouched
    assert switched.weight_unit == "lb"
    assert switched.weight_display == pytest.approx(198.4, abs=0.1)
    # The protein target is a property of the mass, so it did not move either.
    assert client_service.client_target(switched).daily_grams == pytest.approx(kg_to_lb(90) * 0.8)


def test_restate_weight_round_trips_without_float_dust(env_db):
    """The GUI's unit selector leans on this; 150 must come back as 150."""
    as_lb = client_service.restate_weight(150.0, "kg", "lb")
    assert as_lb == pytest.approx(330.693, abs=0.001)
    assert client_service.restate_weight(as_lb, "lb", "kg") == 150.0
    assert client_service.restate_weight(150.0, "lb", "lb") == 150.0


def test_a_bad_unit_is_an_error_not_a_default(env_db):
    client = _add("Ana Ruiz", 90.0, "kg")
    with pytest.raises(client_service.ClientError):
        client_service.update_client(client.id, weight=90.0, weight_unit="stone")
    with pytest.raises(client_service.ClientError):
        client_service.create_client("Stone Person", weight=90.0, weight_unit="stone")


def test_clearing_a_weight_clears_the_unit_and_the_target(env_db):
    client = _add("Ana Ruiz", 90.0, "kg")
    cleared = client_service.update_client(client.id, weight=None)
    assert cleared.weight_kg is None
    assert cleared.weight_unit is None
    assert client_service.client_target(cleared) is None


def test_a_client_with_no_weight_gets_no_invented_target(env_db):
    _add("Dev Patel")
    summaries = client_service.list_client_summaries()
    assert len(summaries) == 1
    assert summaries[0].target is None
    assert summaries[0].has_target is False


# --------------------------------------------------------------------------- #
# 2. Partial updates: an edit of one field is not a wipe of the others
# --------------------------------------------------------------------------- #
def test_renaming_leaves_every_other_field_alone(env_db):
    client = _add("Ana Ruiz", 90.0, "lb", notes="prefers fish", age=41)
    renamed = client_service.update_client(client.id, name="Ana Ruiz-Mendez")

    assert renamed.name == "Ana Ruiz-Mendez"
    assert renamed.weight_kg == pytest.approx(client.weight_kg)
    assert renamed.weight_unit == "lb"
    assert renamed.notes == "prefers fish"
    assert renamed.age == 41


def test_a_blank_name_is_refused_on_create_and_on_rename(env_db):
    with pytest.raises(client_service.ClientNameRequiredError):
        client_service.create_client("   ")
    client = _add("Ana Ruiz")
    with pytest.raises(client_service.ClientNameRequiredError):
        client_service.update_client(client.id, name="  ")
    assert client_service.get_client(client.id).name == "Ana Ruiz"


def test_an_unknown_field_is_refused_rather_than_silently_dropped(env_db):
    client = _add("Ana Ruiz")
    with pytest.raises(client_service.ClientError):
        client_service.update_client(client.id, favourite_colour="green")


# --------------------------------------------------------------------------- #
# 3. Delete: deliberate, described, recoverable
# --------------------------------------------------------------------------- #
def test_delete_without_confirmation_does_nothing_and_names_the_client(env_db):
    client = _add("Ana Ruiz", 90.0, "lb")
    with pytest.raises(client_service.DeleteNotConfirmedError) as caught:
        client_service.delete_client(client.id)

    assert "Ana Ruiz" in str(caught.value)
    assert caught.value.client.id == client.id
    assert client_service.get_client(client.id).is_deleted is False


def test_a_confirmed_delete_hides_the_client_but_keeps_the_record(env_db):
    client = _add("Ana Ruiz", 90.0, "lb")
    removed = client_service.delete_client(client.id, confirm=True)

    assert removed.name == "Ana Ruiz"
    assert client_service.list_clients() == []
    still_there = client_service.get_client(client.id)
    assert still_there.is_deleted is True
    assert still_there.weight_kg == pytest.approx(client.weight_kg)   # nothing lost


def test_a_deleted_client_can_be_restored_with_its_weight_intact(env_db):
    client = _add("Ana Ruiz", 195.0, "lb")
    client_service.delete_client(client.id, confirm=True)
    restored = client_service.restore_client(client.id)

    assert restored.is_deleted is False
    assert restored.weight_display == pytest.approx(195.0)
    assert restored.weight_unit == "lb"
    assert [c.name for c in client_service.list_clients()] == ["Ana Ruiz"]


def test_describe_client_says_who_and_what_is_being_removed(env_db):
    client = _add("Ana Ruiz", 90.0, "lb")
    described = client_service.describe_client(
        client, client_service.client_target(client)
    )
    assert "Ana Ruiz" in described
    assert f"id {client.id}" in described
    assert "90 lb" in described        # their unit, not kilograms
    assert "g/day" in described


def test_describe_client_is_honest_about_a_client_with_no_weight(env_db):
    client = _add("Dev Patel")
    described = client_service.describe_client(client, client_service.client_target(client))
    assert "no weight on file" in described
    assert "no target" in described


def test_an_ambiguous_name_is_never_resolved_by_picking_one(env_db):
    """The dangerous case: 'delete ana' with two Anas on file."""
    _add("Ana Ruiz")
    _add("Ana Silva")
    with pytest.raises(client_service.AmbiguousClientError) as caught:
        client_service.resolve_client("ana")
    assert len(caught.value.matches) == 2
    assert "Ana Ruiz" in str(caught.value) and "Ana Silva" in str(caught.value)


def test_an_exact_name_wins_over_a_substring(env_db):
    ana = _add("Ana")
    _add("Ana Silva")
    assert client_service.resolve_client("ana").id == ana.id


# --------------------------------------------------------------------------- #
# 4. The CLI
# --------------------------------------------------------------------------- #
def test_cli_add_list_show_edit_delete_restore(env_db):
    added = runner.invoke(app, ["client", "add", "Ana Ruiz", "-w", "90", "-u", "lb"])
    assert added.exit_code == 0, added.stdout
    assert "Added Ana Ruiz" in added.stdout
    assert "90 lb" in added.stdout

    listed = runner.invoke(app, ["client", "list"])
    assert listed.exit_code == 0
    assert "Ana Ruiz" in listed.stdout
    assert "90 lb" in listed.stdout

    shown = runner.invoke(app, ["client", "show", "Ana Ruiz"])
    assert shown.exit_code == 0
    assert "90 lb" in shown.stdout              # as entered
    assert "40.8233 kg" in shown.stdout         # as stored

    edited = runner.invoke(app, ["client", "edit", "Ana Ruiz", "--weight", "180"])
    assert edited.exit_code == 0, edited.stdout
    assert "180 lb" in edited.stdout     # the unit on file, not a fresh guess

    removed = runner.invoke(app, ["client", "delete", "Ana Ruiz", "--yes"])
    assert removed.exit_code == 0, removed.stdout
    assert "Removed Ana Ruiz" in removed.stdout
    assert runner.invoke(app, ["client", "list"]).stdout.count("Ana Ruiz") == 0

    client_id = client_service.list_clients(include_deleted=True)[0].id
    restored = runner.invoke(app, ["client", "restore", str(client_id)])
    assert restored.exit_code == 0
    assert "Restored Ana Ruiz" in restored.stdout


def test_cli_add_refuses_a_weight_with_no_unit(env_db):
    result = runner.invoke(app, ["client", "add", "Ana Ruiz", "--weight", "90"])
    assert result.exit_code == 1
    assert client_service.list_clients() == []


def test_cli_delete_names_the_client_before_asking(env_db):
    _add("Ana Ruiz", 90.0, "lb")
    result = runner.invoke(app, ["client", "delete", "Ana Ruiz"], input="n\n")

    assert "Ana Ruiz" in result.stdout
    assert "90 lb" in result.stdout          # what it is about to remove
    assert result.exit_code != 0             # aborted
    assert client_service.list_clients()[0].is_deleted is False


def test_cli_delete_defaults_to_no_on_a_bare_enter(env_db):
    """A stray Return must not remove an irreplaceable record."""
    _add("Ana Ruiz", 90.0, "lb")
    result = runner.invoke(app, ["client", "delete", "Ana Ruiz"], input="\n")
    assert result.exit_code != 0
    assert client_service.list_clients()[0].is_deleted is False


def test_cli_delete_refuses_an_ambiguous_name(env_db):
    _add("Ana Ruiz")
    _add("Ana Silva")
    result = runner.invoke(app, ["client", "delete", "ana", "--yes"])
    assert result.exit_code == 1
    assert all(not c.is_deleted for c in client_service.list_clients())


def test_cli_never_touches_the_repository_directly():
    """The drift guard, at the source level: no SQL, no repository in the CLI."""
    from pathlib import Path

    import grocery_planner.cli as cli_module

    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    # Just the client commands: the rest of the CLI is other tickets' code.
    client_section = source.split("# Clients (GFP-33)")[1].split("def _is_iso_date")[0]
    # ...minus the banner comment that says the rule out loud.
    client_section = client_section.replace(
        "# CustomerRepository call lives in this file.", ""
    )
    assert "CustomerRepository" not in client_section
    assert "db.connect" not in client_section
    assert "SELECT" not in client_section.upper() or "sqlite" not in client_section


# --------------------------------------------------------------------------- #
# 5. The GUI -- same functions, same rules
# --------------------------------------------------------------------------- #
def test_the_roster_module_holds_no_client_sql_of_its_own():
    """No repository, no connection, no SQL in the pane -- the ticket's rule."""
    from pathlib import Path

    import grocery_planner

    # Located by PATH rather than by importing the module: importing it pulls in
    # PySide6, which CI does not install (`.[dev]`), and this assertion is about
    # the SOURCE TEXT -- it does not need Qt to be loadable. Importing would have
    # turned a check that can always run into one that always skips.
    roster_source = (
        Path(grocery_planner.__file__).parent / "gui" / "roster.py"
    ).read_text(encoding="utf-8")
    body = roster_source.split('"""', 2)[2]     # past the module docstring
    assert "CustomerRepository" not in body
    assert "db.connect" not in body


def test_the_gui_add_dialog_calls_the_same_create_client_the_cli_does(window, monkeypatch):
    """The acceptance criterion, asserted literally: one function, two callers."""
    calls = []
    real = client_service.create_client

    def spy(name, **kwargs):
        calls.append((name, kwargs))
        return real(name, **kwargs)

    monkeypatch.setattr(client_service, "create_client", spy)

    from grocery_planner.gui.roster import ClientDialog

    dialog = ClientDialog(window.roster)
    dialog.name_edit.setText("Ana Ruiz")
    dialog.weight_spin.setValue(90.0)
    dialog.unit_box.setCurrentIndex(dialog.unit_box.findData("lb"))
    dialog.on_save()

    runner.invoke(app, ["client", "add", "Ben Okafor", "-w", "195", "-u", "lb"])

    assert [c[0] for c in calls] == ["Ana Ruiz", "Ben Okafor"]
    assert calls[0][1]["weight"] == 90.0 and calls[0][1]["weight_unit"] == "lb"
    assert calls[1][1]["weight"] == 195.0 and calls[1][1]["weight_unit"] == "lb"


def test_the_gui_delete_calls_the_same_delete_client_the_cli_does(window, monkeypatch):
    from grocery_planner.gui import roster as roster_module

    ana = _add("Ana Ruiz", 90.0, "lb")
    ben = _add("Ben Okafor", 195.0, "lb")
    calls = []
    real = client_service.delete_client

    def spy(customer_id, **kwargs):
        calls.append((customer_id, kwargs))
        return real(customer_id, **kwargs)

    monkeypatch.setattr(client_service, "delete_client", spy)
    monkeypatch.setattr(
        roster_module.ConfirmRemoveDialog, "exec", lambda self: roster_module.QDialog.Accepted
    )

    window.roster.reload()
    window.roster.select_client(ana.id)
    window.roster.on_remove_client()

    runner.invoke(app, ["client", "delete", str(ben.id), "--yes"])

    assert [c[0] for c in calls] == [ana.id, ben.id]
    # Both front ends must have said so explicitly; the service refuses otherwise.
    assert all(c[1]["confirm"] is True for c in calls)


def test_the_gui_add_dialog_stores_pounds_as_kilograms(window):
    from grocery_planner.gui.roster import ClientDialog

    dialog = ClientDialog(window.roster)
    dialog.name_edit.setText("Ana Ruiz")
    dialog.weight_spin.setValue(90.0)
    dialog.unit_box.setCurrentIndex(dialog.unit_box.findData("lb"))
    dialog.on_save()

    saved = dialog.saved
    assert saved.weight_kg == pytest.approx(90 * KG_PER_LB)
    assert saved.weight_unit == "lb"
    assert saved.weight_display == pytest.approx(90.0)
    assert client_service.client_target(saved).daily_grams == pytest.approx(72.0, abs=0.1)


def test_the_gui_edit_dialog_round_trips_a_pounds_client_unchanged(window):
    """Open an edit, change nothing but the name, and the mass must not move."""
    from grocery_planner.gui.roster import ClientDialog

    client = _add("Ben Okafor", 195.0, "lb")
    dialog = ClientDialog(window.roster, client=client)
    assert dialog.weight_spin.value() == pytest.approx(195.0)   # shown in lb
    assert dialog.unit_box.currentData() == "lb"

    dialog.name_edit.setText("Ben Okafor-Hall")
    dialog.on_save()

    assert dialog.saved.name == "Ben Okafor-Hall"
    assert dialog.saved.weight_kg == pytest.approx(client.weight_kg)
    assert dialog.saved.weight_display == pytest.approx(195.0)


def test_switching_the_unit_on_a_weight_already_on_file_restates_it(window):
    """kg -> lb moves the number, not the person -- and saving proves it."""
    from grocery_planner.gui.roster import ClientDialog

    client = _add("Ana Ruiz", 90.0, "kg")
    dialog = ClientDialog(window.roster, client=client)
    dialog.unit_box.setCurrentIndex(dialog.unit_box.findData("lb"))

    assert dialog.weight_spin.value() == pytest.approx(198.4, abs=0.1)
    dialog.on_save()
    # Exactly 90, not 89.993: an untouched weight is not re-saved through a
    # one-decimal spinbox.
    assert dialog.saved.weight_kg == 90.0
    assert dialog.saved.weight_unit == "lb"
    assert dialog.saved.weight_display == pytest.approx(198.4, abs=0.1)


def test_switching_the_unit_on_a_freshly_typed_weight_does_not_convert_it(window):
    """A typed 195 with 'lb' selected is 195 lb, never 429.9 lb."""
    from grocery_planner.gui.roster import ClientDialog

    dialog = ClientDialog(window.roster)
    dialog.name_edit.setText("Ben Okafor")
    dialog.weight_spin.setValue(195.0)             # unit box still says kg
    dialog.unit_box.setCurrentIndex(dialog.unit_box.findData("lb"))

    assert dialog.weight_spin.value() == pytest.approx(195.0)
    dialog.on_save()
    assert dialog.saved.weight_display == pytest.approx(195.0)
    assert dialog.saved.weight_kg == pytest.approx(195 * KG_PER_LB)


def test_reopening_an_edit_and_saving_changes_nothing_about_the_weight(window):
    """The fidelity guard: hand-typed data must survive being looked at."""
    from grocery_planner.gui.roster import ClientDialog

    client = _add("Ana Ruiz", 90.0, "lb")
    for _ in range(3):
        dialog = ClientDialog(window.roster, client=client_service.get_client(client.id))
        dialog.on_save()
    assert client_service.get_client(client.id).weight_kg == client.weight_kg


def test_the_gui_edit_dialog_refuses_a_blank_name_and_stays_open(window):
    from grocery_planner.gui.roster import ClientDialog

    client = _add("Ana Ruiz", 90.0, "lb")
    dialog = ClientDialog(window.roster, client=client)
    dialog.name_edit.setText("   ")
    dialog.on_save()

    assert dialog.saved is None
    assert "name" in dialog.message.text().lower()
    assert client_service.get_client(client.id).name == "Ana Ruiz"


def test_remove_is_disabled_until_a_client_is_actually_selected(window):
    """No selection, no removal -- the first of the three deliberate acts."""
    roster = window.roster
    roster.reload()
    assert roster.remove_btn.isEnabled() is False
    assert roster.edit_btn.isEnabled() is False

    ana = _add("Ana Ruiz", 90.0, "lb")
    roster.reload()
    roster.select_client(ana.id)
    assert roster.remove_btn.isEnabled() is True
    assert roster.edit_btn.isEnabled() is True


def test_the_confirmation_names_the_client_and_defaults_to_cancel(window):
    from grocery_planner.gui.roster import ConfirmRemoveDialog

    client = _add("Ana Ruiz", 90.0, "lb")
    dialog = ConfirmRemoveDialog(client, client_service.client_target(client), window)

    assert "Ana Ruiz" in dialog.detail.text()
    assert "90 lb" in dialog.detail.text()
    assert dialog.remove_btn.text() == "Remove Ana Ruiz"   # not "OK"
    assert dialog.cancel_btn.isDefault() is True
    assert dialog.remove_btn.isDefault() is False
    assert "restore" in dialog.warning.text()


def test_cancelling_the_confirmation_removes_nothing(window, monkeypatch):
    from grocery_planner.gui import roster as roster_module

    ana = _add("Ana Ruiz", 90.0, "lb")
    monkeypatch.setattr(
        roster_module.ConfirmRemoveDialog, "exec", lambda self: roster_module.QDialog.Rejected
    )
    window.roster.reload()
    window.roster.select_client(ana.id)
    window.roster.on_remove_client()

    assert client_service.get_client(ana.id).is_deleted is False
    assert "not removed" in window.roster.message.text()


def test_a_removal_can_be_undone_from_the_roster(window, monkeypatch):
    from grocery_planner.gui import roster as roster_module

    ana = _add("Ana Ruiz", 195.0, "lb")
    monkeypatch.setattr(
        roster_module.ConfirmRemoveDialog, "exec", lambda self: roster_module.QDialog.Accepted
    )
    roster = window.roster
    roster.reload()
    roster.select_client(ana.id)
    roster.on_remove_client()

    assert client_service.get_client(ana.id).is_deleted is True
    assert roster.undo_btn.isHidden() is False

    roster.on_undo_remove()
    back = client_service.get_client(ana.id)
    assert back.is_deleted is False
    assert back.weight_display == pytest.approx(195.0)      # nothing lost in the trip
    assert roster.undo_btn.isHidden() is True               # nothing left to undo


def test_the_roster_row_shows_the_entered_unit_and_no_invented_target(window):
    _add("Ana Ruiz", 195.0, "lb")
    _add("Dev Patel")
    roster = window.roster
    roster.reload()

    labels = [
        roster.client_list.item(i).text() for i in range(roster.client_list.count())
    ]
    assert any("195 lb" in label for label in labels)
    assert any("Dev Patel — weight not on file" in label for label in labels)
    assert not any("Dev Patel — 0" in label for label in labels)


# --------------------------------------------------------------------------- #
# 6. The repository still owns the SQL -- the service is not a second one
# --------------------------------------------------------------------------- #
def test_the_service_writes_the_same_rows_the_repository_reads(env_db):
    saved = _add("Ana Ruiz", 90.0, "lb")
    from_repo = CustomerRepository.get(saved.id)
    assert from_repo.name == "Ana Ruiz"
    assert from_repo.weight_kg == pytest.approx(90 * KG_PER_LB)
    assert from_repo.weight_unit == "lb"
