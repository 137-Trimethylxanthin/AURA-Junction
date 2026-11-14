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
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { uiLang, useUi } from '@/i18n/uiLang'

/** Each language is named in itself, so someone who cannot read the current one still finds theirs. */
const NAMES: Record<Lang, string> = { en: 'English', fr: 'Français', fi: 'Suomi', de: 'Deutsch' }

/** Interface language (menus and labels only; answers always follow the question's language). */
export function LanguageMenu() {
  const { lang, tu } = useUi()
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
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
