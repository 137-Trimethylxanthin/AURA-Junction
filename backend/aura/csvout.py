"""Evidence rows of one answer as CSV. Text cells that a spreadsheet would run as a formula
(starting with = + - @ tab or CR) get a leading apostrophe."""

from __future__ import annotations

import csv
import io

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
COLUMNS = ["kind", "id", "subject", "room_id", "room_name", "unit", "x", "y", "w", "h",
           "included", "area_m2", "edited", "note", "check_p"]


def safe(v: object) -> object:
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return v


def evidence_csv(response: dict, rooms_by_id: dict[str, dict] | None = None) -> str:
    rooms_by_id = rooms_by_id or {}
    per_box = (response.get("check") or {}).get("per_box") or {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COLUMNS)
    ans = response.get("answer", {})
    for b in response.get("evidence", {}).get("boxes", []):
        room = rooms_by_id.get(b.get("room_id", ""), {})
        w.writerow([safe(x) for x in (
            "box", b["id"], b.get("subject", ""), b.get("room_id", ""), room.get("name", ""),
            b.get("unit", ""), b["x"], b["y"], b["w"], b["h"], "", "", "", b.get("note", ""),
            per_box.get(b["id"], ""))])
    for r in response.get("evidence", {}).get("rooms", []):
        room = rooms_by_id.get(r["room_id"], {})
        w.writerow([safe(x) for x in (
            "room", f"room-{r['room_id']}", "room", r["room_id"], room.get("name", ""),
            room.get("unit", ""), "", "", "", "", "yes" if r["included"] else "no", r["area_m2"],
            "yes" if r.get("edited") else "", "", "")])
    w.writerow([])
    summary = (f"{ans.get('value')} {ans.get('subject') or ans.get('unit') or ''}".strip()
               if ans.get("kind") in ("count", "area", "text") else f"abstain: {ans.get('reason')}")
    w.writerow([safe(x) for x in ("answer", response.get("answer_id", ""), summary,
                                  response.get("canonical_en", ""))])
    return buf.getvalue()
