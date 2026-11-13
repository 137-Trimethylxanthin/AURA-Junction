import type { DrawingSummary } from '@/api/contract'

/** JSON-free module: routes and the library can name the bundled drawing without pulling its 100 kB file in. */
export const BUNDLED_DRAWING_ID = 'swiss-46639'

/** Library card for the bundled drawing; a unit test keeps it equal to drawing.swiss-46639.json. */
export const BUNDLED_SUMMARY: DrawingSummary = {
  id: BUNDLED_DRAWING_ID,
  title: 'Plan 3e étage',
  subtitle: 'Site 11420 · 1:200',
  kind: 'cad_dxf',
  status: 'ready',
  source: 'library',
  thumbnail: '/drawings/swiss-46639/plan.svg',
  credit: {
    text: 'Swiss Dwellings v3.0.0 by Matthias Standfest et al. (Archilyse AG), doi:10.5281/zenodo.7788422, CC BY 4.0. Rendered from the dataset geometry; labels and numbering are synthetic.',
    license: 'CC BY 4.0',
    url: 'https://doi.org/10.5281/zenodo.7788422',
  },
}
