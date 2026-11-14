"""A whole-building IFC count boxes each element on the storey drawing that holds it."""
from types import SimpleNamespace

from aura.tools import _box_on_sibling_floors


class FakeRegistry:
    def __init__(self, drawings):
        self.drawings = drawings

    def library_ids(self):
        return list(self.drawings)

    def get(self, i):
        return self.drawings.get(i)


def storey(win_guids):
    boxes = [{'id': f'win-{k + 1:02d}', 'x': k, 'y': 1, 'w': 1, 'h': 1, 'subject': 'window', 'note': f'IFC {g}'}
             for k, g in enumerate(win_guids)]
    return SimpleNamespace(drawing={'evidence': {'window': boxes}})


def test_elements_are_boxed_on_their_own_storey_and_the_rest_stay_rows():
    reg = FakeRegistry({
        'ifc-m--level-1': storey(['g1', 'g2']),
        'ifc-m--level-2': storey(['g3']),
        'ifc-other--level-1': storey(['g4']),  # another building: never used
    })
    d = SimpleNamespace(id='ifc-m--level-1', registry=reg)
    raw = {'answer': {'value': 4}, 'summary': '4 windows in the model.',
           'evidence': {'boxes': [], 'rooms': [], 'rows': [{'id': g, 'name': g} for g in ('g1', 'g3', 'g4', 'g9')]}}
    out = _box_on_sibling_floors(d, 'window', raw)
    boxes = out['evidence']['boxes']
    assert [(b['id'], b['drawing_id']) for b in boxes] == [('win-01@level-1', 'ifc-m--level-1'),
                                                           ('win-01@level-2', 'ifc-m--level-2')]
    assert [r['id'] for r in out['evidence']['rows']] == ['g4', 'g9']
    assert out['answer']['value'] == 4  # the count is untouched
    assert '2 of them boxed on their storey plans (2 floors); 2 have no box.' in out['summary']


def test_single_drawings_and_answers_with_boxes_are_left_alone():
    raw = {'evidence': {'boxes': [{'id': 'win-01'}], 'rows': [{'id': 'g1'}]}}
    d = SimpleNamespace(id='ifc-m--level-1', registry=FakeRegistry({}))
    assert _box_on_sibling_floors(d, 'window', raw) is raw
    raw2 = {'evidence': {'boxes': [], 'rows': [{'id': 'g1'}]}}
    assert _box_on_sibling_floors(SimpleNamespace(id='swiss-1', registry=None), 'window', raw2) is raw2
