import { useStore } from 'zustand'
import { apiBase } from '@/api/library'
import { appStore } from '@/store'
import { createSessionStore, type SessionHistoryState } from './sessionStore'

export * from './sessionStore'

/**
 * The app-wide session history. Importing this module starts the live sync (autosave of the thread on screen,
 * the `aura:open-history` listener, the backend pull), so import it eagerly, not behind a lazy() boundary.
 */
export const sessionHistory = createSessionStore({ app: appStore, remoteBase: apiBase() })
sessionHistory.startSync()

/** `const { open, setOpen } = useSessionHistory()`; pass a selector to read other fields. */
export function useSessionHistory(): SessionHistoryState & { setOpen: (open: boolean) => void }
export function useSessionHistory<T>(selector: (s: SessionHistoryState) => T): T
export function useSessionHistory<T>(selector?: (s: SessionHistoryState) => T) {
  const all = useStore(sessionHistory.store, selector ?? ((s: SessionHistoryState) => s as unknown as T))
  return selector ? all : { ...(all as SessionHistoryState), setOpen: sessionHistory.setOpen }
}
