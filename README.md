# AURA: Automated Understanding of Rooms & Areas

Ask questions about a construction drawing and see the evidence on the plan. Built for the Stoa challenge "Make Blueprints Talk" at Junction 2026.

Principle: where the drawing has CAD data (DXF), answers come from plain code (ezdxf + shapely) with **0 model calls**, and every number shown comes from a tool result. Gemini is used only where geometry cannot answer (raster plans, question understanding, answer checks), and every answer shows how many model calls it cost.

## Architecture

```
browser (Cloudflare Pages, React + Vite)  --HTTPS-->  Modal web app (FastAPI, 1 container)
                                                         |-- router -> tools over a drawing model (ezdxf/shapely)
                                                         |-- Gemini (budgeted, via aura.budget)
                                                         `-- SQLite + drawings on Modal Volume `aura-data` (/data)
```

- `frontend/`: the app and landing pages. Static build, no secrets.
- `backend/aura/`: FastAPI app (`aura.main:create_app`), solvers, renderers, budget, Gemini client.
- `backend/modal_app.py`: Modal deployment. One container (`max_containers=1`) so there is a single SQLite writer.
- `design/`: mockups and the derived assets of the bundled drawing.
- `STACK.md`: the technology choices and why.

## Local development

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, Python 3.12 (uv fetches it).

```bash
# backend
cd backend
uv sync                      # add --extra ifc for IfcOpenShell
uv run uvicorn aura.main:create_app --factory --reload --no-proxy-headers

# frontend (second terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev
```

Always pass `--no-proxy-headers`: uvicorn otherwise trusts `X-Forwarded-For` from 127.0.0.1, which lets a client forge the IP used for per-IP limits. Modal's own web serving is unaffected. In the fallback where the local API sits behind a `cloudflared` quick tunnel, every client then appears as 127.0.0.1, so per-IP limits (for example the 15/h agent limit) become global for the tunnel.

Without `VITE_API_URL` the frontend runs on its bundled drawing and local adapter.

## Tests

```bash
cd backend && uv run pytest -q                  # unit tests
cd backend && uv run pytest -q -m dataset       # needs data/ (Swiss Dwellings, CubiCasa5k)
cd backend && AURA_LIVE=1 uv run pytest -m live # live Gemini calls, spends budget
cd frontend && npx tsc -b --noEmit && npx vitest run
cd frontend && npx playwright test
```

## Deploy

Needs a Modal login and a Cloudflare login, which only a human can do.

1. Secrets (once): `modal secret create aura-gemini GEMINI_API_KEY=...` and `modal secret create aura-presenter AURA_PRESENTER_TOKEN=... AURA_IP_SALT=<random string>`
2. Preprocess drawings locally (writes `backend/build/`), then upload them **immediately before deploying, never while the app is live** (the app is the only writer to the Volume):
   `modal volume put aura-data backend/build/drawings /drawings`
3. Deploy the API: `modal deploy backend/modal_app.py`. It prints the web URL.
4. Put that URL in `frontend/public/_headers` (`connect-src`), then build and deploy the site:
   `cd frontend && VITE_API_URL=<modal-url> npm run build && npx wrangler pages deploy dist`
5. Check: `scripts/smoke.sh <modal-url> <pages-url>`

To re-create the frontend snapshot: `scripts/snapshot.sh`.

## Configuration (environment variable names only)

| Name | Where | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | Modal secret `aura-gemini` | Gemini access, server side only |
| `AURA_PRESENTER_TOKEN` | Modal secret `aura-presenter` | Presenter bypass of per-IP limits |
| `DATA_DIR` | set in `modal_app.py` | Volume mount (`/data`) |
| `AURA_IP_SALT` | Modal secret `aura-presenter` | Salt for hashed client IPs |
| `ALLOWED_ORIGINS` | backend env | CORS origins (comma list) |
| `ALLOWED_ORIGIN_REGEX` | backend env | CORS regex; default pins the Pages project `aura-j26` (and its preview subdomains). Set it to the real project name if it differs |
| `AURA_BUDGET_SEED` | backend env, **required on Modal** | Requests already used on this key before this database (default 94 locally) |
| `AURA_MODELS` | backend env | Gemini model names |
| `VITE_API_URL` | frontend build | API base URL |
| `AURA_LIVE` | tests | `1` enables live Gemini tests |

Secrets are never committed. `.env` files are git-ignored.

## Gemini budget rules

Every Gemini call goes through `budget.reserve` and is committed or released. There is an event-wide total, a daily cap, per-IP buckets, a presenter bypass and a kill switch. Library answers are precomputed and cached. Hitting a cap returns a clear error, not a silent fallback. Every request that was sent is reported in the answer's `calls`/`cost_usd`, also when the model step then fails or times out.

What the 1,000-request event cap does and does not guarantee:

- **Durable count.** Each request is written to the `requests` table and committed on its own connection *before* it is sent (status -1 until it settles). A crash, a failed write batch or a timeout cannot lose it. A request that was reserved but never sent is deleted again. Within 50 of the cap the Modal Volume is committed on every request.
- **One database only.** The cap counts what this database has seen, plus `AURA_BUDGET_SEED`. It does not know about requests from other servers, eval scripts, teammates or an earlier Volume that use the same key. A fresh Volume starts at the seed.
- **The seed is explicit.** On Modal the app refuses to start unless `AURA_BUDGET_SEED` is set at deploy time, e.g. `AURA_BUDGET_SEED=230 modal deploy backend/modal_app.py`. Set it to the usage shown in Google's console, not a guess. Locally the default is 94.
- **Two containers.** During a redeploy the old and new container can briefly run together, each with its own in-memory counter. Stop the old app first (`modal app stop aura-backend`).
- **Kill switch.** `AURA_MODELS=off` (fixed when a container starts) or, live, a file `MODELS_OFF` in `DATA_DIR` (on the Volume: `/data/MODELS_OFF`). The immediate hard stop is `modal app stop aura-backend`.
- **Not a provider quota.** The only hard guarantee across restarts, redeploys and other clients is a request quota on the key in Google Cloud Console. Set one if the key is shared.

## Data and licences

Code: MIT (`LICENSE`). Dependencies and data credits: `THIRD_PARTY.md`. The bundled drawing derives from Swiss Dwellings v3.0.0 (CC BY 4.0, Standfest et al., Archilyse AG). CubiCasa5k (CC BY-NC-SA) is used locally for evaluation only and is not redistributed.
