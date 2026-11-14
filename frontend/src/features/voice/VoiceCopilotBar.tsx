import { AudioLinesIcon, LoaderCircleIcon, MicOffIcon, PhoneOffIcon, SearchIcon } from 'lucide-react'
import type { Lang } from '@/api/contract'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import { askBlueprint } from './askBlueprint'
import { stopSpeaking } from './useTextToSpeech'
import { useLiveVoiceSession, type LiveStatus } from './useLiveVoiceSession'
import { VOICE_EVENTS, useVoiceEvent } from './useVoiceEvent'
import { VoiceLevel } from './VoiceLevel'
import { voiceApiAvailable } from './voiceApi'

/**
 * Live voice co-pilot: a start button that grows into a status bar with the running transcript.
 * Alt+Shift+V (`aura:toggle-live-voice`) starts and ends it. Numbers it speaks come from `ask_blueprint`,
 * which asks through the chat (so each spoken answer also appears in the thread with its evidence).
 */
export function VoiceCopilotBar({
  onAsk = askBlueprint,
  className,
}: {
  onAsk?: (question: string) => Promise<Record<string, unknown>>
  className?: string
}) {
  const { lang, tu } = useUi()
  const live = useLiveVoiceSession({ lang: lang as Lang, onAsk })
  const enabled = voiceApiAvailable()
  const toggle = () => {
    if (!live.active) stopSpeaking() // the readout and the co-pilot never talk over each other
    live.toggle()
  }
  useVoiceEvent(VOICE_EVENTS.live, toggle, enabled)
  if (!enabled) return null

  const statusText: Record<LiveStatus, string> = {
    idle: tu('vo_live_idle'),
    connecting: tu('vo_live_connecting'),
    listening: tu('vo_live_listening'),
    thinking: tu('vo_live_thinking'),
    speaking: tu('vo_live_speaking'),
    error: live.error ?? tu('vo_err_live_conn'),
  }

  if (!live.active && live.status !== 'error') {
    return (
      <Button
        type="button"
        variant="outline"
        className={cn('h-10 min-w-10 gap-2', className)}
        aria-keyshortcuts="Alt+Shift+V"
        title={`${tu('vo_live_start')} (Alt+Shift+V)`}
        onClick={toggle}
        data-copilot="idle"
      >
        <AudioLinesIcon />
        <span>{tu('vo_live_start')}</span>
      </Button>
    )
  }

  const last = live.lines.slice(-3)
  return (
    <section
      aria-label={tu('vo_live_region')}
      data-copilot={live.status}
      className={cn(
        'flex flex-col gap-2 rounded-xl border bg-card p-2 pl-3 shadow-sm animate-in fade-in-0 duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] motion-safe:slide-in-from-bottom-1',
        live.status === 'error' && 'border-destructive/40',
        className,
      )}
    >
      <div className="flex items-center gap-2">
        {live.status === 'connecting' || live.status === 'thinking' ? (
          <LoaderCircleIcon aria-hidden className="size-4 animate-spin text-muted-foreground" />
        ) : live.status === 'error' ? (
          <MicOffIcon aria-hidden className="size-4 text-destructive" />
        ) : (
          <VoiceLevel active className={live.status === 'speaking' ? 'text-primary' : 'text-destructive'} />
        )}
        <strong
          className={cn('flex-1 text-sm font-medium', live.status === 'error' && 'text-destructive')}
          role="status"
          aria-live="polite"
        >
          {statusText[live.status]}
        </strong>
        {live.status === 'error' ? (
          <Button type="button" variant="outline" className="h-10" onClick={toggle}>
            {tu('vo_retry')}
          </Button>
        ) : null}
        <Button
          type="button"
          variant={live.status === 'error' ? 'ghost' : 'destructive'}
          size="icon"
          aria-label={tu('vo_live_end')}
          aria-keyshortcuts="Alt+Shift+V"
          title={`${tu('vo_live_end')} (Alt+Shift+V)`}
          onClick={live.stop}
        >
          <PhoneOffIcon />
        </Button>
      </div>
      {last.length ? (
        <ul className="flex flex-col gap-1 text-sm" aria-label={tu('vo_live_transcript')}>
          {last.map((l, i) => (
            <li key={i} className={cn('flex gap-1.5 animate-in fade-in-0 duration-150', l.role === 'user' ? 'text-muted-foreground' : 'text-foreground')}>
              {l.role === 'tool' ? (
                <>
                  <SearchIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-primary" />
                  <span className="text-xs">{tu('vo_live_asking', { q: l.text })}</span>
                </>
              ) : (
                <span>
                  <span className="sr-only">{l.role === 'user' ? tu('vo_live_you') : 'AURA'}: </span>
                  {l.text}
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {live.status !== 'error' ? <p className="text-xs text-muted-foreground">{tu('vo_live_grounded')}</p> : null}
    </section>
  )
}
