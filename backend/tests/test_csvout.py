"""CSV evidence export tests: UTF-8 BOM, IFC rows, quantity & note summary formatting."""

from __future__ import annotations

import csv
import io

from aura.csvout import evidence_csv


def _parse(csv_text: str) -> list[list[str]]:
    assert csv_text.startswith("\ufeff")
    return list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))


def test_csv_bom_and_ifc_rows_and_quantity_summary():
    resp = {
        "answer_id": "a-qty-1",
        "canonical_en": "What is the total wall length on the whole model",
        "answer": {"kind": "quantity", "value": 142.8, "unit": "m"},
        "evidence": {
            "boxes": [],
            "rooms": [],
            "rows": [
                {"id": "2O2Fr$t4X7Zf8NOew3FLOH", "name": "Basic Wall:200mm", "value": 72.4},
                {"id": "3K9Qp$t4X7Zf8NOew3FLOI", "name": "=EVIL()", "value": 70.4},
            ],
        },
    }
    rows = _parse(evidence_csv(resp))
    assert rows[0][:5] == ["kind", "id", "subject", "room_id", "room_name"]
    assert rows[1][0] == "row" and rows[1][1] == "2O2Fr$t4X7Zf8NOew3FLOH"
    assert rows[1][4] == "Basic Wall:200mm" and rows[1][13] == "72.4"
    assert rows[2][4] == "'=EVIL()"
    assert rows[-1][:3] == ["answer", "a-qty-1", "142.8 m"]
    assert "abstain" not in rows[-1][2]


def test_csv_note_summary_is_not_formatted_as_abstain():
    resp = {
        "answer_id": "a-note-1",
        "canonical_en": "Which bedroom has the widest door?",
        "answer": {
            "kind": "note",
            "value": "Bedroom 3.05 has the widest door at 1.00 m",
            "number": 1.0,
            "unit": "m",
        },
        "evidence": {"boxes": [], "rooms": []},
    }
    rows = _parse(evidence_csv(resp))
    assert rows[-1][:3] == [
        "answer",
        "a-note-1",
        "Bedroom 3.05 has the widest door at 1.00 m",
    ]
