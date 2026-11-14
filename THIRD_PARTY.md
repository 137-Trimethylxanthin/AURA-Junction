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
| ifcopenshell (optional extra `ifc`) | 0.9.0 | LGPL-3.0-or-later; used as an imported library, not modified or vendored, only in local preprocessing and the eval (not in the served app) |
| pytest, pytest-asyncio, ruff (dev only) | 9.2.0, 1.5.0, 0.17.5 | MIT, Apache-2.0, MIT |

Regenerate: `cd backend && uv pip list`, then check each package's `importlib.metadata` licence fields.

## Agent Service (`agent-service/`, Python 3.12 sidecar)

`agent-service/` is authored by Team **KuK Hofclankerei** and published as part of AURA under the repository's **MIT** licence (forked from the teammate's `junction-service` on 14 Nov 2026 with explicit MIT consent; PyMuPDF and LibreDWG/`dwg2dxf` were removed so zero AGPL/GPL dependencies exist).

Direct dependencies (`agent-service/requirements.txt`):

| Package | Version | Licence |
|---|---|---|
| openai-agents | 0.24.0 | MIT |
| openai | 3.51.0 | Apache-2.0 |
| fastapi | 0.162.7 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.33 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| anyio | 4.16.0 | MIT |
| ezdxf | 1.4.5 | MIT |
| pdfplumber | 0.11.10 | MIT (uses `pdfminer.six` 20260107, MIT) |
| pypdfium2 | 5.15.2 | BSD-3-Clause / Apache-2.0 (bundles PDFium, BSD-3-Clause) |
| shapely | 2.2.0 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 |
| scikit-image | 0.26.0 | BSD-3-Clause |
| pillow | 13.0.0 | MIT-CMU (HPND-style) |
| matplotlib | 3.11.2 | PSF-2.0 / BSD-compatible |
| rapidocr-onnxruntime | 1.4.4 | Apache-2.0 |
| onnxruntime | 1.32.0 | MIT |
| pytesseract | 0.3.13 | Apache-2.0 |
| ifcopenshell | 0.9.0.post1 | LGPL-3.0-or-later (imported dynamically as an unmodified library) |
| pytest, pytest-asyncio (dev only) | 9.2.0, 1.5.0 | MIT, Apache-2.0 |

## Frontend (npm, production)

`npx license-checker --production --summary` reports: MIT 124, OFL-1.1 2, Apache-2.0 2, MPL-2.0 2, ISC 2, 0BSD 2, BlueOak-1.0.0 1, BSD-3-Clause 1, plus the frontend package itself (private, "UNLICENSED"). No GPL or AGPL.

Direct dependencies: react, react-dom, react-router, radix-ui, shadcn/ui components (MIT, copied source), tailwindcss, lucide-react, motion, sonner, zustand, react-resizable-panels, class-variance-authority, tw-animate-css, three (3D view; loaded only when it is opened) (all MIT or ISC).

Fonts (SIL OFL-1.1, via `@fontsource-variable`): Inter, JetBrains Mono.

## Data

- **Swiss Dwellings v3.0.0**, by Matthias Standfest, Michael Franzen, Yvonne Schröder, Luis Gonzalez Medina, Yarilo Villanueva Hernandez, Jan Hendrik Buck, Yen-Ling Tan, Milena Niedzwiecka, Rachele Colmegna (Archilyse AG), https://doi.org/10.5281/zenodo.7788422, licence [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Changes: the drawings (`frontend/public/drawings/`, `design/assets/`) were rendered by us from the published geometry, with drawing style, room and unit numbers, room names in an assigned language and a title block added; labels and numbering are synthetic. Archilyse AG does not endorse these drawings. Credit text: `design/assets/LICENSE.md`.
- **IFC library models** (source: `data/junction-dataset/data/ifc_models/LICENSE.md`; a 2D storey plan is rendered from each model with IfcOpenShell, geometry unmodified; the KIT licence is "for unrestricted use", the BIM-Speed model is CC BY 4.0, both allow rendered derivatives with credit):

  | File | Title | Licence | Credit and source |
  |---|---|---|---|
  | `kit-fzk-haus_ac20-fzk-haus.ifc` | FZK-Haus (Simple Phantasy Building) | KIT, "for unrestricted use" (LicenseRef-KIT-unrestricted) | Institute for Automation and Applied Informatics (IAI) / Karlsruhe Institute of Technology (KIT), KIT IFC Examples. https://www.ifcwiki.org/index.php?title=KIT_IFC_Examples&oldid=552 |
  | `kit-institute_ac20-institute-var-2.ifc` | Buerogebaeude (Phantasy Office Building, AC20-Institute-Var-2) | as above | as above |
  | `kit-smiley-west_ac-20-smiley-west-10-bldg.ifc` | Smiley West (terraced houses) | as above | as above |
  | `bimspeed-warmond_ar.ifc` | BIM-Speed Warmond demo site (baseline) | CC BY 4.0 | Garcia, Ana Galegos; Hartmann, Timo (2022): BIM-Speed Demonstration Projects - BIM and BEM models. TU Berlin DepositOnce, https://doi.org/10.14279/depositonce-16441.2 (EU H2020 BIM-SPEED, grant 820553). https://depositonce.tu-berlin.de/items/735d547e-789a-4124-ad49-6a24f67cacdf |

  The buildingSMART Duplex and Medical-Dental Clinic models are published under CC BY 4.0 today and were originally CC BY-ND 3.0; the dataset applies the stricter CC BY-ND 3.0. We use them only in accuracy eval counts, unchanged, and distribute no rendered or derived output from them.
  Other IFC models in the dataset (e.g. IFC-Bench v2, which includes GPL-3.0 BIMserver.org models) are not used or distributed by AURA.
- **CubiCasa5K**, Copyright (c) 2019 CubiCasa; Kalervo, Ylioinas, Häikiö, Karhu, Kannala, "CubiCasa5K: A Dataset and an Improved Multi-Task Model for Floorplan Image Analysis", SCIA 2019, https://zenodo.org/records/2613548. The GitHub repository states CC BY-NC 4.0 and the Zenodo record CC BY-NC-SA 4.0; we apply the stricter CC BY-NC-SA 4.0 (non-commercial, share-alike). It is used locally for evaluation only. No CubiCasa image, annotation or derived image is committed (`.omc/research/gemini/experiments/` is git-ignored). The repository does contain aggregate evaluation numbers derived from it (`backend/eval/results/cubicasa.json`, `facts.json`); these are statistics, not CubiCasa content. Whether aggregate statistics count as "adapted material" under CC BY-NC-SA is unverified (we believe not).
- The Stoa challenge dataset (`data/`, 12 GB) is git-ignored and not redistributed.

## Services

Google Gemini API (called with the team's own key, server side only) and Modal and Cloudflare Pages (hosting). Their terms apply to those services; no code of theirs is vendored.
