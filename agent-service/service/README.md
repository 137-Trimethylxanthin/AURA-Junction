# Drawing QA service

The thin agent of `agent/` (a shell, an image viewer, `agent/SYSTEM.md` + `agent/VISION.md`) behind an HTTP API,
built on FastAPI and the OpenAI Agents SDK (`openai-agents` 0.24). It answers questions about IFC / DXF /
PDF / PNG / JPG / SVG files and returns, per question, the answer with the evidence it rests on. Every step the agent
takes is available as a typed event stream.

## Run

```bash
cd agent-service
export OPENAI_API_KEY=...                       # OpenAI directly; or use ../scripts/agent-service.sh for Gemini
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8090
.venv/bin/python -m pytest tests_service -q     # tests: scripted model, no key needed
```

| Variable | Default | Meaning |
|---|---|---|
| `JUNCTION_MODEL` | the SDK default (`OPENAI_DEFAULT_MODEL`, else `gpt-5.6-luna`) | Model name (e.g. `gemini-3.8-flash`, `gemini-4-argon`, or `demo`). |
| `JUNCTION_REASONING_EFFORT` | `low` | Reasoning effort (`low`, `medium`, `high`, or `off`) passed via `ModelSettings` with automatic HTTP 400 fallback. |
| `JUNCTION_BIFROST_URL` | unset | Root URL of a Bifrost gateway, e.g. `http://localhost:8080`. When set, all model traffic goes to `<url>/openai/v1` (a URL already ending in `/v1` is used as given) and no OpenAI key is needed. |
| `JUNCTION_BIFROST_API_KEY` | unset | Bifrost virtual key, sent as the bearer token to the gateway. |
| `JUNCTION_DATA_DIR` | `agent-service/.cache/service` | Uploaded files, run records, workspaces. |
| `JUNCTION_REGISTER_ROOT` | `dataset/junction-dataset` | Only files below it can be registered by path. |
| `JUNCTION_MAX_TURNS` | 150 | Model turns per run. |
| `JUNCTION_SHELL_TIMEOUT` | 600 | Ceiling (s) for one shell call; the model may ask for less. |
| `JUNCTION_MAX_UPLOAD_MB` | 500 | Upload size limit. |
| `JUNCTION_MAX_CONCURRENT` | 4 | Agent runs executing at once; further runs stay `queued`. |
| `JUNCTION_MAX_IMAGE_PX` | 1568 | Long side of images handed to the model. |
| `JUNCTION_API_TOKEN` | unset | When set, every endpoint needs `Authorization: Bearer <token>` (or `?token=` for `EventSource` / `<img>`); without it `/health` answers only `{"status": "ok"}`, and `/docs` and `/openapi.json` are switched off. |
| `JUNCTION_SANDBOX` | `none` | `none`, `docker` or `bwrap`; see Security. An unusable mode stops start-up rather than falling back. |
| `JUNCTION_DOCKER_IMAGE` | `alpine:3.23` | Root file-system skeleton for `docker` mode (must already be pulled). |
| `JUNCTION_TRACING` | `0` | `1` enables SDK tracing, which uploads run data to OpenAI with `OPENAI_API_KEY`. Behind Bifrost it stays off unless that key is set; the gateway key is never used for it. |

## Running with Bifrost

Models other than OpenAI's are reached through a [Bifrost](https://github.com/maximhq/bifrost) gateway, which
serves the OpenAI API for every provider it is configured with. The service uses the SDK's own OpenAI Responses
model on a client pointed at the gateway (`service/agent.py::build_model`); the Responses API is required because
the SDK's Chat Completions path drops the images `view_image` returns.

```bash
mkdir -p bifrost-data && chmod 777 bifrost-data          # the container runs as uid 1000
cat > bifrost-data/config.json <<'JSON'
{"providers": {"anthropic": {"keys": [{"name": "main", "value": "env.ANTHROPIC_API_KEY", "models": ["*"], "weight": 1.0}]}},
 "governance": {"virtual_keys": [{"id": "vk-junction", "name": "junction", "value": "env.JUNCTION_VK", "is_active": true,
   "provider_configs": [{"provider": "anthropic", "allowed_models": ["*"], "key_ids": ["*"], "weight": 1}]}]}}
JSON
docker run -d --name bifrost -p 127.0.0.1:8080:8080 -v $PWD/bifrost-data:/app/data \
       -e ANTHROPIC_API_KEY=... -e JUNCTION_VK=sk-bf-choose-one maximhq/bifrost
curl http://127.0.0.1:8080/health                                              # {"components":...,"status":"ok"}
curl -H "Authorization: Bearer $JUNCTION_VK" http://127.0.0.1:8080/openai/v1/models   # ids such as anthropic/claude-haiku-4-5

export JUNCTION_BIFROST_URL=http://127.0.0.1:8080 JUNCTION_BIFROST_API_KEY=sk-bf-choose-one
export JUNCTION_MODEL=anthropic/claude-haiku-4-5
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health      # model.gateway: {"url", "reachable", "model_known"} - model_known is null when the list cannot be read
```

The provider key lives only in the gateway; the service holds the virtual key and never passes it, or the gateway
URL, to the agent's shell. What was checked against the `maximhq/bifrost` image (built 2026-11-10): it listens on
8080; `/health`; the Responses API at `/openai/v1/responses` (also `/v1/responses`), streamed; the model list at
`/openai/v1/models`; a virtual key is accepted as the bearer token (or in `x-bf-vk`), and once a `config.json` is
present requests without one are refused. With a fake Anthropic upstream behind the real gateway, a whole run of
this service went through: the `view_image` output reached the provider as an Anthropic image block, the answer
schema as `output_config`, and token usage came back. No call to a real model has been made through it: no provider
key was available.

## Endpoints

```bash
U=http://127.0.0.1:8090
curl $U/health                                           # model (+ key present? / gateway reachable?), sandbox mode, converters
curl -F file=@plan.dxf $U/files                          # -> {"file_id": "...", "name", "ext", "size"}
curl -X DELETE $U/files/$FILE                            # -> {"deleted": "..."}
curl -H 'content-type: application/json' $U/files/register \
     -d '{"path": "data/ifc_bench/ac20_arc.ifc"}'        # a file already on the server, relative to the register root
curl $U/files/$FILE/raw -o original                      # the original bytes, for a viewer

curl -H 'content-type: application/json' $U/ask \
     -d '{"file_ids": ["'$FILE'"], "questions": ["How many doors are there?"]}'
# blocks -> {"run_id", "status": "done", "answers": [...], "usage": {...tokens}, "seconds"}
# with "stream": true the response is the run's event stream instead (run id in the X-Run-Id header)

curl -H 'content-type: application/json' $U/runs -d '{"file_ids": [...], "questions": [...]}'   # 202 {"run_id"}
curl -X POST $U/runs/$RUN/cancel                         # 200 {"id": "...", "status": "cancelled" | run.status}
curl $U/runs/$RUN                                        # status queued|running|done|error, answers, usage, events
curl -N $U/runs/$RUN/events                              # Server-Sent Events: backlog first, then live, ends with the run
curl $U/runs/$RUN/artifacts                              # files the agent wrote: [{"name", "size"}]
curl $U/runs/$RUN/artifacts/render.png -o render.png     # any workspace file, inputs included (drawing.png, ...)
curl $U/runs/$RUN/views/5.png -o seen.png                # an image exactly as the model saw it (`view` of the event)
```

One answer:

```json
{"n": 1, "answer": 14, "unit": "count", "method": "...", "check": "...", "confidence": 0.9, "evidence_total": 14,
 "evidence": [{"kind": "ifc", "guid": "...", "ifc_class": "IfcDoor", "name": "..."},
              {"kind": "cad", "handle": "2F3", "layer": "A-DOOR", "bbox": [0, 0, 900, 2100], "text": "..."},
              {"kind": "image", "bbox_px": [10, 20, 60, 90], "image": "page1.png", "label": "..."},
              {"kind": "text", "text": "...", "note": "storey 2"}]}
```

`answer` is a number, a string or `null` (the file cannot answer it; also used for a question the agent skipped).
Inside the workspace files are named `drawing.<ext>`, `drawing_2.<ext>`, ...; original names are kept in the run
record (`files[].original_name`) and never shown to the model. Evidence with `bbox_px` names its image by workspace
path; fetch it from `/runs/{id}/artifacts/<image>`.

Files are served with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`; PNG, JPEG and SVG
artifacts render inline, everything else (HTML included) is a download.

Errors: unknown or malformed ids 404, a path outside its root 403, invalid input 422, oversized upload 413, missing
token 401. A failed agent run is not a server error: the run ends with `status: "error"` and a one-line `error`
(`/ask` then answers 502 with the same body).

## Events

`GET /runs/{id}/events` (and `/ask` with `stream: true`) send one SSE message per event:
`id: <seq>`, `event: <type>`, `data: <json>`. `seq` is dense and starts at 1; reconnect with `Last-Event-ID` or
`?after=<seq>` to resume. Every event has `seq`, `t` (seconds since the run started) and `type`:

| type | fields |
|---|---|
| `run_started` | `run_id`, `model`, `files` (workspace names), `questions` |
| `tool_started` | `call_id`, `tool` (`shell` or `view_image`), `command` or `path`; for a call the SDK rejected (unknown tool, arguments that do not fit) the name the model used and its raw `arguments` |
| `tool_finished` | `call_id`, `tool`, `exit_code` (null unless the command exited by itself), `ended` (`exit`, `timeout`, `overflow`: killed for printing too much, `failed`, `cancelled`, `rejected`), `timed_out`, `seconds`, `output` (head + tail, at most 4000 characters). For `view_image`: `path`, `view` (URL of the image exactly as the model saw it; stays valid when the agent overwrites the file), `width`, `height`, `shown_width`, `shown_height`; `output` holds the refusal reason if any |
| `message` | `text`: something the model said between tool calls |
| `reasoning` | `text`: a reasoning summary, when the model provides one |
| `answers` | `answers`: the final list, as in `/ask` |
| `run_finished` | `status`, `seconds`, `usage` (`requests`, `input_tokens`, `output_tokens`, `total_tokens`), `error` |

Token deltas are not forwarded: the trace is meant to show how the answer was found, and the steps do that. The SDK
reports token usage, not money, so there is no cost field. The same list is in `GET /runs/{id}` under `events` and
is persisted with the run, so finished runs survive a restart (a run that was in flight is marked `error` and
its log is closed with a `run_finished` event). Every `tool_started` is followed by a `tool_finished` with the same
`call_id`, and every run's log ends with `run_finished`.

On SIGTERM / Ctrl-C all runs are cancelled at once (they end as `error`, "cancelled (service shutting down)"), so
open event streams and blocking `/ask` calls close and the server exits without waiting for the agents.

## Scoring on the benchmark

```bash
.venv/bin/python -m service.eval_client --set dev --kinds ifc,dxf --workers 4      # needs the service running
```

Registers each drawing (same file mapping as `agent/run.py`, including `--as-ext png` to give the agent the
same-stem file in another format), posts its questions in chunks of `--max-q`, writes
`runs/service/<name>.preds.jsonl` and `<name>.report.txt` with `bench.score.report`. A chunk whose run failed is
retried up to 3 times, as the experiment harness does; a chunk that still has no answers (agent error, HTTP error,
empty answer list) is recorded with its `error` and counted under "failed questions".

## Security

The agent executes model-written shell commands. What protects what:

- All modes: the command's environment is built from nothing (PATH with the venv, HOME = workspace, locale), so
  provider keys, the Bifrost URL and key and `JUNCTION_API_TOKEN` are not in it. Each call runs in its own process
  group; the group is killed when the call ends for any reason (exit, timeout, cancellation), so background jobs
  do not outlive it. Output is read through a pipe keeping only head and tail, and a command that prints more than
  64 MB is killed: output never touches the disk. Inputs are copies, so stored originals cannot be altered.
  `view_image`, artifacts, raw files and registration resolve symlinks and refuse anything outside their root;
  images above 150 million pixels are refused before decoding. The token and a body-size budget are checked before
  any request body is read. Run and file ids are random 64-bit hex and are the only access control between clients.
- `none` (default): no isolation beyond the above. Commands run as the service user with network access and can
  read whatever that user can, including other runs' workspaces and, on Linux, the service's own environment through
  `/proc`. Processes that leave the process group (`setsid`, daemons) are found by a marker in their environment
  and killed too; one that also wipes its environment escapes. Use only with trusted callers and files, as in the
  benchmark.
- `docker` (implemented and tested on this machine): every command runs in a fresh container with no network, a
  read-only root, all capabilities dropped, `no-new-privileges`, pid and memory limits. Only the workspace is
  writable; the host's `/usr` and the Python venv are mounted read-only so the same tools are available. The service
  environment and the rest of the file system (dataset, other runs) are not visible. Not covered: container escapes
  via kernel bugs, CPU/disk exhaustion inside the workspace, and it adds about 0.3 s per call. The commands run as
  root inside the container (without capabilities).
- `bwrap`: not implemented, because bubblewrap is not installed here and could not be tested; selecting it stops
  start-up with that message.

Not protected in any mode: prompt injection through drawing content can steer what the agent does inside its sandbox
and what it answers; an accepted upload (authenticated, within the size limit) is spooled by the framework to the
temp directory before it is copied to the data dir; nothing limits what a command writes into its workspace, or
the CPU it burns until its timeout; a `kill -9` of the service leaves the commands of its runs running; there is no
rate limiting.

## Chat page

`GET /` serves a single-page React chat (`service/ui/index.html`, React loaded from esm.sh, no build step):
paste the access token (`JUNCTION_API_TOKEN`), upload a drawing (PDF, PNG/JPG, DXF, IFC), ask questions.
Each question is one background run (`POST /runs`); the page follows its event stream and shows the tool calls
live, then the answer with confidence, method, cross-check and evidence. For PNG/JPG drawings, pixel-box evidence
is drawn on the image ("show on drawing"). The page itself is public (it holds no data); every API call it makes
carries the token. The service listens on 127.0.0.1 only: reach it through an SSH tunnel
(`ssh -L 8000:127.0.0.1:8000 <host>`, then open http://localhost:8000).
