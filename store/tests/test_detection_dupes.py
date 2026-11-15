"""A thing detected twice on one page image is counted once (gemini_detect.nested_duplicates, detection_dupes)."""
from jstore import detection_dupes as dd
from jstore import gemini_detect as G


def win(x, y, w=40, h=8, votes=1, score=0.8):
    return ("window", [x, y, x + w, y + h], votes, score)


def test_box_around_a_run_of_windows_goes_and_the_windows_stay():
    items = [win(0, 0), win(60, 0), win(120, 0), win(300, 0), ("window", [-5, -2, 165, 10], 1, 0.9)]
    assert G.nested_duplicates(items) == {4: 0}


def test_fragment_inside_a_usual_box_goes():
    # four showers of the usual size, and the shower head of one detected again as a shower
    items = [("shower", [0, 0, 90, 90], 2, 0.9), ("shower", [200, 0, 290, 90], 2, 0.9),
             ("shower", [400, 0, 490, 90], 2, 0.9), ("shower", [30, 30, 50, 50], 1, 0.7)]
    assert G.nested_duplicates(items) == {3: 0}


def test_neighbours_and_other_labels_are_left_alone():
    items = [win(0, 0), win(30, 0), ("door", [0, 0, 40, 8], 1, 0.8), ("door", [100, 100, 140, 140], 1, 0.8)]
    assert G.nested_duplicates(items) == {}          # windows overlap by a quarter only; the door is another label


def test_equal_boxes_keep_the_one_with_more_votes():
    items = [win(0, 0, votes=1), win(1, 0, votes=3), win(200, 0)]
    assert G.nested_duplicates(items) == {0: 1}


def test_drop_nested_marks_the_candidate():
    c = [dict(label="door", box=[0, 0, 50, 50], votes=2, score=0.9, kept=True, why="verified"),
         dict(label="door", box=[100, 0, 150, 50], votes=2, score=0.9, kept=True, why="verified"),
         dict(label="door", box=[10, 20, 40, 28], votes=1, score=0.9, kept=True, why="verified"),
         dict(label="door", box=[300, 300, 302, 302], votes=1, score=0.2, kept=False, why="verifier: nothing")]
    G.drop_nested(c)
    assert [x["kept"] for x in c] == [True, True, False, False] and "drawn once" in c[2]["why"]


class FakeDB:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def run(self, sql, **kw):
        self.sql.append((sql, kw))
        return self.rows if sql.lstrip().startswith("select") else []


def row(eid, kind, box, page=0, file="f1", votes=1, score=0.8):
    return dict(entity_id=eid, kind=kind, file_id=file, page_no=page, x0=box[0], y0=box[1], x1=box[2], y1=box[3],
                votes=votes, score=score)


def test_store_side_pairs_per_page_and_marks_superseded():
    rows = [row(1, "door", [0, 0, 50, 50]), row(2, "door", [100, 0, 150, 50]), row(3, "door", [10, 20, 40, 28]),
            row(4, "door", [10, 20, 40, 28], page=1),          # the same box on another page: not a duplicate
            row(5, "door", [0, 0, 50, 50], file="f2")]         # nor in another file
    db = FakeDB(rows)
    assert dd.of_drawing(db, "k") == {3: 1}
    assert dd.prune(db, "k", dry_run=True) == {3: 1} and not any("update" in s for s, _ in db.sql)
    dd.prune(db, "k")
    upd = [(s, kw) for s, kw in db.sql if "update" in s]
    assert len(upd) == 1 and upd[0][1] == dict(e=3, o="1") and "superseded" in upd[0][0]
    assert "raster_detection" in db.sql[0][0]                 # CAD entities have no detection row: never read
