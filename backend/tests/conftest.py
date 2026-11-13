"""Test setup: no live Gemini calls (FakeTransport only), isolated DATA_DIR per test."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
FIXTURES = Path(__file__).parent / "fixtures"
FRONTEND_DRAWING = REPO / "frontend" / "src" / "data" / "drawing.swiss-46639.json"

# Never let a test see the real key or a stray kill switch from the shell.
for _k in ("GEMINI_API_KEY", "AURA_MODELS", "AURA_PRESENTER_TOKEN", "DATA_DIR", "AURA_BUILD_DIR"):
    os.environ.pop(_k, None)


def pytest_collection_modifyitems(config, items):
    live = os.environ.get("AURA_LIVE") == "1"
    data = (REPO / "data" / "junction-dataset").exists()
    for item in items:
        if "live" in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason="live: set AURA_LIVE=1"))
        if "dataset" in item.keywords and not data:
            item.add_marker(pytest.mark.skip(reason="dataset: needs data/junction-dataset"))


CONFORMANCE = REPO / "frontend" / "src" / "api" / "conformance.fixtures.json"
SWISS_IMAGE = REPO / "design" / "assets" / "original-crop.png"


def make_app(tmp_path, *, transport=None, use_gemini=False, with_image=False, **settings):
    """App over the frontend's bundled swiss-46639 file (local.ts semantics), isolated DATA_DIR."""
    import shutil

    from aura import tools
    from aura.config import Settings
    from aura.main import create_app

    assets = None
    if with_image:
        assets = tmp_path / "assets"
        assets.mkdir(exist_ok=True)
        shutil.copy(SWISS_IMAGE, assets / "original.png")
    s = Settings(DATA_DIR=tmp_path / "data", AURA_BUILD_DIR=tmp_path / "nobuild", **settings)
    d = tools.load_file(FRONTEND_DRAWING, assets_dir=assets)
    return create_app(s, transport=transport, extra_drawings=[d],
                      use_gemini=use_gemini or transport is not None)


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    with TestClient(make_app(tmp_path)) as c:
        yield c


def no_nulls(obj, path="$"):
    if obj is None:
        raise AssertionError(f"null at {path}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            no_nulls(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            no_nulls(v, f"{path}[{i}]")
