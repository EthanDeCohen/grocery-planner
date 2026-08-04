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
    ACTIVITY_LEVELS,
    DEFAULT_PROTEIN_FACTOR,
    GOALS,
    SEXES,
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
    # GFP-138: dropdowns now. "maintenance" is deliberately NOT in GOALS --
    # it stands in for a value a real client already has on file, and must
    # survive rather than being snapped to the nearest offered option.
    panel.sex_box.setCurrentText("female")
    panel.activity_box.setCurrentText("moderate")
    panel.goal_box.setCurrentText("maintenance")
    panel.notes_edit.setText("prefers chicken")
    panel.budget_spin.setValue(45.0)                 # GFP-127
    panel.on_save()

    reloaded = CustomerRepository.get(ana.id, conn=db.connect())
    assert reloaded.height_cm == pytest.approx(165.0)
    assert reloaded.age == 34
    assert reloaded.sex == "female"
    assert reloaded.activity_level == "moderate"
    assert reloaded.goal == "maintenance"
    assert reloaded.notes == "prefers chicken"
    assert reloaded.weekly_budget == pytest.approx(45.0)


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


def test_the_draft_carries_fields_this_panel_does_not_edit(panel):
    """Caught on a screenshot: the page header read 112 g/day (from a 140 lb
    goal weight) while this panel read 118 (from the 148 lb they weigh now).

    _read_customer builds a draft from the WIDGETS, and anything it forgets is
    silently dropped. desired_weight_kg missing means the target falls back to
    current weight -- so two numbers appear for one client, on one page, both
    claiming to be the target.
    """
    from grocery_planner.customers import lb_to_kg

    ana = _add("Ana Ruiz", 62.0, "kg")
    stored = CustomerRepository.save(
        replace(
            CustomerRepository.get(ana.id, conn=db.connect()),
            desired_weight_kg=lb_to_kg(140),
            weekly_budget=20.0,
        ),
        conn=db.connect(),
    )
    panel.set_client(stored.id)

    draft = panel._read_customer()
    assert draft.desired_weight_kg == pytest.approx(lb_to_kg(140))
    assert draft.weekly_budget == pytest.approx(20.0)


def test_the_headline_uses_the_goal_weight_when_there_is_one(panel):
    """The visible consequence of the above."""
    from grocery_planner.customers import lb_to_kg

    ana = _add("Ana Ruiz", 62.0, "kg")          # 136.7 lb now
    CustomerRepository.save(
        replace(
            CustomerRepository.get(ana.id, conn=db.connect()),
            desired_weight_kg=lb_to_kg(140),     # goal 140 lb
        ),
        conn=db.connect(),
    )
    panel.set_client(ana.id)

    assert "112 g/day" in panel.headline_value.text()      # 140 x 0.8
    assert "goal weight" in panel.factor_ends.text()


# --------------------------------------------------------------------------- #
# GFP-138: controlled vocabularies
# --------------------------------------------------------------------------- #
def test_sex_offers_only_the_agreed_values(panel):
    """The user asked for male or female only."""
    offered = [panel.sex_box.itemText(i) for i in range(panel.sex_box.count())]
    assert offered == list(SEXES)
    assert "female" in offered and "male" in offered


def test_a_blank_is_a_legitimate_answer(panel):
    """Nothing in this app reads `sex` for any calculation. Forcing a choice
    would collect a datum the software does not use and cannot always get
    right for a real person."""
    assert SEXES[0] == ""
    assert ACTIVITY_LEVELS[0] == ""
    assert GOALS[0] == ""


def test_activity_and_goal_are_lists_too(panel):
    activity = [panel.activity_box.itemText(i) for i in range(panel.activity_box.count())]
    goals = [panel.goal_box.itemText(i) for i in range(panel.goal_box.count())]
    assert "moderate" in activity and "sedentary" in activity
    assert "cut" in goals and "maintain" in goals


def test_a_value_already_on_file_is_kept_not_snapped(panel):
    """The schema never constrained these columns, so real rows hold values
    outside any list this app now offers. Snapping them to the nearest option
    would rewrite a nutritionist's note without asking."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    CustomerRepository.save(
        replace(CustomerRepository.get(ana.id, conn=db.connect()),
                goal="something bespoke"),
        conn=db.connect(),
    )
    panel.set_client(ana.id)
    assert panel.goal_box.currentText() == "something bespoke"

    panel.on_save()
    assert CustomerRepository.get(
        ana.id, conn=db.connect()
    ).goal == "something bespoke"


# --------------------------------------------------------------------------- #
# GFP-127: the budget entry the user could never find
# --------------------------------------------------------------------------- #
def test_the_budget_can_be_set_in_the_app(panel):
    """It had a column, a CLI flag and a service module, and no way to enter
    it in the GUI -- which is why the user never saw it."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    panel.budget_spin.setValue(30.0)
    panel.on_save()
    assert CustomerRepository.get(
        ana.id, conn=db.connect()
    ).weekly_budget == pytest.approx(30.0)


def test_no_budget_is_not_a_budget_of_zero(panel):
    """Null is not zero: a client whose money has never been discussed is
    unmeasured, not permanently over budget."""
    ana = _add("Ana Ruiz", 62.0, "kg")
    panel.set_client(ana.id)
    assert panel.budget_spin.value() == 0.0
    assert panel.budget_spin.specialValueText() == "not set"

    panel.on_save()
    assert CustomerRepository.get(ana.id, conn=db.connect()).weekly_budget is None


def test_a_stored_budget_is_shown_on_load(panel):
    ana = _add("Ana Ruiz", 62.0, "kg")
    CustomerRepository.save(
        replace(CustomerRepository.get(ana.id, conn=db.connect()), weekly_budget=25.0),
        conn=db.connect(),
    )
    panel.set_client(ana.id)
    assert panel.budget_spin.value() == pytest.approx(25.0)
