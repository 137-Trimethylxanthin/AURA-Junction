import { useUi } from "@/i18n/uiLang"
import {
  Suspense,
  lazy,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { ArrowUpIcon, CrosshairIcon, LayersIcon, SparklesIcon, TerminalIcon, XIcon } from "lucide-react"
import { toast } from "sonner"
import type { Scope } from "@/api/contract"
import { Button } from "@/components/ui/button"
import { fmtCount } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  actions,
  appStore,
  selectActiveProject,
  selectActiveTurn,
  selectAnswererMode,
  selectAttachedMark,
  selectCrossPlanScope,
  selectDrawing,
  selectIsPending,
  selectSessionLang,
  useAppStore,
} from "@/store"
import { downloadText, evidenceCsv } from "@/features/plugins/csv"
import { dictationSupported, useDictation } from "@/features/voice/useDictation"
import { MicButton } from "@/features/voice/MicButton"
import { RewriteCard } from "@/features/rewrite/RewriteCard"
import { needsTidy, splitLocally } from "@/features/rewrite/heuristic"
import { canRewriteWithModel, useRewrite } from "@/features/rewrite/state"
import { t } from "@/i18n/strings"
import { promptBlocksFor, fillBlock, useDraft } from "./draft"
import { markLabel } from "@/features/plan/shared"
import type { UiKey } from "@/i18n/strings"
import { sessionHistory } from "@/features/history"

const PB_LABEL: Record<string, UiKey> = { Count: "pb_count", Area: "pb_area", "Where is": "pb_where", Check: "pb_check", List: "pb_list" }
import { ScrollRow } from "./ScrollRow"
import { SuggestionList } from "./Suggestions"
import {
  LANG_NAME_EN,
  filterSlashCommands,
  resolveSlashCommand,
  type ParsedSlashAction,
  type SlashCommandSpec,
} from "./view"

const CHAT_TOASTER = "chat"
const NEW_CHAT_UNDO_TOAST = "new-chat-undo"

/** Loaded the first time the microphone is used. */
const VoiceCard = lazy(() =>
  import("@/features/voice/VoiceCard").then((m) => ({ default: m.VoiceCard }))
)

const MAX_LINES = 4
const LINE_PX = 20

/** The server's question limit (AskRequest.question max_length); the textarea stops there. */
const MAX_QUESTION = 2000

export function Composer() {
  const text = useDraft((s) => s.text)
  const focusTick = useDraft((s) => s.focusTick)
  const pending = useAppStore(selectIsPending)
  const drawing = useAppStore(selectDrawing)
  const activeProject = useAppStore(selectActiveProject)
  const crossPlanScope = useAppStore(selectCrossPlanScope)
  const hintId = useId()
  const slashMenuId = useId()
  const { lang: uiLang, tu } = useUi()
  const mark = useAppStore(selectAttachedMark)
  const hasTurns = useAppStore((s) => s.turns.length > 0)
  const lang = useAppStore(selectSessionLang)
  const promptBlocks = useMemo(() => promptBlocksFor(drawing, activeProject), [drawing, activeProject])
  const ta = useRef<HTMLTextAreaElement>(null)
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [canSpeak] = useState(dictationSupported)
  const [slashIndex, setSlashIndex] = useState(0)
  const [dismissedSlashText, setDismissedSlashText] = useState<string | null>(null)
  const speech = useDictation(lang)
  const rewriteOpen = useRewrite((s) => s.status !== "idle")
  // Re-evaluated when the source changes (the model rewrite needs the API).
  const sourceKind = useAppStore((s) => s.session.source.kind)
  const withModel = sourceKind === "api" && canRewriteWithModel()
  /** What tidy-up can honestly do for this text: the model (any text), or a local split (clear joins only). */
  const tidyAvailable = (s: string) =>
    !s.trimStart().startsWith("/") && s.trim().length >= 12 && (withModel || splitLocally(s).length >= 2)
  const suggestTidy = !rewriteOpen && tidyAvailable(text) && needsTidy(text)
  const startTidy = (s: string) => {
    useDraft.getState().setText("")
    void useRewrite.getState().start(s)
  }

  const slashMatches = useMemo(() => filterSlashCommands(text), [text])
  const slashOpen = slashMatches.length > 0 && dismissedSlashText !== text
  const safeSlashIndex = slashMatches.length > 0 ? Math.min(slashIndex, slashMatches.length - 1) : 0
  const activeSlashCmd: SlashCommandSpec | undefined = slashOpen ? slashMatches[safeSlashIndex] : undefined

  // Auto-grow up to MAX_LINES.
  useLayoutEffect(() => {
    const el = ta.current
    if (!el) return
    el.style.height = "auto"
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

  // Keep the highlighted slash option scrolled into view during keyboard navigation.
  useEffect(() => {
    if (!slashOpen || !activeSlashCmd) return
    const opt = document.getElementById(`${slashMenuId}-${activeSlashCmd.id}`)
    opt?.scrollIntoView?.({ block: "nearest" })
  }, [slashOpen, activeSlashCmd, slashMenuId])

  const setCrossPlanScopeSafe = (on: boolean) => {
    actions().setCrossPlanScope(on)
  }

  const submitQuestion = (questionText: string) => {
    const q = questionText.trim()
    if (!q || pending) return
    useDraft.getState().setText("")
    setDismissedSlashText(null)
    if (!mark && crossPlanScope && activeProject && activeProject.floors.length >= 2) {
      const projectScope: Scope = {
        kind: "project",
        project_id: activeProject.id,
        drawing_ids: activeProject.floors.map((f) => f.drawing_id),
      }
      void actions().ask(q, { scope: projectScope })
    } else {
      void actions().ask(q)
    }
    ta.current?.focus()
  }

  const executeSlashAction = (action: ParsedSlashAction) => {
    if (action.kind === "invalid") {
      toast(action.message, { toasterId: CHAT_TOASTER })
      ta.current?.focus()
      return
    }
    if (action.kind === "prompt") {
      if (pending) return
      submitQuestion(action.prompt)
      return
    }
    useDraft.getState().setText("")
    setDismissedSlashText(null)
    const st = appStore.getState()
    switch (action.id) {
      case "new": {
        if (st.turns.length > 0 || st.ui.marks.length > 0) {
          const restore = actions().newConversation()
          toast(tu("hist_saved"), {
            id: NEW_CHAT_UNDO_TOAST,
            toasterId: CHAT_TOASTER,
            duration: 6000,
            action: {
              label: tu("undo"),
              onClick: () => restore(),
            },
          })
        }
        break
      }
      case "history": {
        sessionHistory.setOpen(true)
        break
      }
      case "clear": {
        if (st.turns.length > 0 || st.ui.marks.length > 0) {
          const restore = actions().newConversation()
          toast(tu("new_chat_cleared"), {
            id: NEW_CHAT_UNDO_TOAST,
            toasterId: CHAT_TOASTER,
            duration: 6000,
            action: {
              label: tu("undo"),
              onClick: () => restore(),
            },
          })
        }
        break
      }
      case "export": {
        const activeTurn = selectActiveTurn(st)
        if (activeTurn?.response) {
          downloadText(
            `aura-${drawing.id}-${activeTurn.id}-evidence.csv`,
            evidenceCsv(activeTurn.response, activeTurn.toggles, drawing),
          )
        } else {
          toast(tu("pl_csv_empty"), { toasterId: CHAT_TOASTER })
        }
        break
      }
      case "trace":
        actions().setTraceOpen(!st.ui.traceOpen)
        break
      case "compare":
        if (drawing.images.original) {
          actions().setCompare({ on: !st.ui.compare.on })
        } else {
          toast("No separate original image on this drawing.", { toasterId: CHAT_TOASTER })
        }
        break
      case "all-floors":
        if (activeProject && activeProject.floors.length >= 2) {
          setCrossPlanScopeSafe(!crossPlanScope)
        } else {
          toast("Open or bundle a multi-floor project in the library first.", { toasterId: CHAT_TOASTER })
        }
        break
      case "fit":
        actions().resetZoom()
        break
      case "zoom":
        actions().setZoom(action.zoom)
        break
      case "select":
      case "mark":
        if (st.ui.tool === "whatif") actions().cancelWhatIf()
        actions().setTool(action.id)
        break
      case "whatif": {
        const canWhatIf = drawing.rooms.some((r) => Boolean(r.rect)) && selectAnswererMode(st) !== "agent"
        if (canWhatIf) {
          actions().setTool("whatif")
        } else {
          toast("What-If resizing requires CAD room geometry in Exact or Hybrid mode.", { toasterId: CHAT_TOASTER })
        }
        break
      }
      case "undo":
        if (st.undoStack.length > 0) {
          actions().undo()
        } else {
          actions().undoNewConversation()
        }
        break
      case "help":
        actions().setShortcutsOpen(true)
        break
    }
    ta.current?.focus()
  }

  const pickSlashSpec = (spec: SlashCommandSpec) => {
    if (spec.id === "zoom" && !/^\/zoom\s+\S+/i.test(text.trim())) {
      useDraft.getState().fill("/zoom ")
      setDismissedSlashText(null)
      return
    }
    const typed = resolveSlashCommand(text, { hasTurns })
    if (typed && typed.id === spec.id) {
      executeSlashAction(typed)
      return
    }
    const resolved = resolveSlashCommand(spec.cmd, { hasTurns })
    if (resolved) executeSlashAction(resolved)
  }

  const send = () => {
    const exact = resolveSlashCommand(text, { hasTurns })
    if (exact) {
      executeSlashAction(exact)
      return
    }
    if (slashOpen && activeSlashCmd) {
      pickSlashSpec(activeSlashCmd)
      return
    }
    submitQuestion(text)
  }

  const resolvedDraftSlash = resolveSlashCommand(text, { hasTurns })
  const canSend =
    Boolean(text.trim()) && (!pending || resolvedDraftSlash?.kind === "client")

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
      <RewriteCard />
      {voiceOpen ? (
        <Suspense fallback={null}>
          <VoiceCard
            state={speech.state}
            text={speech.text}
            error={speech.error}
            langName={LANG_NAME_EN[lang]}
            onStop={speech.stop}
            onRetry={speech.start}
            onDiscard={closeVoice}
            onUse={() => {
              const said = speech.text
              closeVoice()
              // A transcript is often several questions at once: offer the same confirm card.
              if (tidyAvailable(said) && needsTidy(said, { fromVoice: true })) startTidy(said)
              else useDraft.getState().fill(said)
            }}
          />
        </Suspense>
      ) : null}

      {slashOpen ? (
        <ul
          id={slashMenuId}
          role="listbox"
          aria-label="Slash commands"
          data-slash-menu
          className="flex max-h-56 flex-col gap-0.5 overflow-y-auto rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-md"
        >
          {slashMatches.map((spec, idx) => {
            const activeOpt = idx === safeSlashIndex
            return (
              <li
                key={spec.id}
                id={`${slashMenuId}-${spec.id}`}
                role="option"
                aria-selected={activeOpt}
                data-slash-cmd={spec.id}
                onMouseDown={(e) => {
                  // Keep textarea focus when clicking an option.
                  e.preventDefault()
                  pickSlashSpec(spec)
                }}
                onPointerEnter={(e) => {
                  if (e.pointerType === "mouse") setSlashIndex(idx)
                }}
                className={cn(
                  "flex min-h-10 cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm transition-colors",
                  activeOpt ? "bg-muted text-foreground" : "text-foreground hover:bg-muted/70",
                )}
              >
                <TerminalIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <span className="num shrink-0 font-semibold text-foreground">
                  {spec.cmd}
                  {spec.argHint ? <span className="ml-1 font-normal text-muted-foreground">{spec.argHint}</span> : null}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{spec.descriptionKey ? tu(spec.descriptionKey) : spec.description}</span>
              </li>
            )
          })}
        </ul>
      ) : null}

      {suggestTidy ? (
        // Suggested by the local heuristic (several questions, long or rambling text): never a model call to decide.
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="min-w-0 flex-1 truncate">
            {withModel ? "Looks like several questions." : "Looks like several questions; they can be split locally."}
          </span>
          <Button variant="secondary" data-tidy="suggested" onClick={() => startTidy(text)}>
            <SparklesIcon data-icon="inline-start" />
            {t(lang, "rw_offer")}
          </Button>
        </div>
      ) : null}

      {mark ? (
        <div className="flex min-w-0">
          <span className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-user-surface py-0 pr-0 pl-2.5 text-sm font-medium text-user">
            <CrosshairIcon className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">{tu('mark_chip', { label: markLabel(mark.label, tu) })}</span>
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0 text-user hover:bg-user/10 hover:text-user"
              aria-label={tu('mark_remove', { label: markLabel(mark.label, tu) })}
              onClick={() => actions().attachMark(null)}
            >
              <XIcon />
            </Button>
          </span>
        </div>
      ) : crossPlanScope && activeProject && activeProject.floors.length >= 2 ? (
        <div className="flex min-w-0">
          <span
            data-project-scope-chip={activeProject.id}
            className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-info-surface py-0 pr-0 pl-2.5 text-sm font-medium text-info"
          >
            <LayersIcon className="size-3.5 shrink-0" aria-hidden />
            <span className="num truncate">
              {tu('floor_all_n', { n: fmtCount(activeProject.floors.length, uiLang) })}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0 text-info hover:bg-info/10 hover:text-info"
              aria-label={`${tu('floor_all_n', { n: fmtCount(activeProject.floors.length, uiLang) })} — single floor`}
              onClick={() => setCrossPlanScopeSafe(false)}
            >
              <XIcon />
            </Button>
          </span>
        </div>
      ) : null}

      <div
        className={cn(
          "flex items-end gap-1.5 rounded-xl border border-input bg-background p-1.5 transition-shadow",
          "focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30"
        )}
      >
        <textarea
          ref={ta}
          rows={1}
          value={text}
          onChange={(e) => {
            setSlashIndex(0)
            setDismissedSlashText(null)
            useDraft.getState().setText(e.target.value)
          }}
          onKeyDown={(e) => {
            if (slashOpen) {
              if (e.key === "ArrowDown") {
                e.preventDefault()
                setSlashIndex((i) => (i + 1) % slashMatches.length)
                return
              }
              if (e.key === "ArrowUp") {
                e.preventDefault()
                setSlashIndex((i) => (i - 1 + slashMatches.length) % slashMatches.length)
                return
              }
              if (e.key === "Escape") {
                e.preventDefault()
                e.stopPropagation()
                setDismissedSlashText(text)
                return
              }
              if (
                e.key === "Tab" &&
                !e.shiftKey &&
                !e.ctrlKey &&
                !e.metaKey &&
                !e.altKey &&
                activeSlashCmd
              ) {
                e.preventDefault()
                const nextFill = activeSlashCmd.acceptsArgs ? `${activeSlashCmd.cmd} ` : activeSlashCmd.cmd
                if (text === nextFill && !activeSlashCmd.acceptsArgs) {
                  pickSlashSpec(activeSlashCmd)
                } else {
                  useDraft.getState().fill(nextFill)
                  setDismissedSlashText(null)
                }
                return
              }
            }
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault()
              send()
            }
          }}
          placeholder={tu('ask_placeholder')}
          aria-label={tu('ask_label')}
          aria-autocomplete={slashOpen ? "list" : undefined}
          aria-controls={slashOpen ? slashMenuId : undefined}
          aria-activedescendant={slashOpen && activeSlashCmd ? `${slashMenuId}-${activeSlashCmd.id}` : undefined}
          maxLength={MAX_QUESTION}
          aria-describedby={hintId}
          aria-keyshortcuts="/"
          className="max-h-24 min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] leading-5 outline-none placeholder:text-muted-foreground focus-visible:outline-none"
        />
        {!rewriteOpen && tidyAvailable(text) && !suggestTidy ? (
          // Manual tidy-up for any input (the model with the API, a clear local split otherwise).
          <Button
            variant="ghost"
            size="icon"
            aria-label={t(lang, "rw_offer")}
            title={withModel ? "Tidy this up with Gemini (1 model request)" : "Split into separate questions (local, no model)"}
            data-tidy="manual"
            onClick={() => startTidy(text)}
          >
            <SparklesIcon />
          </Button>
        ) : null}
        {canSpeak ? (
          <MicButton
            open={voiceOpen}
            state={speech.state}
            onToggle={() => (!voiceOpen ? openVoice() : speech.state === "listening" ? speech.stop() : closeVoice())}
          />
        ) : null}
        <Button
          size="icon"
          aria-label={tu('send')}
          title={tu('send_hint')}
          disabled={!canSend}
          onClick={send}
        >
          <ArrowUpIcon />
        </Button>
      </div>

      {pending && text.trim() ? (
        <p id={hintId} className="px-1 text-xs text-muted-foreground" role="status">
          {tu('waiting')}
        </p>
      ) : text.length > MAX_QUESTION - 200 ? (
        <p id={hintId} className="num px-1 text-xs text-muted-foreground" role="status">
          {tu('chars', { n: text.length, max: MAX_QUESTION })}
        </p>
      ) : null}

      <ScrollRow label={tu("pb_row")}>
        <ul aria-label="Prompt blocks" className="flex shrink-0 gap-1.5">
          {promptBlocks.map((b) => (
            <li key={b.label}>
              <button
                type="button"
                title={tu('pb_fill', { text: b.text })}
                onClick={() => fillBlock(b)}
                className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-lg bg-muted px-3 text-xs font-semibold text-foreground transition-colors outline-none hover:bg-accent-foreground/10 focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {tu(PB_LABEL[b.label] ?? 'pb_list')}
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
