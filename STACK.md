# Tech stack

Stoa challenge "Make Blueprints Talk": answer questions about construction drawings, correctly and verifiably.
Judged 25 % each on accuracy, visualised reasoning, creativity and low inference cost.

Principle: answer with plain code wherever the drawing has CAD data. Use a model only where it is needed, and show it on every answer ("0 model calls" when geometry answered).

Research behind these choices: `.omc/research/stoa/stoa-research.md` (sections 5, 8, 9, 10).

## Backend

| Layer | Choice | Why |
|---|---|---|
| Language, API | Python 3.12, FastAPI, `uv` | All drawing libraries are Python |
| Swiss plans (DXF twin) | `ezdxf` + `shapely` | 99.2 % correct (`18,660 / 18,792` after room-number normalisation; `99.0 %` / `18,606` raw), measured on the full set, 0 model calls |
| IFC models | `IfcOpenShell` | 99.5 % correct, measured; LGPL is fine as an imported library |
| PDF schedules, rasterising | `pdfplumber` + `pypdfium2` | Not PyMuPDF (AGPL) |
| CubiCasa images (67 % of the score) | OCR (`RapidOCR` or Tesseract `fin`) for room labels; RF-DETR (Apache-2.0) detector for doors, windows, fixtures | Biggest risk: exact-count accuracy not measured yet |
| Question routing | Template cache + `multilingual-e5-small` nearest neighbour | Cost grows with templates, not with questions |
| Prompt builder | Gemini flash-lite: voice or rambling → prompt blocks; unseen templates → JSON | |
| Answer check | Gemini Decisions API: one yes/no per numbered box, with confidence | One image billed once for up to 1,000 checks |
| Plugins | MCP server (FastMCP) around the same `ask()` | "Easy plugin usage" |
| Storage | Precomputed JSON / SQLite for geometry and evidence; MariaDB only if time allows | |

## Frontend

React + Vite + TypeScript + Tailwind + **shadcn/ui**.
Drawing: OpenSeadragon (BSD) for deep zoom, PDF.js for PDFs, custom SVG overlays for evidence. Motion: `motion/react` springs.

| Mockup piece | shadcn component |
|---|---|
| Right-click menu on the plan | `ContextMenu` (long-press on touch is built in) |
| Plan / chat split | `Resizable` |
| Select / Mark area / What-if sizes | `ToggleGroup` |
| Compare: opacity, Swipe/Blend | `Slider`, `ToggleGroup` |
| Reasoning tree, Plugins | `Sheet` or sidebar panel |
| Evidence rows, chips | `Button`, `Badge`, `ScrollArea` |
| Room size editor | `Popover` + `Input` |
| Feedback confirmations | `Sonner` |
| Question / drawing search | `Command` |

Not shadcn: evidence boxes, room polygons and the swipe divider stay custom SVG on drawing coordinates.
Run `npx shadcn@latest init` once, then add only the components we use.

## Theme: compatible with Stoa's design system, not a copy of their brand

Stoa's site (stoabuild.com, read 13 Nov 2026) runs a shadcn theme. We reuse its token names and semantic colours so our tool could drop into their product.
We keep our own name and logo and do not copy their brand look (forest green `#011e01`, Gelasio serif headlines).

Colour meanings (one colour = one meaning):

| Meaning | Token | Value |
|---|---|---|
| Agent evidence (boxes, rooms, active answer) | `--info` / `--info-surface` | `#1260b8` / `#ddeaf7` |
| Exact, from CAD geometry | `--success` / `--success-surface` | `#367c48` / `#e3f9ea` |
| User corrections (rejected, changed) | `--warning` / `--warning-surface` | `#965422` / `#f9f3d4` |
| Errors | `--destructive` / `--destructive-surface` | `#b3391e` / `#f9e5e2` |
| User input (marked areas, scope, what-if sizes) | `--user` / `--user-surface` (ours) | `#0f766e` / `#ccfbf1` |

```css
:root {
  --radius: 0.5rem;
  --background: #fdfdfd;
  --foreground: #1a1a1a;
  --card: #ffffff;
  --card-foreground: #1a1a1a;
  --popover: #ffffff;
  --popover-foreground: #1a1a1a;
  --primary: #1a1a1a;
  --primary-foreground: #ffffff;
  --secondary: #0000000d;
  --secondary-foreground: #1a1a1a;
  --muted: #0000000d;
  --muted-foreground: #666666;
  --accent: #00000008;
  --accent-foreground: #1a1a1a;
  --border: #e7e7e7;
  --input: #767676;
  --ring: #7d7d7d;
  --destructive: #b3391e;
  --destructive-foreground: #ffffff;
  --destructive-surface: #f9e5e2;
  --success: #367c48;
  --success-surface: #e3f9ea;
  --warning: #965422;
  --warning-surface: #f9f3d4;
  --info: #1260b8;
  --info-surface: #ddeaf7;
  /* ours: user input */
  --user: #0f766e;
  --user-surface: #ccfbf1;
  --font-sans: "Inter", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", monospace;
}
```

Type: Inter for UI, JetBrains Mono for numbers, costs and IDs. Minimum text size 12 px; touch targets 40–44 px (site crews on tablets).
Stoa's theme is light-only; a dark theme is optional.

## Hosting

| Part | Where | Why |
|---|---|---|
| API + detector (GPU) | Modal (`asgi_app`, scale to zero) | Modal challenge: live deployed demo is 15 % of the rubric |
| Frontend | Cloudflare Pages | Free; Stoa also uses Cloudflare |

## Prizes this stack enters

- Stoa (main target)
- Modal: detector training and serving, live API
- Google DeepMind, Best Use of Gemini API: Decisions API check + voice prompt builder
- Optional: Gemma (small open vision model on Modal as fallback checker); MariaDB (mention only)
- Skipped: Nemotron (third model), Verda ($20 top-up), Aiven, Context.dev, Oxylabs (no fit)

## Rules

- Repo is MIT. No AGPL libraries (PyMuPDF, Ultralytics/YOLO, xeokit).
- CubiCasa-trained weights stay out of the repo (data is CC BY-NC-SA).
- Keep the drawing credit visible when a drawing is shown (`data/<source>/LICENSE.md`).
- Dataset stays out of git (`/data/` is in `.gitignore`).

## Open decision

Ask Stoa whether training on CubiCasa's public annotations is allowed.
Yes: RF-DETR detector on Modal. No: OCR + Decisions API checks on candidate boxes.
