import { LoaderCircleIcon, MicOffIcon, XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import type { DictationState } from './useDictation'
import { VoiceLevel } from './VoiceLevel'

export function VoiceCard({
  state,
  text,
  error,
  langName,
  onUse,
  onStop,
  onDiscard,
  onRetry,
}: {
  state: DictationState
  text: string
  error: string | null
  langName: string
  onUse(): void
  onStop(): void
  onDiscard(): void
  /** Optional: record again after an error. */
  onRetry?(): void
}) {
  const { tu } = useUi()
  const listening = state === 'listening'
  const transcribing = state === 'transcribing'
  const title = listening
    ? tu('vc_listening', { lang: langName })
    : transcribing
      ? tu('vo_transcribing')
      : error
        ? tu('vc_stopped')
        : tu('vc_heard')
  return (
    <section
      aria-label={tu('vc_region')}
      aria-busy={transcribing}
      data-voice-card={state}
      className={cn(
        'flex flex-col gap-2 rounded-xl border bg-card p-3 transition-colors animate-in fade-in-0 duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] motion-safe:slide-in-from-bottom-1',
        listening && 'border-destructive/40',
        error && 'border-destructive/40',
      )}
    >
      <div className="flex items-center gap-2">
        {listening ? (
          <VoiceLevel active className="text-destructive" />
        ) : transcribing ? (
          <LoaderCircleIcon aria-hidden className="size-4 animate-spin text-muted-foreground" />
        ) : error ? (
          <MicOffIcon aria-hidden className="size-4 text-destructive" />
        ) : (
          <span aria-hidden className="size-2.5 rounded-full bg-success" />
        )}
        <strong className="flex-1 text-sm font-medium" role="status" aria-live="polite">
          {title}
        </strong>
        <Button variant="ghost" size="icon" aria-label={tu('vc_discard')} title={tu('vc_discard')} onClick={onDiscard}>
          <XIcon />
        </Button>
      </div>
      <p className={cn('min-h-5 text-sm text-foreground', (listening || transcribing) && text && 'text-muted-foreground')} aria-live="polite">
        {error ? (
          <span className="text-destructive">{error}</span>
        ) : text ? (
          `“${text}”`
        ) : (
          <span className="text-muted-foreground">{transcribing ? tu('vo_transcribing_hint') : tu('vc_prompt')}</span>
        )}
      </p>
      <div className="flex gap-2">
        {listening ? (
          <Button className="flex-1" onClick={onStop}>
            {tu('vo_stop_transcribe')}
          </Button>
        ) : error && onRetry ? (
          <Button className="flex-1" variant="outline" onClick={onRetry}>
            {tu('vo_retry')}
          </Button>
        ) : (
          <Button className="flex-1" disabled={!text || transcribing} onClick={onUse}>
            {tu('vc_use')}
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{tu('vo_dictation_hint')}</p>
    </section>
  )
}
