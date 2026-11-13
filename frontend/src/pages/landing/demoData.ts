import { ask } from "@/api/local"
import { bundledDrawing } from "@/data/drawing"
import { doubleLeaves } from "@/features/plan/badges"

/**
 * The real local answer for the sample drawing, computed once by the app's own offline adapter.
 * The hero demo and the "how it works" story both draw from it; nothing here is canned.
 */
export const QUESTION = "How many windows are drawn on this floor plan?"
export const RESPONSE = ask({
  drawing_id: bundledDrawing.id,
  question: QUESTION,
  lang_hint: "en",
})
export const BOXES = RESPONSE.evidence.boxes
export const LEAVES = doubleLeaves(BOXES)
export const GOLD = RESPONSE.gold?.value
