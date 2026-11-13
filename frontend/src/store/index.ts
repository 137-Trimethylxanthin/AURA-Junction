import { useMemo } from 'react'
import { useStore } from 'zustand'
import { useShallow } from 'zustand/react/shallow'
import { deriveAnswer, type Derived } from '@/lib/answer'
import { ZOOM_STEPS, createAppStore, type AgentTurn, type AppState, type Turn } from './appStore'

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
export const selectDrawing = (s: AppState) => s.session.drawing
export const selectSessionLang = (s: AppState) => s.session.lang
export const selectCanUndo = (s: AppState) => s.undoStack.length > 0
export const selectZoom = (s: AppState) => ZOOM_STEPS[s.ui.zoomIndex] ?? 100
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
