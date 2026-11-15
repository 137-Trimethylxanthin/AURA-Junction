import { LanguagesIcon } from 'lucide-react'
import type { Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { uiLang, useUi } from '@/i18n/uiLang'
import { unitPref, useUnitPref, type UnitSystem } from '@/lib/unitPref'

/** Each language is named in itself, so someone who cannot read the current one still finds theirs. */
const NAMES: Record<Lang, string> = { en: 'English', fr: 'Français', fi: 'Suomi', de: 'Deutsch' }

/** How answers show sizes; the unit a question asks for ("in square feet") always wins. */
const UNITS_LABEL: Record<Lang, string> = { en: 'Units in answers', fr: 'Unités des réponses', fi: 'Vastausten yksiköt', de: 'Einheiten in Antworten' }
const UNIT_NAMES: Record<Lang, Record<UnitSystem, string>> = {
  en: { metric: 'Metric (m, m²)', imperial: 'Imperial (ft, sq ft)' },
  fr: { metric: 'Métrique (m, m²)', imperial: 'Impérial (ft, sq ft)' },
  fi: { metric: 'Metrinen (m, m²)', imperial: 'Brittiläinen (ft, sq ft)' },
  de: { metric: 'Metrisch (m, m²)', imperial: 'Imperial (ft, sq ft)' },
}

/** Interface language (menus and labels only; answers always follow the question's language). */
export function LanguageMenu() {
  const { lang, tu } = useUi()
  const units = useUnitPref()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" aria-label={`${tu('lang_menu')}: ${NAMES[lang]}`} title={tu('lang_menu_hint')} data-lang-menu>
          <LanguagesIcon data-icon="inline-start" />
          <span className="text-xs font-medium uppercase">{lang}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">{tu('lang_menu_hint')}</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={lang} onValueChange={(v) => uiLang.set(v as Lang)}>
            {(Object.keys(NAMES) as Lang[]).map((l) => (
              <DropdownMenuRadioItem key={l} value={l} lang={l} className="min-h-10">
                {NAMES[l]}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup data-units-menu>
          <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">{UNITS_LABEL[lang]}</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={units} onValueChange={(v) => unitPref.set(v as UnitSystem)}>
            {(['metric', 'imperial'] as const).map((u) => (
              <DropdownMenuRadioItem key={u} value={u} className="min-h-10" data-units={u}>
                {UNIT_NAMES[lang][u]}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
