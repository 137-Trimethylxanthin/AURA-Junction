"""Function-calling agent on gemini-3.1-flash-lite, for router misses only.

It runs when the Decisions `choice` step is unsure (confidence < 0.6) or says "other". At most
3 generateContent iterations per turn; it must end with final_answer(result_id, explanation).
The answer value always comes from the named ToolResult (answer.py), never from the model;
the explanation passes the provenance filter before it is shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aura import tools
from aura.contract import ToolResult
from aura.gemini.client import GeminiClient

MAX_ITERATIONS = 3
SYSTEM = (
    "You answer questions about one construction floor plan by calling tools. Never write a "
    "number yourself: every number must come from a tool result. Scopes are 'floor', "
    "'unit:<letter>', 'room:<id>' or 'label:<printed room label>'. Finish with final_answer, "
    "naming the result_id that answers the question, and a one- or two-sentence explanation in "
    "the user's language. If no tool can answer, call final_answer with result_id 'none'."
)


@dataclass
class AgentResult:
    final: ToolResult | None
    explanation: str
    results: list[ToolResult] = field(default_factory=list)
    calls: int = 0
    cost_usd: float = 0.0
    stopped: str = ""  # why no final answer: "no_final" | "limit" | ""


def _payload(tr: ToolResult) -> dict:
    out = {"result_id": tr.result_id, "summary": tr.summary, "supported": tr.supported}
    if tr.value is not None:
        out["value"] = tr.value
    if tr.text is not None:
        out["text"] = tr.text
    if tr.kind == "rooms":
        out["rooms"] = tr.meta.get("rooms", [])[:40]
    return out


def execute(d: tools.LoadedDrawing, name: str, args: dict) -> ToolResult | None:
    scope = tools.parse_scope_arg(args.get("scope"))
    if name == "count_objects":
        return tools.count_objects(d, str(args.get("subject", "")), scope)
    if name == "room_areas":
        return tools.room_areas(d, scope)
    if name == "list_rooms":
        return tools.list_rooms(d, scope)
    if name == "find_text":
        pattern = str(args.get("pattern", ""))[:200]
        try:
            return tools.find_text(d, pattern)
        except Exception:  # noqa: BLE001 - a bad regex from the model is just no result
            return None
    return None


def history_contents(history: list[dict]) -> list[dict]:
    """The last 6 turns as plain text (what was asked, what was answered)."""
    out: list[dict] = []
    for t in history[-6:]:
        resp = t.get("response") or {}
        ans = resp.get("answer") or {}
        said = resp.get("canonical_en", "")
        if ans.get("kind") in ("count", "area", "text"):
            said += f" → {ans.get('value')}"
        out.append({"role": "user", "parts": [{"text": t.get("message", "")}]})
        out.append({"role": "model", "parts": [{"text": f"Answered: {said}"}]})
    return out


async def run_agent(client: GeminiClient, d: tools.LoadedDrawing, question: str, *,
                    history: list[dict] | None = None, ip_hash: str | None = None,
                    presenter: bool = False) -> AgentResult:
    decl = [{"functionDeclarations": tools.TOOL_DECLARATIONS}]
    rooms = ", ".join(sorted({r["name"] for r in d.drawing.get("rooms", [])}))[:600]
    units = ", ".join(u["id"] for u in d.drawing.get("units", []))
    system = SYSTEM + f" This drawing has apartments [{units}] and room labels [{rooms}]."
    contents = [*history_contents(history or []),
                {"role": "user", "parts": [{"text": question}]}]
    res = AgentResult(final=None, explanation="")
    by_id: dict[str, ToolResult] = {}
    for _ in range(MAX_ITERATIONS):
        g = await client.generate(contents, tools=decl, purpose="agent", system=system,
                                  ip_hash=ip_hash, presenter=presenter)
        res.calls += 1
        res.cost_usd += g.cost_usd
        if not g.function_calls:
            res.stopped = "no_final"
            return res
        contents.append(g.content)
        replies = []
        final_call = None
        for fc in g.function_calls:
            if fc.name == "final_answer":
                final_call = fc
                continue
            tr = execute(d, fc.name, fc.args)
            if tr is None:
                replies.append({"functionResponse": {"name": fc.name, "response": {
                    "error": "unknown tool or bad arguments"}}})
                continue
            by_id[tr.result_id] = tr
            res.results.append(tr)
            replies.append({"functionResponse": {"name": fc.name, "response": _payload(tr)}})
        if final_call is not None:
            res.final = by_id.get(str(final_call.args.get("result_id", "")))
            res.explanation = str(final_call.args.get("explanation", ""))[:1000]
            return res
        contents.append({"role": "user", "parts": replies})
    res.stopped = "limit"
    return res


def describe_call(tr: ToolResult) -> str:
    return f"{tr.tool}({json.dumps(tr.args, ensure_ascii=False)})"
