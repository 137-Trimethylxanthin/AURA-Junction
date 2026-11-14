# AURA Agent Service (`agent-service/`)

FastAPI sidecar + autonomous blueprint inspection agent (OpenAI Agents SDK) for **AURA** (*Automated Understanding of Rooms & Areas*), answering open-ended, multi-group color-coded, directional circulation, and multi-plan questions across construction drawings (`IFC`, `DXF`, `PDF`, `PNG`/`JPG`, `SVG`) with grounded evidence.

Licence: **MIT** — Team **KuK Hofclankerei** (forked from the teammate's `junction-service` on 14 Nov 2026 with MIT consent; later updates are merged by hand). All runtime dependencies are permissive (`MIT` / `Apache-2.0` / `BSD-3-Clause`, plus `ifcopenshell` `LGPL-3.0-or-later` imported unmodified).

**Start here: `service/CONTRACT.md`** — the HTTP/SSE contract (upload, register, ask, cancel, live event stream, answer + evidence format).

## Run it
```bash
# Create Python 3.12 virtualenv and install permissive dependencies
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# Launch via repo wrapper (loads ~/.config/junction/env safely on 127.0.0.1:8090; add --demo for scripted mode)
../scripts/agent-service.sh --demo

# Run offline test suite (no API key needed)
.venv/bin/python -m pytest tests_service -q
```
Optional system tool the agent may use for raster OCR: `tesseract-ocr`.

## Layout
- `service/` — FastAPI app (`app.py`), run registry & cancellation (`runs.py`), agent & schema (`agent.py`), sandboxed tools & live crop telemetry (`tools.py`), workspace & Btrfs CoW `_precomputed` staging (`workspace.py`), demo model (`demo.py`), reference UI (`ui/index.html`), `README.md`, `CONTRACT.md`.
- `agent/SYSTEM.md`, `agent/VISION.md` — runtime agent instructions.
- `bin/junction-areas`, `areas/` — deterministic room-area reader (`PDF` via `pdfplumber` + `pypdfium2`, `DXF` via `ezdxf`, `PNG`/`JPG` via `opencv`/`shapely`).

## Security note
In production / public demos, run with `JUNCTION_SANDBOX=docker` (the default in `scripts/agent-service.sh`), which executes every shell command inside a rootless Podman/Docker container (`--network none --read-only --cap-drop ALL --security-opt no-new-privileges`).
