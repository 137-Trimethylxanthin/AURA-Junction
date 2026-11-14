import { listDrawings, type ClientOptions } from './client'
import { apiBase } from './library'
import { BUNDLED_DRAWING_ID } from '@/data/ids'

/** The in-app link every "open the sample drawing" entry point uses; the route picks the drawing. */
export const SAMPLE_ROUTE = '/demo'

/**
 * The drawing the sample entry points open. With a backend it is one the server really has (the bundled
 * drawing when the server lists it, else the first ready drawing of its library), so the first question
 * is never asked about a drawing unknown to the server. Without a backend, or when the list cannot be
 * read, it is the drawing bundled with the app.
 */
export async function sampleDrawingId(
  opts: ClientOptions | null = apiBase() ? { baseUrl: apiBase()! } : null,
  timeoutMs = 5_000,
): Promise<string> {
  if (!opts) return BUNDLED_DRAWING_ID
  try {
    const { drawings } = await listDrawings(opts, timeoutMs)
    if (drawings.some((d) => d.id === BUNDLED_DRAWING_ID)) return BUNDLED_DRAWING_ID
    const ready = drawings.find((d) => d.status === 'ready')
    return ready?.id ?? BUNDLED_DRAWING_ID
  } catch {
    return BUNDLED_DRAWING_ID
  }
}
