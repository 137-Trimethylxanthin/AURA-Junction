import { XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import type { SpeechState } from './useSpeech'

export function VoiceCard({
  state,
  text,
  error,
  langName,
  onUse,
  onStop,
  onDiscard,
}: {
  state: SpeechState
  text: string
  error: string | null
  langName: string
  onUse(): void
  onStop(): void
  onDiscard(): void
}) {
  const { tu } = useUi()
  const listening = state === 'listening'
  return (
    <section aria-label={tu('vc_region')} className="flex flex-col gap-2 rounded-xl border bg-card p-3">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={cn('size-2.5 rounded-full', listening ? 'animate-pulse bg-destructive' : 'bg-muted-foreground/40')}
        />
        <strong className="flex-1 text-sm font-medium">
          {listening ? tu('vc_listening', { lang: langName }) : error ? tu('vc_stopped') : tu('vc_heard')}
        </strong>
        <Button variant="ghost" size="icon" aria-label={tu('vc_discard')} onClick={onDiscard}>
          <XIcon />
        </Button>
      </div>
      <p className="min-h-5 text-sm text-foreground" aria-live="polite">
        {error ? <span className="text-destructive">{error}</span> : text ? `“${text}”` : <span className="text-muted-foreground">{tu('vc_prompt')}</span>}
      </p>
      <div className="flex gap-2">
        <Button className="flex-1" disabled={!text} onClick={onUse}>
          {tu('vc_use')}
        </Button>
        {listening ? (
          <Button variant="outline" onClick={onStop}>
            {tu('vc_stop')}
          </Button>
        ) : null}
      </div>
    </section>
  )
}
