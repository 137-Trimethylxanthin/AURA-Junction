import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowUpIcon, CrosshairIcon, MicIcon, XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { LOCALE } from '@/lib/format'
import { cn } from '@/lib/utils'
import { actions, selectAttachedMark, selectIsPending, selectSessionLang, useAppStore } from '@/store'
import { speechSupported, useSpeech } from '@/features/voice/useSpeech'
import { VoiceCard } from '@/features/voice/VoiceCard'
import { PROMPT_BLOCKS, fillBlock, useDraft } from './draft'
import { ScrollRow } from './ScrollRow'
import { SuggestionList } from './Suggestions'
import { LANG_NAME_EN } from './view'

const MAX_LINES = 4
const LINE_PX = 20

function isTyping(el: Element | null) {
  return el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
}

export function Composer() {
  const text = useDraft((s) => s.text)
  const focusTick = useDraft((s) => s.focusTick)
  const pending = useAppStore(selectIsPending)
  const mark = useAppStore(selectAttachedMark)
  const hasTurns = useAppStore((s) => s.turns.length > 0)
  const lang = useAppStore(selectSessionLang)
  const ta = useRef<HTMLTextAreaElement>(null)
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [canSpeak] = useState(speechSupported)
  const speech = useSpeech(LOCALE[lang])

  // Auto-grow up to MAX_LINES.
  useLayoutEffect(() => {
    const el = ta.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_LINES * LINE_PX + 16)}px`
  }, [text])

  // Someone asked for focus (a prompt block, voice, "/").
  useEffect(() => {
    if (!focusTick) return
    const el = ta.current
    if (!el) return
    el.focus()
    const sel = useDraft.getState().select
    if (sel) el.setSelectionRange(sel[0], sel[1])
    else el.setSelectionRange(el.value.length, el.value.length)
  }, [focusTick])

  // "/" focuses the composer from anywhere outside a text field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || isTyping(document.activeElement)) return
      e.preventDefault()
      ta.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const send = () => {
    const q = text.trim()
    if (!q || pending) return
    useDraft.getState().setText('')
    void actions().ask(q)
    ta.current?.focus()
  }

  const openVoice = () => {
    setVoiceOpen(true)
    speech.start()
  }
  const closeVoice = () => {
    speech.reset()
    setVoiceOpen(false)
  }

  return (
    <div className="flex flex-col gap-2">
      {voiceOpen ? (
        <VoiceCard
          state={speech.state}
          text={speech.text}
          error={speech.error}
          langName={LANG_NAME_EN[lang]}
          onStop={speech.stop}
          onDiscard={closeVoice}
          onUse={() => {
            useDraft.getState().fill(speech.text)
            closeVoice()
          }}
        />
      ) : null}

      {mark ? (
        <div className="flex">
          <span className="inline-flex items-center gap-1.5 rounded-lg bg-user-surface py-0 pr-0 pl-2.5 text-sm font-medium text-user">
            <CrosshairIcon className="size-3.5" aria-hidden />
            {mark.label} attached
            <Button
              variant="ghost"
              size="icon"
              className="text-user hover:bg-user/10 hover:text-user"
              aria-label={`Remove ${mark.label} from the question`}
              onClick={() => actions().attachMark(null)}
            >
              <XIcon />
            </Button>
          </span>
        </div>
      ) : null}

      <div
        className={cn(
          'flex items-end gap-1.5 rounded-xl border border-input/60 bg-background p-1.5 transition-shadow',
          'focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30',
        )}
      >
        <textarea
          ref={ta}
          rows={1}
          value={text}
          onChange={(e) => useDraft.getState().setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
          placeholder="Ask in any language…"
          aria-label="Ask about this drawing"
          aria-keyshortcuts="/"
          className="max-h-24 min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] leading-5 outline-none placeholder:text-muted-foreground focus-visible:outline-none"
        />
        {canSpeak ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Ask by voice"
            aria-pressed={voiceOpen}
            title="Ask by voice"
            onClick={voiceOpen ? closeVoice : openVoice}
            className={cn(voiceOpen && 'bg-destructive-surface text-destructive')}
          >
            <MicIcon />
          </Button>
        ) : null}
        <Button size="icon" aria-label="Send" title="Send (Enter)" disabled={!text.trim() || pending} onClick={send}>
          <ArrowUpIcon />
        </Button>
      </div>

      <ScrollRow label="Prompt blocks and suggested questions">
        <ul aria-label="Prompt blocks" className="flex shrink-0 gap-1.5">
          {PROMPT_BLOCKS.map((b) => (
            <li key={b.label}>
              <button
                type="button"
                title={`Fill in: ${b.text}`}
                onClick={() => fillBlock(b)}
                className="inline-flex min-h-10 items-center rounded-lg bg-muted px-3 text-xs font-semibold text-foreground transition-colors outline-none hover:bg-accent-foreground/10 focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {b.label}
              </button>
            </li>
          ))}
        </ul>
        {hasTurns ? (
          <>
            <span aria-hidden className="h-6 w-px shrink-0 bg-border" />
            <SuggestionList row />
          </>
        ) : null}
      </ScrollRow>
    </div>
  )
}
