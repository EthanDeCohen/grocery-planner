"""Tests for the GFP-51 biometrics panel: derived headline, units, live
recompute, unit-switch semantics, and Save/``client_changed``.

Mirrors tests/test_gui.py's conventions: PySide6 is optional (skipped when
the ``gui`` extra is absent), everything runs offscreen, and widgets are
never actually shown -- so visibility/focus assertions are avoided here
entirely (this panel's behaviour doesn't depend on either).
"""
import pytest

pytest.importorskip("PySide6", reason="GUI extra not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from grocery_planner import db  # noqa: E402
from grocery_planner.customers import KG_PER_LB, Customer, CustomerRepository, kg_to_lb  # noqa: E402
from grocery_planner.gui.biometrics import BiometricsPanel  # noqa: E402


@pytest.fixture
def panel(env_db, monkeypatch):
    """A BiometricsPanel over an isolated DB, rendered offscreen."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    widget = BiometricsPanel()
    yield widget
    widget.close()
    app.processEvents()


def _add(name, weight=None, unit=None, **kwargs):
    return CustomerRepository.save(
        Customer.create(name, weight=weight, weight_unit=unit, **kwargs),
        conn=db.connect(),
    )


# --------------------------------------------------------------------- #
# Headline: derived, never hand-typed
# --------------------------------------------------------------------- #
def test_headline_matches_protein_target_for_a_known_weight(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    assert "99 g/day" in panel.headline_value.text()      # 62 * 1.6
    assert "694 g/week" in panel.headline_detail.text()   # 99.2 * 7


def test_a_client_with_no_weight_gets_an_explicit_no_target_state(panel):
    """GFP-29's rule: no weight on file means no target, never a guess."""
    dev = _add("Dev Patel")
    panel.set_client(dev.id)

    assert panel.headline_value.text() == "No target"
    assert "No weight on file" in panel.headline_detail.text()


def test_initial_panel_before_any_client_is_loaded_shows_no_target(panel):
    """Nothing invented while no client has been loaded yet either."""
    assert panel.headline_value.text() == "No target"


# --------------------------------------------------------------------- #
# Units: always beside the number, and the 2.2x-bug conversion on entry
# --------------------------------------------------------------------- #
def test_entering_pounds_stores_canonical_kg_and_reads_back(panel):
    ben = _add("Ben Okafor")   # no weight yet
    panel.set_client(ben.id)

    # Realistic order: pick the unit first, then type the number in it.
    panel.unit_box.setCurrentIndex(panel.unit_box.findData("lb"))
    panel.weight_spin.setValue(195.0)
    panel.on_save()

    reloaded = CustomerRepository.get(ben.id, conn=db.connect())
    assert reloaded.weight_kg == pytest.approx(195.0 * KG_PER_LB)
    assert reloaded.weight_display == pytest.approx(195.0)


def test_weight_and_protein_unit_are_always_shown(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    assert panel.unit_box.currentText() == "kg"
    assert "g/day" in panel.headline_value.text()
    assert "g/week" in panel.headline_detail.text()


# --------------------------------------------------------------------- #
# Live recompute: the headline tracks the draft, before any Save
# --------------------------------------------------------------------- #
def test_editing_weight_updates_the_headline_before_save(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.weight_spin.setValue(80.0)
    assert "128 g/day" in panel.headline_value.text()   # 80 * 1.6

    # Editing alone must not have touched the database.
    assert CustomerRepository.get(ana.id, conn=db.connect()).weight_kg == pytest.approx(62.0)


def test_editing_protein_factor_updates_the_headline_before_save(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.factor_spin.setValue(2.0)
    assert "124 g/day" in panel.headline_value.text()   # 62 * 2.0

    assert CustomerRepository.get(ana.id, conn=db.connect()).protein_factor == pytest.approx(1.6)


# --------------------------------------------------------------------- #
# Unit-switch semantics: converts the displayed number, never the meaning
# --------------------------------------------------------------------- #
def test_switching_unit_converts_the_displayed_value(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.unit_box.setCurrentIndex(panel.unit_box.findData("lb"))

    assert panel.weight_spin.value() == pytest.approx(kg_to_lb(62.0), abs=0.1)
    # The real body weight -- and therefore the headline -- must not move
    # just because the display unit did.
    assert "99 g/day" in panel.headline_value.text()


def test_switching_unit_back_and_forth_does_not_drift_the_stored_kg(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.unit_box.setCurrentIndex(panel.unit_box.findData("lb"))
    panel.unit_box.setCurrentIndex(panel.unit_box.findData("kg"))
    panel.on_save()

    reloaded = CustomerRepository.get(ana.id, conn=db.connect())
    assert reloaded.weight_kg == pytest.approx(62.0, abs=0.01)


# --------------------------------------------------------------------- #
# Save / client_changed
# --------------------------------------------------------------------- #
def test_client_changed_fires_on_a_successful_save(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    seen = []
    panel.client_changed.connect(seen.append)
    panel.on_save()
    assert seen == [ana.id]


def test_save_persists_all_edited_fields(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.height_spin.setValue(165.0)
    panel.age_spin.setValue(34)
    panel.sex_edit.setText("female")
    panel.activity_edit.setText("moderate")
    panel.goal_edit.setText("maintenance")
    panel.notes_edit.setText("prefers chicken")
    panel.on_save()

    reloaded = CustomerRepository.get(ana.id, conn=db.connect())
    assert reloaded.height_cm == pytest.approx(165.0)
    assert reloaded.age == 34
    assert reloaded.sex == "female"
    assert reloaded.activity_level == "moderate"
    assert reloaded.goal == "maintenance"
    assert reloaded.notes == "prefers chicken"


def test_set_client_returns_false_for_a_missing_client(panel):
    assert panel.set_client(999999) is False
    assert "not found" in panel.message.text().lower()


def test_a_failed_load_clears_the_previous_client_instead_of_leaving_them(panel):
    """A stale dose beside the wrong client's name is the worst case here."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    assert "99 g/day" in panel.headline_value.text()

    panel.set_client(999999)
    assert panel.name_edit.text() == ""
    assert panel.headline_value.text() == "No target"
    assert panel.weight_spin.value() == 0.0
    assert not panel.save_btn.isEnabled()


def test_a_deleted_client_clears_the_panel_too(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    CustomerRepository.delete(ana.id, conn=db.connect())

    assert panel.set_client(ana.id) is False
    assert panel.name_edit.text() == ""
    assert panel.headline_value.text() == "No target"


def test_a_blank_name_is_refused_rather_than_erasing_the_client(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.name_edit.setText("   ")
    seen = []
    panel.client_changed.connect(seen.append)
    panel.on_save()

    assert "needs a name" in panel.message.text()
    assert seen == []                                     # nothing broadcast
    assert CustomerRepository.get(ana.id, conn=db.connect()).name == "Ana Ruiz"


def test_saving_without_a_loaded_client_does_nothing(panel):
    seen = []
    panel.client_changed.connect(seen.append)
    panel.on_save()
    assert seen == []
