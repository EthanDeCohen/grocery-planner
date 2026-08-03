"""Tests for the GFP-51 biometrics panel: derived headline, units, live
recompute, unit-switch semantics, and Save/``client_changed``.

Mirrors tests/test_gui.py's conventions: PySide6 is optional (skipped when
the ``gui`` extra is absent), everything runs offscreen, and widgets are
never actually shown -- so visibility/focus assertions are avoided here
entirely (this panel's behaviour doesn't depend on either).
"""
from dataclasses import replace

import pytest

pytest.importorskip("PySide6", reason="GUI extra not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from grocery_planner import db  # noqa: E402
from grocery_planner.customers import (
    DEFAULT_PROTEIN_FACTOR,
    MAX_PROTEIN_FACTOR,
    MIN_PROTEIN_FACTOR,
)
from grocery_planner.gui.biometrics import FACTOR_SCALE
from grocery_planner.customers import KG_PER_LB, Customer, CustomerRepository, kg_to_lb  # noqa: E402
from grocery_planner.gui import biometrics  # noqa: E402
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

    # GFP-132: 62 kg = 136.7 lb, x 0.8 g/lb = 109.4 g/day.
    assert "109 g/day" in panel.headline_value.text()
    assert "765 g/week" in panel.headline_detail.text()   # 109.4 * 7


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
    assert "141 g/day" in panel.headline_value.text()   # 80 * 1.6

    # Editing alone must not have touched the database.
    assert CustomerRepository.get(ana.id, conn=db.connect()).weight_kg == pytest.approx(62.0)


def test_editing_protein_factor_updates_the_headline_before_save(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    panel.factor_spin.setValue(1.0)                     # the top of the band
    assert "137 g/day" in panel.headline_value.text()   # 136.7 lb * 1.0

    # Still unsaved: the spin box moved the headline, not the database.
    assert CustomerRepository.get(
        ana.id, conn=db.connect()
    ).protein_factor == pytest.approx(DEFAULT_PROTEIN_FACTOR)


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
    assert "109 g/day" in panel.headline_value.text()


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
    assert "109 g/day" in panel.headline_value.text()

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


# --------------------------------------------------------------------------- #
# GFP-47 (partial) — the shipped default avatar
# --------------------------------------------------------------------------- #
def test_the_default_avatar_ships_as_package_data():
    """An asset missing from package-data is an asset that vanishes on install."""
    from importlib import resources

    package, name = biometrics.DEFAULT_AVATAR_RESOURCE
    assert resources.files(package).joinpath(name).is_file()


def test_the_default_avatar_loads_and_is_square(panel):
    pixmap = biometrics.default_avatar_pixmap(56)
    assert pixmap is not None
    assert not pixmap.isNull()
    assert pixmap.width() == pixmap.height() == 56


def test_every_client_shows_the_default_avatar_until_photos_exist(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    shown = panel.avatar_label.pixmap()
    assert shown is not None and not shown.isNull()
    assert shown.toImage() == biometrics.default_avatar_pixmap().toImage()


def test_a_missing_asset_degrades_to_the_initials_disc(panel, monkeypatch):
    """A packaging mistake must cost a nicety, not leave an empty hole."""
    monkeypatch.setattr(biometrics, "default_avatar_pixmap", lambda *a, **k: None)
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)

    shown = panel.avatar_label.pixmap()
    assert shown is not None and not shown.isNull()
    assert shown.toImage() == biometrics._initials_pixmap("AR").toImage()


def test_initials_handle_the_awkward_names():
    assert biometrics._initials("Ana Ruiz") == "AR"
    assert biometrics._initials("Cher") == "CH"
    assert biometrics._initials("") == "?"          # mid-intake, no name yet
    assert biometrics._initials("Ana de la Cruz") == "AC"


def test_saving_without_a_loaded_client_does_nothing(panel):
    seen = []
    panel.client_changed.connect(seen.append)
    panel.on_save()
    assert seen == []



# --------------------------------------------------------------------------- #
# GFP-133: the factor control -- a slider across the band, bound to a box
# --------------------------------------------------------------------------- #
def test_the_factor_hard_stops_at_the_prescribed_ends(panel):
    """The user was explicit: "hard stop at 1.0, it will be .8 to 1."

    Asserted on the WIDGET rather than on a validator, because a range Qt
    enforces cannot be got round by typing -- there is no path through the UI
    to a value the nutritionist did not prescribe.
    """
    panel.factor_spin.setValue(2.0)
    assert panel.factor_spin.value() == pytest.approx(MAX_PROTEIN_FACTOR)

    panel.factor_spin.setValue(0.1)
    assert panel.factor_spin.value() == pytest.approx(MIN_PROTEIN_FACTOR)


def test_the_slider_spans_exactly_the_band(panel):
    assert panel.factor_slider.minimum() == round(MIN_PROTEIN_FACTOR * FACTOR_SCALE)
    assert panel.factor_slider.maximum() == round(MAX_PROTEIN_FACTOR * FACTOR_SCALE)


def test_moving_the_slider_updates_the_box(panel):
    panel.factor_slider.setValue(round(0.9 * FACTOR_SCALE))
    assert panel.factor_spin.value() == pytest.approx(0.9)


def test_editing_the_box_moves_the_slider(panel):
    panel.factor_spin.setValue(0.85)
    assert panel.factor_slider.value() == round(0.85 * FACTOR_SCALE)


def test_the_two_controls_do_not_bounce(panel):
    """Slider -> box -> slider -> box is the classic infinite loop in this
    widget pair. It shows up as the value crawling, or the cursor jumping
    while you type."""
    for value in (0.82, 0.97, 0.80, 1.00, 0.91):
        panel.factor_spin.setValue(value)
        assert panel.factor_spin.value() == pytest.approx(value)
        assert panel.factor_slider.value() == round(value * FACTOR_SCALE)

        panel.factor_slider.setValue(round(value * FACTOR_SCALE))
        assert panel.factor_spin.value() == pytest.approx(value)


def test_the_slider_moves_the_headline_live(panel):
    """The number being chosen is "137 g/day", not "0.87". If the grams only
    refreshed on Save there would be no reason to prefer a slider."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    before = panel.headline_value.text()

    panel.factor_slider.setValue(round(MAX_PROTEIN_FACTOR * FACTOR_SCALE))
    assert panel.headline_value.text() != before
    assert "137 g/day" in panel.headline_value.text()


def test_the_ends_are_labelled_in_grams_for_this_client(panel):
    """0.8 and 1.0 mean nothing to a nutritionist mid-consultation; 109 and
    137 g/day do."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    ends = panel.factor_ends.text()
    assert "109 g/day" in ends and "137 g/day" in ends


def test_the_ends_say_which_weight_they_came_from(panel):
    """GFP-132: falling back to current weight is fine, doing it silently is
    not -- for a client cutting or gaining the two give different answers."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    assert "no goal weight set" in panel.factor_ends.text()


def test_loading_a_client_moves_both_controls(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    CustomerRepository.save(
        replace(CustomerRepository.get(ana.id, conn=db.connect()), protein_factor=0.95),
        conn=db.connect(),
    )
    panel.set_client(ana.id)
    assert panel.factor_spin.value() == pytest.approx(0.95)
    assert panel.factor_slider.value() == round(0.95 * FACTOR_SCALE)
