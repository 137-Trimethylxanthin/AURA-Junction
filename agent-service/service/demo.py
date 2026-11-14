"""JUNCTION_MODEL=demo: a scripted stand-in model for frontend work without an API key or minutes-long runs.

Every run goes through the real API, tools, workspace and event stream; only the model is fake.  The script is
chosen from the conversation so far (how many tool results it already holds), so one instance serves any number
of runs:  1. shell: list the files   2. view_image: the uploaded image, or a rendered placeholder
          3. final answers: one canned answer per question, with evidence of the kind a real run produces.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message, function_call

RENDER = ("ls -la; python -c \"import glob; from PIL import Image, ImageDraw; "
          "imgs = [f for f in glob.glob('*') if f.lower().endswith(('.png', '.jpg', '.jpeg'))]; "
          "im = Image.open(imgs[0]).convert('RGB') if imgs else Image.new('RGB', (1200, 800), 'white'); "
          "d = ImageDraw.Draw(im); d.rectangle([100, 100, 400, 300], outline='red', width=6); "
          "im.save('demo_view.png'); print('rendered demo_view.png', im.size)\"")


def _texts(items: Any) -> str:
    out = []
    for it in items if isinstance(items, list) else [items]:
        c = it.get("content") if isinstance(it, dict) else it
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [p.get("text", "") for p in c if isinstance(p, dict)]
    return "\n".join(out)


def _answers(prompt: str) -> str:
    questions = re.findall(r"^\s*(\d+)\.\s+(.*)$", prompt, re.M)
    image = next(iter(re.findall(r"\b(drawing[\w-]*\.(?:png|jpe?g))\b", prompt, re.I)), None)
    out = []
    for n, q in questions:
        ev = (dict(kind="image", bbox_px=[100, 100, 400, 300], image=image, label="demo box", text=None)
              if image else dict(kind="text", text="demo evidence", label="demo"))
        full = dict(kind=None, guid=None, ifc_class=None, name=None, handle=None, layer=None, bbox=None,
                    bbox_px=None, image=None, text=None, label=None, note="demo mode: no model was called")
        full.update(ev)
        out.append(dict(n=int(n), answer=42, unit="count", evidence=[full], evidence_total=1, confidence=0.5,
                        method=f"Demo answer to: {q.strip()[:120]}", check="none (demo mode)"))
    return json.dumps(dict(answers=out))


class DemoModel(Model):
    def _step(self, args: tuple, kwargs: dict) -> ScriptedModel:
        items = kwargs.get("input", args[1] if len(args) > 1 else [])
        items = items if isinstance(items, list) else [items]
        done = sum(1 for it in items if isinstance(it, dict) and it.get("type") == "function_call_output")
        if done == 0:
            return ScriptedModel([[function_call("shell", dict(command=RENDER, timeout_s=None), call_id="demo_1")]])
        if done == 1:
            return ScriptedModel([[function_call("view_image", dict(path="demo_view.png"), call_id="demo_2")]])
        return ScriptedModel([[assistant_message(_answers(_texts(items)))]])

    async def get_response(self, *args: Any, **kwargs: Any):
        return await self._step(args, kwargs).get_response(*args, **kwargs)

    def stream_response(self, *args: Any, **kwargs: Any):
        return self._step(args, kwargs).stream_response(*args, **kwargs)
