"""Fixtures: an app wired to a scripted model (agents.testing.ScriptedModel), so no API key is needed."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call

from service.app import create_app
from service.config import Settings

SECRET = "sk-fake-secret-do-not-leak"
# What the scripted "agent" does in its shell call: prints its environment and renders a picture.
RENDER = ("env; echo \"key=[$OPENAI_API_KEY]\"; "
          "python -c \"from PIL import Image; Image.new('RGB', (3000, 1000), 'white').save('render.png')\"; "
          "ls")


def final(answers: list[dict[str, Any]] | None = None) -> Any:
    """The final structured output, as the model would send it under the strict schema (all keys present)."""
    ev = dict(kind="cad", guid=None, ifc_class=None, name=None, handle="2F3", layer="DOORS", bbox=[0, 0, 1, 2],
              bbox_px=None, image=None, text=None, label=None, note=None)
    default = [dict(n=1, answer=5.0, unit="count", method="counted INSERTs", evidence=[ev], evidence_total=5,
                    check="render agrees", confidence=0.9)]
    return assistant_message(json.dumps(dict(answers=default if answers is None else answers)))


def script(*steps: Any) -> ScriptedModel:
    return ScriptedModel(list(steps))


def happy_script() -> ScriptedModel:
    """shell -> view_image -> final answer: the shape of every real run."""
    return script([function_call("shell", dict(command=RENDER, timeout_s=None), call_id="call_1")],
                  [function_call("view_image", dict(path="render.png"), call_id="call_2")],
                  [final()])


@pytest.fixture
def secrets_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("JUNCTION_API_TOKEN", SECRET)
    monkeypatch.setenv("JUNCTION_BIFROST_API_KEY", SECRET)
    monkeypatch.setenv("JUNCTION_BIFROST_URL", "http://gateway.invalid:8080")


@pytest.fixture
def register_root(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "Secret Name A-801.dxf").write_text("0\nEOF\n")
    (root / "notes.txt").write_text("not a drawing")
    (tmp_path / "outside.dxf").write_text("0\nEOF\n")
    (root / "link.dxf").symlink_to(tmp_path / "outside.dxf")
    return root


@pytest.fixture
def settings(tmp_path: Path, register_root: Path) -> Settings:
    return dataclasses.replace(Settings(), model="scripted", data_dir=tmp_path / "data", register_root=register_root,
                               max_turns=8, shell_timeout=20, max_upload_mb=1, max_concurrent=2)


@pytest.fixture
def make_client(settings: Settings) -> Callable[..., httpx.AsyncClient]:
    """make_client(scripted_model, **settings overrides) -> an HTTP client talking to a fresh app in-process.
    Passing False as the model injects none, so the app builds the configured one (the Bifrost tests)."""
    def make(scripted: ScriptedModel | None | bool = None, **overrides: Any) -> httpx.AsyncClient:
        model = None if scripted is False else scripted or happy_script()
        app = create_app(dataclasses.replace(settings, **overrides), model=model)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60)
    return make


async def upload(client: httpx.AsyncClient, name: str = "Villa Kaarna floor 2.dxf", data: bytes = b"0\nEOF\n") -> str:
    r = await client.post("/files", files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def sse_events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
