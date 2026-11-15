import type { Drawing } from '@/api/contract'

/**
 * Whether the 3D view has a model to show. It is built from the vector plan (plan.svg: walls, rooms,
 * openings). A drawing shown as a page image (a PDF sheet, a scan without a wall model) would only
 * get its ink raised off the sheet, title block and details included, so 3D is not offered there.
 */
export function has3dModel(drawing: Pick<Drawing, 'images' | 'kind'>): boolean {
  return /\.svg(\?|$)/i.test(drawing.images?.rebuilt ?? '') || drawing.kind !== 'raster'
}
