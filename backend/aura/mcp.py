"""MCP server (FastMCP, streamable HTTP) mounted at /mcp: list_drawings, ask, evidence_csv.

It answers through the same pipeline and the same budget as the HTTP API. MCP callers share
one per-IP bucket ("mcp") for router-miss model calls.
"""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from aura.contract import AskRequest
from aura.csvout import evidence_csv
from aura.pipeline import Caller, Services, ask

MCP_CALLER = Caller(ip_hash="mcp", presenter=False)


def build_mcp(get_svc: Callable[[], Services]) -> FastMCP:
    mcp = FastMCP(
        "AURA",
        instructions=(
            "AURA answers quantity questions about construction floor plans from their CAD "
            "geometry (counts of doors, windows, fixtures, rooms, apartments; net areas). Every "
            "number comes with evidence boxes or rooms; abstains instead of guessing."),
    )

    @mcp.tool(run_in_thread=False)
    def list_drawings() -> list[dict]:
        """The library drawings: id, title, kind and what each can answer."""
        svc = get_svc()
        out = []
        for did in svc.registry.library_ids():
            d = svc.registry.get(did)
            if d is None:
                continue
            out.append({"id": d.id, "title": d.drawing.get("title", d.id),
                        "kind": d.drawing.get("kind", "cad_dxf"),
                        "capabilities": d.engine.capabilities(),
                        "units": [u["id"] for u in d.drawing.get("units", [])]})
        return out

    @mcp.tool(run_in_thread=False)
    async def ask_drawing(drawing_id: str, question: str) -> dict:
        """Ask a question about one drawing (EN/FR/FI/DE/IT). Returns the AURA AskResponse:
        answer, evidence, trace, gold (when a dataset question matches), calls and cost."""
        req = AskRequest(drawing_id=drawing_id, question=question)
        return await ask(get_svc(), req, MCP_CALLER)

    @mcp.tool(run_in_thread=False)
    def evidence_csv_for(answer_id: str) -> str:
        """The evidence rows of an earlier answer (by answer_id) as CSV."""
        svc = get_svc()
        resp = svc.store.answer(answer_id) if svc.store else None
        if resp is None:
            raise ValueError(f"Unknown answer '{answer_id}'")
        did = svc.store.answer_drawing(answer_id) if svc.store else None
        d = svc.registry.get(did) if did else None
        rooms = {r["room_id"]: r for r in (d.drawing.get("rooms", []) if d else [])}
        return evidence_csv(resp, rooms)

    return mcp
