import { toast } from 'sonner'
import { downloadText, evidenceCsv } from '@/features/plugins/csv'
import { sendStageCommand } from '@/features/plan/shared'
import { tu } from '@/i18n/uiLang'
import { appStore, selectActiveTurn, type AppStore } from '@/store'

/** Window events other features listen for (history, dictation, read-aloud, live voice). */
export const POWER_EVENTS = {
  history: 'aura:open-history',
  dictate: 'aura:toggle-dictation',
  speak: 'aura:toggle-speak',
  live: 'aura:toggle-live-voice',
} as const

export type PowerActionId =
  | `pan-${'left' | 'right' | 'up' | 'down'}`
  | `pan-fast-${'left' | 'right' | 'up' | 'down'}`
  | 'fit'
  | `zoom-${100 | 200 | 300 | 450}`
  | 'peek'
  | 'group-next'
  | 'group-prev'
  | 'dropped'
  | `floor-${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9}`
  | 'all-floors'
  | 'export'
  | 'library'
  | 'plugins'
  | 'new-chat'
  | keyof typeof POWER_EVENTS

export interface PowerContext {
  store?: AppStore
  navigate?: (to: string) => void
}

const PAN = 80
const PAN_FAST = 240
const PAN_VEC = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] } as const

export const ZOOM_PRESETS = [100, 200, 300, 450] as const

const sortedFloors = (s: ReturnType<AppStore['getState']>) =>
  s.activeProject && s.activeProject.floors.length >= 2
    ? [...s.activeProject.floors].sort((a, b) => a.order - b.order || a.drawing_id.localeCompare(b.drawing_id))
    : []

function cycleGroup(store: AppStore, delta: 1 | -1): boolean {
  const s = store.getState()
  const turn = selectActiveTurn(s)
  const ev = turn?.response?.evidence ?? turn?.stream?.evidence
  const ids = ((ev as { groups?: { id: string }[] } | undefined)?.groups ?? []).map((g) => g.id)
  if (ids.length === 0) return false
  const order: (string | null)[] = [...ids, null]
  const cur = s.ui.focusedGroupId
  const idx = cur === null ? order.length - 1 : Math.max(0, order.indexOf(cur))
  s.setFocusedGroup(order[(idx + delta + order.length) % order.length])
  return true
}

/** Executes one power-user action. Returns false when it does not apply right now (the key then falls through). */
export function runPowerAction(id: PowerActionId, ctx: PowerContext = {}): boolean {
  const store = ctx.store ?? appStore
  const s = store.getState()
  if (id.startsWith('pan-')) {
    const fast = id.startsWith('pan-fast-')
    const dir = id.slice(fast ? 9 : 4) as keyof typeof PAN_VEC
    const [x, y] = PAN_VEC[dir]
    const d = fast ? PAN_FAST : PAN
    sendStageCommand({ type: 'pan', dx: x * d, dy: y * d })
    return true
  }
  if (id.startsWith('zoom-')) {
    s.setZoom(Number(id.slice(5)))
    return true
  }
  if (id.startsWith('floor-')) {
    const target = sortedFloors(s)[Number(id.slice(6)) - 1]
    if (!target) return false
    if (target.drawing_id !== s.session.drawing.id) ctx.navigate?.(`/d/${target.drawing_id}`)
    return true
  }
  switch (id) {
    case 'fit':
      sendStageCommand({ type: 'fit' })
      return true
    case 'peek':
      sendStageCommand({ type: 'peek' })
      return true
    case 'dropped':
      sendStageCommand({ type: 'dropped' })
      return true
    case 'group-next':
      return cycleGroup(store, 1)
    case 'group-prev':
      return cycleGroup(store, -1)
    case 'all-floors':
      if (sortedFloors(s).length < 2) return false
      s.setCrossPlanScope(!s.crossPlanScope)
      return true
    case 'export': {
      const turn = selectActiveTurn(s)
      const r = turn?.response
      if (!turn || !r) return false
      if (r.evidence.boxes.length === 0 && r.evidence.rooms.length === 0 && (r.evidence.rows?.length ?? 0) === 0) {
        return false
      }
      downloadText(`${s.session.drawing.id}-evidence.csv`, evidenceCsv(r, turn.toggles, s.session.drawing))
      return true
    }
    case 'library':
      ctx.navigate?.('/app')
      return true
    case 'plugins':
      s.setPluginsOpen(true)
      return true
    case 'new-chat': {
      if (s.turns.length === 0) return false
      const restore = s.newConversation()
      toast(tu('new_chat_cleared'), {
        id: 'new-chat-undo',
        toasterId: 'chat',
        duration: 6000,
        action: { label: tu('undo'), onClick: () => restore() },
      })
      return true
    }
    case 'history':
    case 'dictate':
    case 'speak':
    case 'live':
      window.dispatchEvent(new CustomEvent(POWER_EVENTS[id]))
      return true
  }
  return false
}

export interface PowerRow {
  id: string
  label: string
  keys: readonly string[]
  /** Keys are alternatives (`/`) rather than a chord (`+`). */
  anyKey?: boolean
  /** Palette action; rows without one (Space+Drag, arrows) are help-only. */
  action?: PowerActionId
  group: 'stage' | 'workflow'
}

type Tu = (key: Parameters<typeof tu>[0], vars?: Record<string, string | number>) => string

/** Power-user bindings shown in the `?` cheatsheet and the Mod+K palette. */
export function powerRows(t: Tu, alt: string): PowerRow[] {
  return [
    { id: 'pan-drag', group: 'stage', label: t('sc_pan_drag'), keys: ['Space', 'Drag'] },
    { id: 'pan-arrows', group: 'stage', label: t('sc_pan_arrows'), keys: ['←', '↑', '↓', '→'], anyKey: true },
    { id: 'pan-fast', group: 'stage', label: t('sc_pan_fast'), keys: ['Shift', '←↑↓→'] },
    { id: 'fit', group: 'stage', label: t('sc_zoom_fit'), keys: ['F'], action: 'fit' },
    ...ZOOM_PRESETS.map((n, i): PowerRow => ({
      id: `zoom-${n}`,
      group: 'stage',
      label: t('sc_zoom_to', { n }),
      keys: ['Shift', String(i + 1)],
      action: `zoom-${n}`,
    })),
    { id: 'peek', group: 'stage', label: t('sc_peek'), keys: ['H'], action: 'peek' },
    { id: 'group-next', group: 'stage', label: t('sc_group_next'), keys: ['G'], action: 'group-next' },
    { id: 'group-prev', group: 'stage', label: t('sc_group_prev'), keys: ['Shift', 'G'], action: 'group-prev' },
    { id: 'dropped', group: 'stage', label: t('sc_dropped'), keys: ['D'], action: 'dropped' },
    { id: 'floor-n', group: 'stage', label: t('sc_floor_n', { n: '1–9' }), keys: [alt, '1…9'] },
    { id: 'all-floors', group: 'stage', label: t('match_all_floors'), keys: [alt, 'A'], action: 'all-floors' },
    { id: 'export', group: 'workflow', label: t('cmd_export_csv'), keys: ['E'], action: 'export' },
    { id: 'new-chat', group: 'workflow', label: t('new_chat'), keys: [alt, 'N'], action: 'new-chat' },
    { id: 'library', group: 'workflow', label: t('all_drawings'), keys: [alt, 'L'], action: 'library' },
    { id: 'plugins', group: 'workflow', label: t('plugins'), keys: [alt, 'P'], action: 'plugins' },
    { id: 'history', group: 'workflow', label: t('sc_history'), keys: [alt, 'H'], action: 'history' },
    { id: 'dictate', group: 'workflow', label: t('sc_dictate'), keys: [alt, 'V'], action: 'dictate' },
    { id: 'speak', group: 'workflow', label: t('sc_speak'), keys: [alt, 'S'], action: 'speak' },
    { id: 'live', group: 'workflow', label: t('sc_live_voice'), keys: [alt, 'Shift', 'V'], action: 'live' },
  ]
}
