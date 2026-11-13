# Third-party software and data

AURA is MIT-licensed (see `LICENSE`). No AGPL or GPL-3 code is used: PyMuPDF and ultralytics are deliberately excluded.

## Backend (Python, direct dependencies)

Versions are those in `backend/uv.lock` at the time of writing. Licences are read from each package's metadata.

| Package | Version | Licence |
|---|---|---|
| fastapi | 0.162.7 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic-settings | 2.16.3 | MIT |
| python-multipart | 0.0.33 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| ezdxf | 1.4.5 | MIT |
| shapely | 2.2.0 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib) |
| pillow | 13.0.0 | MIT-CMU (HPND-style) |
| pypdfium2 | 5.15.2 | BSD-3-Clause / Apache-2.0 (bundles PDFium, BSD-3-Clause) |
| google-genai | 3.3.0 | Apache-2.0 |
| fastmcp | 4.2.0 | Apache-2.0 |
| modal | 1.6.4 | Apache-2.0 |
| ifcopenshell (optional extra `ifc`) | 0.9.0 | LGPL-3.0-or-later; used as an imported library, not modified or vendored |
| pytest, pytest-asyncio, ruff (dev only) | 9.2.0, 1.5.0, 0.17.5 | MIT, Apache-2.0, MIT |

Regenerate: `cd backend && uv pip list`, then check each package's `importlib.metadata` licence fields.

## Frontend (npm, production)

`npx license-checker --production --summary` reports: MIT 124, OFL-1.1 2, Apache-2.0 2, MPL-2.0 2, ISC 2, 0BSD 2, BlueOak-1.0.0 1, BSD-3-Clause 1, plus the frontend package itself (private, "UNLICENSED"). No GPL or AGPL.

Direct dependencies: react, react-dom, react-router, radix-ui, shadcn/ui components (MIT, copied source), tailwindcss, lucide-react, motion, sonner, zustand, react-resizable-panels, class-variance-authority, tw-animate-css (all MIT or ISC).

Fonts (SIL OFL-1.1, via `@fontsource-variable`): Inter, JetBrains Mono.

## Data

- **Swiss Dwellings v3.0.0**, by Matthias Standfest et al. (Archilyse AG), doi:10.5281/zenodo.7788422, licence CC BY 4.0. The bundled drawings (`frontend/public/drawings/`, `design/assets/`) are rendered from its geometry. Labels and numbering are synthetic. Credit text: `design/assets/LICENSE.md`.
- **CubiCasa5k** is CC BY-NC-SA 4.0. It is used locally for evaluation only and is **not redistributed**: no CubiCasa image, derived image or annotation is committed (`.omc/research/gemini/experiments/` is git-ignored for this reason).
- The Stoa challenge dataset (`data/`, 12 GB) is git-ignored and not redistributed.

## Services

Google Gemini API (called with the team's own key, server side only) and Modal and Cloudflare Pages (hosting). Their terms apply to those services; no code of theirs is vendored.
