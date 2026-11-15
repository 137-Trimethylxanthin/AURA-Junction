"""The estimated-scale footnote inside a plan.svg is served in the interface's language."""
from __future__ import annotations

import html

import pytest

from aura import plan_notes as pn

SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><g><text x="6" y="94">' + html.escape(pn.ESTIMATE_NOTE)
       + "</text></g></svg>")


@pytest.mark.parametrize("lang", ["de", "fr", "fi", "it"])
def test_footnote_is_translated_and_nothing_else_changes(tmp_path, lang):
    p = tmp_path / "plan.svg"
    p.write_text(SVG, encoding="utf-8")
    out = pn.translated_plan(p, lang).decode("utf-8")
    note = pn.ESTIMATE_NOTE_IN[lang]
    assert note and note != pn.ESTIMATE_NOTE and html.escape(note) in out and pn.ESTIMATE_NOTE not in out
    assert out.replace(html.escape(note), html.escape(pn.ESTIMATE_NOTE)) == SVG


def test_english_unknown_languages_and_plans_without_the_note_are_served_as_they_are(tmp_path):
    p = tmp_path / "plan.svg"
    p.write_text(SVG, encoding="utf-8")
    assert pn.translated_plan(p, "en") is None and pn.translated_plan(p, "xx") is None and pn.translated_plan(p, None) is None
    p.write_text("<svg/>", encoding="utf-8")
    assert pn.translated_plan(p, "it") is None
    assert pn.translated_plan(tmp_path / "missing.svg", "it") is None


def test_the_note_is_the_one_the_store_draws():
    model = pytest.importorskip("jstore.service.aura_model")
    assert model.ESTIMATE_NOTE == pn.ESTIMATE_NOTE
