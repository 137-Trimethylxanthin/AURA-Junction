"""Modal deployment for the AURA backend.

Deploy (from the repo root, by a human with a Modal login):
    modal deploy backend/modal_app.py

One writer container (max_containers=1): SQLite lives on the Volume and the app
cleans up its own uploads. Nothing here is scheduled and nothing else writes to
the Volume. Drawings are put on the Volume locally with `modal volume put`.
"""

import os
from pathlib import Path

import modal

HERE = Path(__file__).parent

app = modal.App("aura-backend")

# Volumes v2 is recommended by Modal; the volume is created on first deploy.
volume = modal.Volume.from_name("aura-data", create_if_missing=True, version=2)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "fastapi",
        "uvicorn[standard]",
        "pydantic",
        "pydantic-settings",
        "python-multipart",
        "httpx",
        "ezdxf",
        "shapely",
        "numpy",
        "pillow",
        "pypdfium2",
        "google-genai",
        "fastmcp",
    )
    # The deployer states the requests already used on this key; the app refuses to start without
    # it (README, "Gemini budget rules"). e.g. AURA_BUDGET_SEED=230 modal deploy modal_app.py
    .env({"DATA_DIR": "/data", "PYTHONPATH": "/root",
          "AURA_BUDGET_SEED": os.environ.get("AURA_BUDGET_SEED", "")})
    .add_local_dir(HERE / "aura", remote_path="/root/aura", ignore=["__pycache__", "*.pyc"])
)

# Eval results (facts for /drawings) are read from /root/eval/results when present.
if (HERE / "eval" / "results").is_dir():
    image = image.add_local_dir(HERE / "eval" / "results", remote_path="/root/eval/results")


@app.function(
    image=image,
    volumes={"/data": volume},
    secrets=[
        modal.Secret.from_name("aura-gemini"),  # GEMINI_API_KEY
        modal.Secret.from_name("aura-presenter"),  # AURA_PRESENTER_TOKEN, AURA_IP_SALT
    ],
    min_containers=1,
    max_containers=1,
    timeout=600,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def web():
    from aura.main import create_app  # imported here so `import modal_app` works without aura

    return create_app()
