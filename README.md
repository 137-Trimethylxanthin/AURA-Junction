# AURA: Automated Understanding of Rooms & Areas

Ask questions about a construction drawing and see the evidence on the plan. Built for the Stoa challenge "Make Blueprints Talk" at Junction 2026.

Principle: where the drawing has CAD data (DXF) and the question matches a template, the answer comes from plain code (ezdxf + shapely) with **0 model calls**, and every number shown comes from a tool result. Gemini is used only where geometry cannot answer (raster plans, understanding a question no template matches, rewriting rambling text, answer checks), and every answer shows how many model calls it cost.

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

Always pass `--no-proxy-headers`: uvicorn otherwise trusts `X-Forwarded-For` from 127.0.0.1, which lets a client forge the IP used for per-IP limits. Modal's own web serving is unaffected. In the fallback where the local API sits behind a `cloudflared` quick tunnel, every client then appears as 127.0.0.1, so per-IP limits (the 40/h router-miss model limit and the 10/h upload limit) become global for the tunnel.

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

Needs a Modal login and a Cloudflare login, which only a human can do. Nothing here runs automatically.

1. Secrets (once): `modal secret create aura-gemini GEMINI_API_KEY=...` and `modal secret create aura-presenter AURA_PRESENTER_TOKEN=<random, 128-bit or more> AURA_IP_SALT=<random string>`. The app refuses to start on Modal without a non-default `AURA_IP_SALT`.
2. Volume (once, before the first upload): `modal volume create aura-data --version=2`.
3. Preprocess drawings locally (writes `backend/build/`), then upload them **immediately before deploying, never while the app is live** (the app is the only writer to the Volume):
   `modal volume put aura-data backend/build/drawings /drawings`
4. Stop the old app if one is running (`modal app stop aura-backend`), so two containers never count the budget at once. Then deploy, stating the Gemini requests already used on this key (Google Cloud console):
   `AURA_BUDGET_SEED=<used so far> modal deploy backend/modal_app.py`. It prints the web URL. Without `AURA_BUDGET_SEED` the container refuses to start. The image installs exactly the versions in `backend/requirements-modal.txt`, exported from `uv.lock` (regenerate it after changing dependencies; a test checks it).
5. Put that URL in `frontend/public/_headers` in **both** `connect-src` (API calls) and `img-src` (plans and thumbnails are images served by the API; without it the CSP blocks them in production). Never widen either to `*`. Then build and deploy the site:
   `cd frontend && VITE_API_URL=<modal-url> npm run build && npx wrangler pages deploy dist --project-name aura-j26`
   If the Pages project has another name, set `ALLOWED_ORIGIN_REGEX` on the backend to match it (CORS and uploads only accept the pinned project).
6. Check: `scripts/smoke.sh <modal-url> <pages-url>`

**Presenter link.** Share it as `https://<site>/app#presenter=TOKEN`. The token is in the URL fragment, which browsers never send to a server, so it stays out of request logs and `Referer` headers; the app stores it and sends it only as the `x-aura-presenter` header. `?presenter=TOKEN` still works but is the fallback (a query string reaches Cloudflare's logs).

**Turning models off live.** `curl -X POST <modal-url>/admin/models -H 'x-aura-presenter: TOKEN' -H 'content-type: application/json' -d '{"on": false}'` stops every model call at once (a `MODELS_OFF` file on the Volume, read on every call); `{"on": true}` turns them back on. Alternatives: redeploy with `AURA_MODELS=off`, or `modal app stop aura-backend` as the hard stop.

To re-create the frontend snapshot: `scripts/snapshot.sh`.

## Configuration (environment variable names only)

Generated from `backend/aura/config.py` by `backend/scripts/env_table.py --write`; a test fails if they differ.

<!-- env-table:start -->
| Variable | Where | Default | What it does |
|---|---|---|---|
| `DATA_DIR` | set in `modal_app.py` |  | SQLite database, uploads and logs (the Volume, `/data`) |
| `AURA_BUILD_DIR` | backend env |  | Preprocessed library drawings |
| `ALLOWED_ORIGINS` | backend env |  | Exact CORS origins (comma list or JSON list) |
| `ALLOWED_ORIGIN_REGEX` | backend env |  | CORS regex; the default pins the Pages project `aura-j26` and its preview subdomains. Set it if the project is named otherwise |
| `GEMINI_API_KEY` | Modal secret `aura-gemini` |  | Gemini access, server side only |
| `AURA_PRESENTER_TOKEN` | Modal secret `aura-presenter` |  | Presenter token: bypasses per-IP limits, unlocks image uploads and the model on/off switch |
| `AURA_IP_SALT` | Modal secret `aura-presenter`, **required on Modal** |  | Secret salt for hashed client IPs (the app refuses to start on Modal with the default) |
| `AURA_EVENT_CAP` | backend env | `1000` | Event-wide request cap (seed + counted + pending) |
| `AURA_BUDGET_SEED` | deploy shell, **required on Modal** | `94` | Gemini requests already used on this key before this database |
| `AURA_DAILY_CAP` | backend env | `350` | Public requests per Europe/Helsinki day |
| `AURA_DEV_CAP` | backend env | `100` | Requests in the `dev` bucket |
| `AURA_MEASUREMENT_CAP` | backend env | `120` | Requests in the `measurement` bucket |
| `AURA_IP_AGENT_PER_HOUR` | backend env | `40` | Router-miss model requests (choice, agent, rewrite) per client IP per hour |
| `AURA_RASTER_PUBLIC_CAP` | backend env | `0` | Image analyses allowed without the presenter token (0 = presenter only) |
| `AURA_BUDGET_TZ` | backend env | `Europe/Helsinki` | Time zone of the daily cap |
| `AURA_DECISION_MODEL` | backend env | `gemini-decision-1` | Model for the Decisions API (checks, question choice) |
| `AURA_AGENT_MODEL` | backend env | `gemini-3.1-flash-lite` | Model for the function-calling agent and /rewrite |
| `AURA_TURN_DEADLINE_S` | backend env | `60` | Deadline of one /ask or /chat turn (s) |
| `AURA_VOLUME_COMMIT_S` | backend env | `60` | Modal Volume commit interval (s) |
| `AURA_CLEANUP_S` | backend env | `3600` | Interval of the expiry sweep (s) |
| `AURA_UPLOAD_TTL_S` | backend env | `86400` | Lifetime of uploads, answers, conversations and cached answers (s) |
| `AURA_UPLOADS_PER_IP_HOUR` | backend env | `10` | Uploads per client IP per hour (presenter exempt) |
| `AURA_MAX_QUEUED_UPLOADS` | backend env | `20` | Uploads queued or parsing at once |
| `AURA_UPLOAD_QUOTA_BYTES` | backend env | `2147483648` | Total bytes of all stored uploads |
| `AURA_MODELS` | backend env |  | Kill switch: `off` disables every model call (read on each call; fixed per container on Modal) |
| `AURA_LIVE` | tests |  | `1` enables live Gemini tests |
| `VITE_API_URL` | frontend build |  | API base URL |
<!-- env-table:end -->

Secrets are never committed. `.env` files are git-ignored.

## Gemini budget rules

Every Gemini call goes through `budget.reserve` and is committed or released. There is an event-wide total, a public daily cap (350), global `dev` and `measurement` buckets, per-IP limits on router-miss model requests (40/h) and uploads (10/h), a presenter bypass and a kill switch. Library answers are precomputed and cached. Hitting a cap returns a clear error, not a silent fallback. Every request that was sent is reported in the answer's `calls`/`cost_usd`, also when the model step then fails; a turn that hits its deadline reports them in the timeout error (504 or the SSE `error` event). An unexpected server error (500) carries no cost fields; its requests are still counted in the budget.

What the 1,000-request event cap does and does not guarantee:

- **Durable count.** Each request is written to the `requests` table and committed on its own connection *before* it is sent (status -1 until it settles). A crash, a failed write batch or a timeout cannot lose it. A request that was reserved but never sent is deleted again; if the process dies between reserving and sending, the row stays and counts (conservative). Within 50 of the cap the Modal Volume is committed on every request.
- **One database only.** The cap counts what this database has seen, plus `AURA_BUDGET_SEED`. It does not know about requests from other servers, eval scripts, teammates or an earlier Volume that use the same key. A fresh Volume starts at the seed.
- **The seed is explicit.** On Modal the app refuses to start unless `AURA_BUDGET_SEED` is set at deploy time (Deploy, step 4). Set it to the usage shown in Google's console, not a guess. Locally the default is 94.
- **Two containers.** During a redeploy the old and new container can briefly run together, each with its own in-memory counter. Stop the old app first (`modal app stop aura-backend`).
- **Kill switch.** Live: `POST /admin/models {"on": false}` with the presenter token (see Deploy). `AURA_MODELS=off` is fixed when a container starts. The immediate hard stop is `modal app stop aura-backend`.
- **Not a provider quota.** The only hard guarantee across restarts, redeploys and other clients is a request quota on the key in Google Cloud Console. Set one if the key is shared.

## Data and licences

Code: MIT (`LICENSE`). Dependencies and data credits: `THIRD_PARTY.md`. The bundled drawing derives from Swiss Dwellings v3.0.0 (CC BY 4.0, Standfest et al., Archilyse AG). CubiCasa5k (CC BY-NC-SA) is used locally for evaluation only and is not redistributed.
