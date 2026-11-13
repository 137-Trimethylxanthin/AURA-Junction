import raw from './drawing.swiss-46639.json'
import { isDrawingFile, type DrawingFile } from '@/api/contract'

export const BUNDLED_DRAWING_ID = 'swiss-46639'

if (!isDrawingFile(raw)) throw new Error('drawing.swiss-46639.json does not match the contract')

/** The bundled drawing (narrowed through the contract guard; JSON imports widen tuple types). */
export const bundledDrawing: DrawingFile = raw as unknown as DrawingFile
