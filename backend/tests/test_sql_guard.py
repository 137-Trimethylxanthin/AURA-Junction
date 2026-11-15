"""A number from the SQL tool answers only questions that ask for a quantity or a list."""
from __future__ import annotations

import pytest

from aura.steps import _asks_quantity


@pytest.mark.parametrize("q", ["How many rooms?", "What is the total area?", "Which room is the largest?",
                               "Wie viele Türen?", "Combien de portes ?", "List all rooms", "Show all doors",
                               "Which rooms have windows?", "Kuinka monta ovea?"])
def test_quantity_or_list_questions(q):
    assert _asks_quantity(q)


@pytest.mark.parametrize("q", ["What is shown on this drawing?", "What is the scale?", "Who drew this plan?",
                               "Is there a lift?"])
def test_other_questions_are_not_answered_by_a_number(q):
    assert not _asks_quantity(q)
