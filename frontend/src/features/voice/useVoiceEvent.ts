import { useEffect, useRef } from 'react'

/** Window CustomEvents dispatched by the keyboard shortcuts (features/shortcuts). */
export const VOICE_EVENTS = {
  dictation: 'aura:toggle-dictation', // Alt+V
  speak: 'aura:toggle-speak', // Alt+S
  live: 'aura:toggle-live-voice', // Alt+Shift+V
} as const

export type VoiceEventName = (typeof VOICE_EVENTS)[keyof typeof VOICE_EVENTS]

/** Calls the latest `handler` on each `name` event while `enabled`. */
export function useVoiceEvent(name: VoiceEventName, handler: () => void, enabled = true) {
  const ref = useRef(handler)
  useEffect(() => {
    ref.current = handler
  })
  useEffect(() => {
    if (!enabled) return
    const fn = () => ref.current()
    window.addEventListener(name, fn)
    return () => window.removeEventListener(name, fn)
  }, [name, enabled])
}
