import { CrosshairIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { langDetected, t } from '@/i18n/strings'
import { actions, type AgentTurn, type UserTurn } from '@/store'
import { scopeChipText } from './view'

export function UserBubble({ turn, reply }: { turn: UserTurn; reply?: AgentTurn }) {
  const resp = reply?.response
  const lang = resp?.detected_lang ?? turn.lang
  const foreign = resp && resp.detected_lang !== 'en'
  return (
    <div className="flex max-w-[88%] min-w-0 flex-col items-end gap-1.5 self-end" data-user-turn={turn.id} lang={turn.lang}>
      <p className="max-w-full min-w-0 rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm whitespace-pre-wrap text-primary-foreground [overflow-wrap:anywhere]">
        {turn.text}
      </p>
      {turn.scope ? (
        <span
          className="inline-flex max-w-full items-center gap-1.5 rounded-xl bg-user-surface px-2.5 py-0.5 text-xs font-medium text-user"
          title={scopeChipText(turn.scope, lang)}
        >
          <CrosshairIcon className="size-3 shrink-0" aria-hidden />
          <span className="line-clamp-2 [overflow-wrap:anywhere]">{scopeChipText(turn.scope, lang)}</span>
        </span>
      ) : null}
      {foreign && reply ? (
        <div className="flex max-w-full min-w-0 flex-col items-end gap-1" lang={lang}>
          <div className="flex flex-wrap items-center justify-end gap-1 text-xs text-muted-foreground">
            <span>{langDetected(lang, resp.detected_lang)}</span>
            <Button
              variant="outline"
              className="px-2.5 text-xs"
              aria-expanded={reply.showCanonical}
              onClick={() => actions().toggleCanonical(reply.id)}
            >
              {reply.showCanonical ? t(lang, 'hide') : t(lang, 'show_in_en')}
            </Button>
          </div>
          {reply.showCanonical ? (
            <p className="max-w-full min-w-0 rounded-lg bg-muted px-2.5 py-1.5 text-xs text-foreground [overflow-wrap:anywhere]">
              {t(lang, 'understood_as')}: <span lang="en">“{resp.canonical_en}”</span>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
