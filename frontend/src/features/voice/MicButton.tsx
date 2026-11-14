import { LoaderCircleIcon, MicIcon, SquareIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useUi } from '@/i18n/uiLang'
import type { DictationState } from './useDictation'
import { VOICE_EVENTS, useVoiceEvent } from './useVoiceEvent'

/**
 * The composer's microphone toggle (40x40). Also answers Alt+V (`aura:toggle-dictation`).
 * `onToggle` decides what a press means for the current state (open, stop and transcribe, close).
 */
export function MicButton({
  open,
  state,
  onToggle,
  disabled,
}: {
  open: boolean
  state: DictationState
  onToggle(): void
  disabled?: boolean
}) {
  const { tu } = useUi()
  useVoiceEvent(VOICE_EVENTS.dictation, onToggle, !disabled)
  const listening = open && state === 'listening'
  const busy = open && state === 'transcribing'
  const label = listening ? tu('vo_mic_stop') : tu('ask_voice')
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={label}
      aria-pressed={open}
      aria-keyshortcuts="Alt+V"
      title={`${label} (Alt+V)`}
      disabled={disabled || busy}
      data-mic={listening ? 'listening' : busy ? 'transcribing' : open ? 'open' : 'idle'}
      onClick={onToggle}
      className={cn('relative', open && 'bg-destructive-surface text-destructive')}
    >
      {busy ? (
        <LoaderCircleIcon className="animate-spin" />
      ) : listening ? (
        <SquareIcon className="fill-current" />
      ) : (
        <MicIcon />
      )}
      {listening ? (
        <span
          aria-hidden
          className="absolute top-1.5 right-1.5 size-2 rounded-full bg-destructive motion-safe:animate-pulse"
        />
      ) : null}
    </Button>
  )
}
