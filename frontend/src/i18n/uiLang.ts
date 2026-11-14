import { useCallback, useSyncExternalStore } from 'react'
import type { Lang } from '@/api/contract'
import { ui, type UiKey } from './strings'

/**
 * The interface language (menus, buttons, labels). Default English, chosen in the header menu, kept in this
 * browser. It never changes answers: an answer always follows its question's language.
 */
const KEY = 'aura.ui-lang'
const LANGS: readonly Lang[] = ['en', 'fr', 'fi', 'de']
const listeners = new Set<() => void>()

function read(): Lang {
  try {
    const v = globalThis.localStorage?.getItem(KEY)
    return v && (LANGS as readonly string[]).includes(v) ? (v as Lang) : 'en'
  } catch {
    return 'en'
  }
}

let current: Lang = read()

/** Non-hook access, for toasts and store code. */
export const uiLang = {
  get: (): Lang => current,
  set(lang: Lang) {
    current = lang
    try {
      globalThis.localStorage?.setItem(KEY, lang)
    } catch {
      // private mode: the choice lasts for this page only
    }
    if (typeof document !== 'undefined') document.documentElement.lang = lang
    listeners.forEach((fn) => fn())
  },
  subscribe(fn: () => void) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
}

if (typeof document !== 'undefined') document.documentElement.lang = current

/** Text in the current interface language. */
export const tu = (key: UiKey, vars?: Record<string, string | number>) => ui(uiLang.get(), key, vars)

/** Hook: re-renders when the interface language changes; returns the translator. */
export function useUi() {
  const lang = useSyncExternalStore(uiLang.subscribe, uiLang.get, () => 'en' as Lang)
  const tr = useCallback((key: UiKey, vars?: Record<string, string | number>) => ui(lang, key, vars), [lang])
  return { lang, tu: tr }
}
