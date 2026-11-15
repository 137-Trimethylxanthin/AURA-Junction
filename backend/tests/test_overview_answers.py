"""“What does this drawing show?” is answered from the drawing's own record, with 0 model calls."""

from types import SimpleNamespace

import pytest

from aura import overview_answers as O

DWG = {
    "id": "st-a101", "title": "XSC-Sheet - A-101-B - Enlarged First Floor Plan (East)", "kind": "cad_dxf",
    "subtitle": "OpeningDesign · Sports Complex · DWG · 18 doors · 27 fixtures", "rooms": [],
    "evidence": {"door": [{}] * 18, "washbasin": [{}] * 20, "wc": [{}] * 3}, "printed_dimensions": 72,
    "evidence_method": {"door": "cad_geometry"}, "store": {"drawing_key": "k"},
}
PDF = {
    "id": "st-cannery", "title": "20181009 - Cannery Residences", "kind": "raster", "source_ext": ".pdf",
    "subtitle": "OpeningDesign · CTR · PDF · page 7 of 16 · 22 rooms · 15 doors · 52 windows",
    "rooms": [{"outdoor": i < 9} for i in range(22)], "sheet": {"page": 7, "pages": 16},
    "evidence": {"door": [{}] * 15, "window": [{}] * 52}, "printed_dimensions": 91,
    "dimensions": [{"x": 1.0}] * 10 + [{}] * 81, "store": {"drawing_key": "k"},
}
SCAN = {"id": "st-scan", "title": "Historic survey 12", "kind": "raster", "subtitle": "Historic · PNG · not interpreted",
        "rooms": [], "evidence": {}, "store": {"drawing_key": "k"}}


@pytest.mark.parametrize("q", [
    "What does this drawing show?", "what is this drawing?", "describe this sheet", "Describe the drawing.",
    "What is shown on this plan?", "What am I looking at?", "Was zeigt diese Zeichnung?", "Beschreibe diesen Plan",
    "Was ist auf dieser Zeichnung zu sehen?", "Que montre ce dessin ?", "Mitä tämä piirustus esittää?",
])
def test_overview_questions_are_recognised(q):
    assert O.is_overview_question(q)


@pytest.mark.parametrize("q", [
    "How many doors are there?", "What does this drawing show about fire exits?", "What is the area of the kitchen?",
    "What is this room?", "Describe the kitchen", "Was zeigt die Tür 3?",
])
def test_other_questions_are_not(q):
    assert not O.is_overview_question(q)


def test_a_dwg_is_described_from_its_record():
    text = O.describe(DWG, "en")
    assert text == ("“XSC-Sheet - A-101-B - Enlarged First Floor Plan (East)” is a floor plan from "
                    "OpeningDesign · Sports Complex (DWG). Read from it: 18 doors, 23 fixtures and 72 printed dimensions.")


def test_a_pdf_page_says_which_page_and_counts_only_that_page():
    text = O.describe(PDF, "en")
    assert "(PDF, page 7 of 16)" in text
    assert "22 rooms (9 of them outdoor), 15 doors, 52 windows and 10 printed dimensions" in text
    assert text.endswith("The counts are for the page shown.")


def test_nothing_read_is_said_and_no_kind_is_invented():
    text = O.describe(SCAN, "en")
    assert text.startswith("“Historic survey 12” is a drawing from Historic (PNG).")
    assert "No rooms, doors, windows" in text


def test_sheet_kinds_from_the_title():
    for title, kind in (("A001 - Site Plan", "a site plan"), ("A80 Elevations", "an elevation sheet"),
                        ("Wall Sections", "a section sheet"), ("Door Schedule", "a schedule sheet (not a plan)")):
        assert kind in O.describe(dict(SCAN, title=title), "en")


def test_built_answer_is_a_note_without_model_calls_in_the_questions_language():
    d = SimpleNamespace(id="st-a101", drawing=DWG)
    for q in ("What does this drawing show?", "what is this drawing?", "describe this sheet"):
        built = O.try_answer(q, "en", d)
        assert built.body["answer"] == {"kind": "note", "value": O.describe(DWG, "en")}
        assert built.results[0].calls == 0 and built.body["evidence"] == {"boxes": [], "rooms": []}
    de = O.try_answer("Was zeigt diese Zeichnung?", "en", d)   # the router saw no language signal
    assert de.body["answer"]["value"].startswith("„XSC-Sheet")
    assert "ist ein Grundriss aus OpeningDesign · Sports Complex (DWG). Daraus gelesen: 18 Türen" in de.body["answer"]["value"]
    assert de.body["detected_lang"] == "de"
    assert O.try_answer("How many doors are there?", "en", d) is None
    assert O.try_answer("What does this drawing show?", "en", SimpleNamespace(id="swiss", drawing={"rooms": []})) is None


def test_every_language_has_every_sentence_noun_and_sheet_kind():
    import re
    langs = {"en", "de", "fr", "fi", "it"}
    holes = lambda t: sorted(re.findall(r"\{(\w+)\}", t))  # noqa: E731
    assert set(O.TEXT) == set(O.NOUNS) == set(O.DRAWING) == langs
    for lang in langs:
        assert set(O.TEXT[lang]) == set(O.TEXT["en"]) and set(O.NOUNS[lang]) == set(O.NOUNS["en"])
        for key, text in O.TEXT[lang].items():
            assert text and holes(text) == holes(O.TEXT["en"][key]), (lang, key)
    for _rx, names in O.SHEET_KINDS:
        assert set(names) == langs and all(names.values())


def test_italian_overview_question_is_answered_in_italian():
    d = SimpleNamespace(id="st-a101", drawing=DWG)
    for q in ("Che cosa mostra questo disegno?", "Cosa mostra questa pianta?", "Cos'è questo disegno?", "Descrivi questa tavola"):
        assert O.is_overview_question(q), q
        built = O.try_answer(q, "en", d)                      # the router saw no language signal
        assert built.body["answer"]["value"] == (
            "«XSC-Sheet - A-101-B - Enlarged First Floor Plan (East)» è una pianta da OpeningDesign · Sports Complex "
            "(DWG). Vi è stato letto: 18 porte, 23 elementi di arredo e sanitari e 72 quote stampate."), q
    assert not O.is_overview_question("Quante porte ci sono in questo disegno?")
