import { uiLang } from '@/i18n/uiLang'
import { NO_LABEL, roomDisplayName } from '@/lib/subtitle'
import { useMemo } from 'react'
import { useStore } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import { deriveAnswer, type Derived } from '@/lib/answer'
import { createAppStore, type AgentTurn, type AppState, type Turn } from './appStore'

export * from './appStore'

/** The app-wide store. Tests build their own with createAppStore({ source }). */
export const appStore = createAppStore()

export function useAppStore<T>(selector: (s: AppState) => T): T {
  return useStore(appStore, selector)
}

/** For selectors that build a new array/object: compares shallowly to avoid re-render loops. */
export function useAppStoreShallow<T>(selector: (s: AppState) => T): T {
  return useStore(appStore, useShallow(selector))
}

// ---- selectors (pure; usable with any store) --------------------------------
export const selectTurn =
  (id: string | null | undefined) =>
  (s: AppState): Turn | undefined =>
    id ? s.turns.find((x) => x.id === id) : undefined

export const selectAgentTurn =
  (id: string | null | undefined) =>
  (s: AppState): AgentTurn | undefined => {
    const x = selectTurn(id)(s)
    return x && x.role === 'agent' ? x : undefined
  }

export const selectActiveTurn = (s: AppState): AgentTurn | undefined => selectAgentTurn(s.ui.activeTurnId)(s)
export const selectSource = (s: AppState) => s.session.source
export const selectAnswererMode = (s: AppState) => s.session.answererMode
export const selectActiveProject = (s: AppState) => s.activeProject
export const selectCrossPlanScope = (s: AppState) => s.crossPlanScope
export const selectFocusedGroupId = (s: AppState) => s.ui.focusedGroupId
export const selectDrawingById = (s: AppState) => s.drawingById
const localized = new WeakMap<object, Map<string, AppState['session']['drawing']>>()
/**
 * The open drawing as it is shown: a room the sheet prints no label for carries the store's English
 * "Room without label", shown in the interface language (plan, 3D, lists). Same object while nothing changes.
 */
export const selectDrawing = (s: AppState) => {
  const d = s.session.drawing
  const lang = uiLang.get()
  if (lang === 'en' || !d?.rooms?.some((r) => r.name === NO_LABEL)) return d
  let per = localized.get(d)
  if (!per) localized.set(d, (per = new Map()))
  let out = per.get(lang)
  if (!out) {
    out = { ...d, rooms: d.rooms.map((r) => (r.name === NO_LABEL ? { ...r, name: roomDisplayName(r.name, lang) } : r)) }
    per.set(lang, out)
  }
  return out
}
export const selectSessionLang = (s: AppState) => s.session.lang
export const selectCanUndo = (s: AppState) => s.undoStack.length > 0
export const selectZoom = (s: AppState) => s.ui.zoom
export const selectAttachedMark = (s: AppState) => s.ui.marks.find((m) => m.id === s.ui.attachedMarkId)
export const selectIsPending = (s: AppState) => s.turns.some((x) => x.role === 'agent' && x.status === 'pending')

// ---- hooks -------------------------------------------------------------------
export const useTurn = (id: string | null | undefined) => useAppStore(selectTurn(id))
export const useAgentTurn = (id: string | null | undefined) => useAppStore(selectAgentTurn(id))
export const useActiveTurn = () => useAppStore(selectActiveTurn)
export const useSource = () => useAppStore(selectSource)

/** Displayed value, gold chip and counted/rejected ids for an agent turn (memoised on the turn object). */
export function useDerived(turnId: string | null | undefined): Derived | null {
  const turn = useAgentTurn(turnId)
  const drawing = useAppStore(selectDrawing)
  return useMemo(
    () => (turn?.response ? deriveAnswer(turn.response, turn.toggles, drawing) : null),
    [turn, drawing],
  )
}

/** Stable action bag (actions never change identity). */
export const actions = () => appStore.getState()
