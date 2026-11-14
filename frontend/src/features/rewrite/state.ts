import { spentOf } from '@/api/client'
import { create } from 'zustand'
import { apiBase } from '@/api/library'
import { postRewrite } from '@/api/rewrite'
import type { Lang, Scope } from '@/api/contract'
import { ApiError, TransportError } from '@/api/client'
import { detect } from '@/lib/detect'
import { actions, appStore } from '@/store'
import { splitLocally } from './heuristic'

export interface RewriteBlockState {
  key: string
  question: string
  intent?: 'count' | 'area' | 'other'
  subject?: string
  /** Sent with the question when present (the model's placement); local blocks leave it to the router. */
  scope?: Scope
  routable: boolean
  asked: boolean
}

interface RewriteState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  original: string
  via: 'model' | 'local'
  lang: Lang
  blocks: RewriteBlockState[]
  notAnswerable: string[]
  cost: { calls: number; cost_usd: number; tokens: number; model: string } | null
  /** Why the model could not be used (the local split is shown instead, or nothing). */
  note: string | null
  start(text: string): Promise<void>
  edit(key: string, question: string): void
  remove(key: string): void
  dismiss(): void
  askOne(key: string): Promise<void>
  askAll(): Promise<void>
}

let seq = 0
const key = () => `rw-${++seq}`

/** True when this session talks to the API (the model rewrite is available). */
export const canRewriteWithModel = () => appStore.getState().session.source.kind === 'api' && !!apiBase()

const idle = {
  status: 'idle' as const,
  original: '',
  via: 'local' as const,
  blocks: [],
  notAnswerable: [],
  cost: null,
  note: null,
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === 'budget_exhausted') return 'The model budget is used up right now.'
    if (e.code === 'rate_limited') return 'Too many model requests from here; try again in a while.'
    if (e.code === 'timeout') return 'The rewrite took too long.'
    return e.message
  }
  if (e instanceof TransportError) return 'The server could not be reached.'
  return 'The rewrite failed.'
}

const localBlocks = (text: string): RewriteBlockState[] =>
  splitLocally(text).map((q) => ({ key: key(), question: q, routable: true, asked: false }))

export const useRewrite = create<RewriteState>()((set, get) => ({
  ...idle,
  lang: 'en',

  async start(text) {
    const original = text.trim()
    if (!original) return
    const lang = detect(original)
    if (!canRewriteWithModel()) {
      const blocks = localBlocks(original)
      set({ ...idle, original, lang, via: 'local', blocks, status: blocks.length ? 'ready' : 'idle' })
      return
    }
    set({ ...idle, original, lang, via: 'model', status: 'loading' })
    const drawingId = appStore.getState().session.drawing.id
    try {
      const r = await postRewrite({ baseUrl: apiBase()! }, { drawing_id: drawingId, text: original.slice(0, 2000) })
      if (get().original !== original) return
      actions().addSpend(r.calls, r.cost_usd)
      set({
        status: 'ready',
        lang: r.detected_lang,
        blocks: r.blocks.map((b) => ({
          key: key(),
          question: b.question,
          intent: b.intent,
          ...(b.subject ? { subject: b.subject } : {}),
          scope: b.scope,
          routable: b.routable && b.scope_exists,
          asked: false,
        })),
        notAnswerable: r.not_answerable,
        cost: { calls: r.calls, cost_usd: r.cost_usd, tokens: r.tokens, model: r.model },
      })
    } catch (e) {
      const spent = spentOf(e)
      if (spent.calls || spent.cost_usd) actions().addSpend(spent.calls, spent.cost_usd)
      if (get().original !== original) return
      const blocks = localBlocks(original)
      set({ status: blocks.length ? 'ready' : 'error', via: 'local', blocks, note: errorText(e) })
    }
  },

  edit(k, question) {
    set((s) => ({ blocks: s.blocks.map((b) => (b.key === k ? { ...b, question } : b)) }))
  },
  remove(k) {
    const blocks = get().blocks.filter((b) => b.key !== k)
    if (!blocks.length) get().dismiss()
    else set({ blocks })
  },
  dismiss() {
    set({ ...idle })
  },

  async askOne(k) {
    const b = get().blocks.find((x) => x.key === k)
    if (!b || !b.routable || !b.question.trim()) return
    set((s) => ({ blocks: s.blocks.map((x) => (x.key === k ? { ...x, asked: true } : x)) }))
    // Always with the block's scope: the text alone can route differently.
    await actions().ask(b.question.trim(), b.scope ? { scope: b.scope } : {})
    if (get().blocks.every((x) => x.asked || !x.routable)) get().dismiss()
  },
  async askAll() {
    for (const b of get().blocks.filter((x) => x.routable && !x.asked)) await get().askOne(b.key)
  },
}))
