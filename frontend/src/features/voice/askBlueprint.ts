import type { AskResponse } from '@/api/contract'
import { actions, appStore, type AgentTurn } from '@/store'

/**
 * The live co-pilot's one tool. It asks through AURA's own chat pipeline (`actions().ask`, the same path as
 * typing the question), so the turn lands in the thread with its evidence on the plan, and returns only the
 * verified fields of the answer. The voice model is told to say no number that is not in this result.
 */

const METHOD_TEXT: Record<AskResponse['method'], string> = {
  cad_geometry: 'exact, from the CAD geometry',
  ocr: 'read from the printed text on the drawing',
  detector: 'detected on the drawing image',
  vision: 'a visual reading of the drawing',
  agent_estimate: 'an agent visual estimate, not an exact measurement',
  none: 'no method (no answer)',
}

export function answerFacts(r: AskResponse): Record<string, unknown> {
  const a = r.answer
  const facts: Record<string, unknown> = { verified: true, method: METHOD_TEXT[r.method] ?? r.method }
  switch (a.kind) {
    case 'count':
      facts.answer = { count: a.value, of: a.subject }
      break
    case 'area':
      facts.answer = { area: a.value, unit: 'square metres' }
      break
    case 'quantity':
      facts.answer = { value: a.value, unit: a.unit }
      break
    case 'text':
      facts.answer = { text: a.value, kind: a.subject }
      break
    case 'note':
      facts.answer = { note: a.value, ...(a.number !== undefined ? { number: a.number, unit: a.unit } : {}) }
      break
    case 'abstain':
      facts.answer = null
      facts.no_answer = a.reason
      if (a.where_to_find) facts.where_to_find = a.where_to_find
      break
  }
  const groups = r.evidence?.groups ?? []
  if (groups.length && a.kind === 'count') {
    const n = new Map<string, number>()
    for (const b of r.evidence.boxes ?? []) if (b.group_id && !b.duplicate_of) n.set(b.group_id, (n.get(b.group_id) ?? 0) + 1)
    const rows = groups
      .map((g) => ({ group: g.label, count: n.get(g.id) ?? 0 }))
      .filter((g) => g.count > 0)
      .sort((x, y) => y.count - x.count)
      .slice(0, 3)
    // Only a breakdown that adds up to the answer is passed on: these tallies are not solver output.
    if (rows.length && rows.reduce((sum, g) => sum + g.count, 0) === a.value) facts.breakdown = rows
  }
  const caveats: string[] = []
  if (r.estimate) {
    const pct = r.estimate.confidence * 100 // as the answer card shows it
    caveats.push(`confidence ${r.method === 'agent_estimate' ? Math.round(pct) : Math.floor(pct)} percent`)
  }
  if (r.check?.status === 'live' && r.check.total) caveats.push(`independent check agreed on ${r.check.agreed ?? 0} of ${r.check.total}`)
  if (caveats.length) facts.caveats = caveats
  return facts
}

const TIMEOUT_MS = 120_000

/** Ask, wait for the agent turn to settle, return its verified facts (or an error the model must relay). */
export async function askBlueprint(question: string): Promise<Record<string, unknown>> {
  const q = question.trim().slice(0, 2000)
  if (!q) return { verified: false, error: 'empty question' }
  const before = new Set(appStore.getState().turns.map((t) => t.id))
  let id: string | undefined
  try {
    id = await actions().ask(q)
  } catch {
    return { verified: false, error: 'AURA could not take the question' }
  }
  const find = (): AgentTurn | undefined => {
    const turns = appStore.getState().turns
    const t = (id ? turns.find((x) => x.id === id) : undefined) ?? turns.find((x) => x.role === 'agent' && !before.has(x.id))
    return t?.role === 'agent' ? t : undefined
  }
  const settled = await new Promise<AgentTurn | undefined>((resolve) => {
    const check = () => {
      const t = find()
      if (t && t.status !== 'pending') {
        unsub()
        clearTimeout(timer)
        resolve(t)
      }
    }
    const unsub = appStore.subscribe(check)
    const timer = setTimeout(() => {
      unsub()
      resolve(find())
    }, TIMEOUT_MS)
    check()
  })
  if (!settled || settled.status === 'pending') return { verified: false, error: 'AURA is still working; the answer will appear in the chat' }
  if (settled.status === 'error' || !settled.response)
    return { verified: false, error: settled.error?.message ?? 'AURA could not answer' }
  return answerFacts(settled.response)
}
