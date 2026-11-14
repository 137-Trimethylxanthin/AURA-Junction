import { LoaderCircleIcon, SquareIcon, Volume2Icon, VolumeXIcon } from 'lucide-react'
import type { AskResponse, Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import { useTextToSpeech } from './useTextToSpeech'
import { VOICE_EVENTS, useVoiceEvent } from './useVoiceEvent'
import { VoiceLevel } from './VoiceLevel'
import { voiceApiAvailable } from './voiceApi'

/**
 * Listen / Stop for one answer (40x40). Reads out only the answer's verified fields, in the interface
 * language. `active` marks the card that answers Alt+S (`aura:toggle-speak`); pass it to one card only.
 */
export function ListenButton({
  turnId,
  response,
  active = false,
  edited = false,
  voice,
  className,
}: {
  turnId: string
  response: AskResponse | undefined
  active?: boolean
  /** The user rejected boxes or rooms, so the card shows a recomputed value; the read-out would not match it. */
  edited?: boolean
  voice?: string
  className?: string
}) {
  const { lang, tu } = useUi()
  const tts = useTextToSpeech(turnId, response, lang as Lang, voice)
  const enabled = Boolean(response) && voiceApiAvailable()
  useVoiceEvent(VOICE_EVENTS.speak, tts.toggle, enabled && active && !edited)
  if (!enabled) return null
  const label = edited
    ? tu('vo_listen_edited')
    : tts.status === 'playing' ? tu('vo_listen_stop') : tts.status === 'loading' ? tu('vo_listen_loading') : tu('vo_listen')
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={label}
        aria-pressed={tts.status === 'playing'}
        aria-keyshortcuts={active ? 'Alt+S' : undefined}
        title={active ? `${label} (Alt+S)` : label}
        data-listen={tts.status}
        disabled={edited && tts.status !== 'playing'}
        onClick={tts.toggle}
        className={cn(tts.status === 'playing' && 'bg-accent text-accent-foreground', tts.status === 'error' && 'text-destructive')}
      >
        {tts.status === 'loading' ? (
          <LoaderCircleIcon className="animate-spin" />
        ) : tts.status === 'playing' ? (
          <SquareIcon className="fill-current" />
        ) : tts.status === 'error' ? (
          <VolumeXIcon />
        ) : (
          <Volume2Icon />
        )}
      </Button>
      {tts.status === 'playing' ? <VoiceLevel active className="text-primary" /> : null}
      <span className="sr-only" role="status" aria-live="polite">
        {tts.status === 'error' ? tts.error : tts.status === 'playing' ? tts.script : ''}
      </span>
      {tts.status === 'error' && tts.error ? <span className="text-xs text-destructive">{tts.error}</span> : null}
    </span>
  )
}
