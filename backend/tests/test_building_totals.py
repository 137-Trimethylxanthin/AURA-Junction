"""A building total over IFC storeys is the sum of the storeys, never the model once per storey."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura.config import Settings
from aura.main import create_app

BUILD = Path(os.environ.get("AURA_TEST_BUILD_DIR", "/root/aura-deploy/build-drawings/drawings"))
MODELS = {"ifc-hitos_arc": "ifc-hitos_arc--2-etg",
          "ifc-bsc-esplanades_1807-ep-ar-v18": "ifc-bsc-esplanades_1807-ep-ar-v18--2-korrus"}

TRUE_DOORS = {"ifc-hitos_arc": 198, "ifc-bsc-esplanades_1807-ep-ar-v18": 211}

pytestmark = pytest.mark.skipif(
    not all((BUILD / d).is_dir() for d in MODELS.values()), reason="IFC bundles not built here")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("totals")
    with TestClient(create_app(Settings(DATA_DIR=tmp / "data", AURA_BUILD_DIR=BUILD))) as c:
        yield c


def _value(c, did, q, **extra):
    r = c.post("/ask", json={"drawing_id": did, "question": q, **extra})
    assert r.status_code == 200, r.text
    return r.json()["answer"]["value"]


@pytest.mark.parametrize("stem", sorted(MODELS))
def test_whole_building_words_do_not_multiply_the_model(client, stem):
    did = MODELS[stem]
    floors = sorted(p.name for p in BUILD.iterdir() if p.name.startswith(stem + "--"))
    per_floor = [_value(client, f, "How many doors are there on this floor?") for f in floors]
    model = _value(client, did, "How many doors are there in the whole building?",
                   scope={"kind": "model"})
    assert sum(per_floor) == model == TRUE_DOORS[stem]
    for q in ("How many doors are there in the whole building?",
              "How many doors are there on all floors together?",
              "How many doors are there?"):
        assert _value(client, did, q) == model, q
    scope = {"kind": "project", "project_id": "proj-any", "drawing_ids": floors}
    assert _value(client, did, "How many doors are there in the whole building?", scope=scope) == model
