# AURA (Automated Understanding of Rooms & Areas): frontend

Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui (radix-nova). Ask questions about a construction drawing and see the evidence on the plan. Light theme only; Inter for UI, JetBrains Mono for numbers, costs and ids.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server |
| `npm run build` | Typecheck and production build to `dist/` |
| `npm run preview` | Serve `dist/` on port 4173 |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run test` | Vitest (jsdom) |
| `npm run e2e` | Playwright (desktop 1440×900, tablets 1024×768 and 820×1180) |

First time: `npx playwright install chromium`.

## Configuration

`VITE_API_URL` — base URL of the backend API. If unset, the app answers from the bundled local data.

## Deploy

Cloudflare Pages; `public/_redirects` routes everything to `index.html`.

## Drawing credit

plan.svg, original-crop.png, mock.json and rooms_all.json are derived from swiss_dwellings/site11420_bldg17737_floor46639 (DXF and PNG).

Credit: Swiss Dwellings v3.0.0 by Matthias Standfest et al. (Archilyse AG), doi:10.5281/zenodo.7788422, CC BY 4.0. Rendered from the dataset geometry; labels and numbering are synthetic.
