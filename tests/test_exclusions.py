"""Deliberately-excluded stores and the line that explains them (GFP-299).

The point of this feature is that a store's absence is legible. These tests are
mostly about that legibility surviving edits, because the failure mode is silent:
nothing breaks when the line stops rendering, it just stops being read.
"""
import pytest

from grocery_planner import exclusions


def test_costco_is_excluded_and_says_why():
    """Absence without a reason invites "did you check?" -- and we did (GFP-298)."""
    names = {s.name for s in exclusions.EXCLUDED}
    assert "Costco" in names

    costco = next(s for s in exclusions.EXCLUDED if s.name == "Costco")
    assert costco.reason, "an exclusion with no reason is a silent cap"
    # The two things a user needs: the number is wrong, and what to do instead.
    assert "marked up" in costco.reason
    assert "in store" in costco.reason


def test_the_summary_names_every_excluded_store():
    line = exclusions.summary()
    for store in exclusions.EXCLUDED:
        assert store.name in line
        assert store.reason in line


def test_the_summary_stays_short_enough_to_read():
    """It sits under a ranking, not in a help page.

    No hard rule exists for this, so the guard is deliberately loose -- it
    catches someone pasting a paragraph in, not someone adding a store.
    """
    assert len(exclusions.summary()) < 400


def test_no_exclusions_yields_an_empty_line_not_a_cheerful_one(monkeypatch):
    """A line that says "nothing excluded" is a line the eye learns to skip."""
    monkeypatch.setattr(exclusions, "EXCLUDED", ())
    assert exclusions.summary() == ""


def test_exclusions_are_immutable():
    """A decision, not state. Nothing should be appending to this at runtime."""
    assert isinstance(exclusions.EXCLUDED, tuple)
    with pytest.raises(Exception):
        exclusions.EXCLUDED[0].name = "Something Else"


# --------------------------------------------------------------------------- #
# The GUI half -- that the line actually reaches a widget
# --------------------------------------------------------------------------- #
def test_the_strip_shows_the_exclusion_line(window):
    """Rendered whatever the ranking says.

    Deliberately NOT conditional on there being rows: an empty week is exactly
    when someone wonders where Costco went, so hiding the note then would hide
    it when it is most needed.
    """
    from grocery_planner.gui.cheapest import CheapestMeatStrip

    strips = window.findChildren(CheapestMeatStrip)
    assert strips, "the cheapest-protein strip is gone from the main window"

    label = strips[0].excluded
    assert label.isVisibleTo(window)
    assert "Costco" in label.text()
    assert "marked up" in label.text()
