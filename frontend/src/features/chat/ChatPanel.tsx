import { tu, useUi } from "@/i18n/uiLang"
import type { UiKey } from "@/i18n/strings"
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { motion, useReducedMotion } from "motion/react"
import { toast } from "sonner"
import { Toaster } from "@/components/ui/sonner"
import { SPLIT_QUERY } from "@/features/plan/shared"
import { useMediaQuery } from "@/hooks/use-media-query"
import { isRoomEvidenceId, roomIdOfEvidence, deriveAnswer } from "@/lib/answer"
import { noun } from "@/lib/format"
import {
  appStore,
  actions,
  useAppStore,
  type AgentTurn,
  type AppState,
  type UserTurn,
} from "@/store"
import { AgentCard } from "./AgentCard"
import { SuggestionList } from "./Suggestions"
import { UserBubble } from "./UserBubble"
import { announce, valueParts } from "./view"

const UNDO_TOAST = "evidence-undo"

/** Chat toasts render in their own toaster at the top of the sidebar column (off the plan and the composer). */
const CHAT_TOASTER = "chat"
const REJECT_TOAST_MS = 6000

function ChatToaster() {
  const wide = useMediaQuery(SPLIT_QUERY, true)
  return (
    <Toaster
      id={CHAT_TOASTER}
      position={wide ? "top-right" : "top-center"}
      offset={wide ? { top: 108, right: 16 } : { top: 64 }}
      visibleToasts={2}
    />
  )
}

/** "Window 3", "Room 3.04" for toasts. */
function evidenceName(s: AppState, turnId: string, evidenceId: string): string {
  const x = s.turns.find((t) => t.id === turnId)
  const resp = x && x.role === "agent" ? x.response : undefined
  if (isRoomEvidenceId(evidenceId))
    return `Room ${roomIdOfEvidence(evidenceId)}`
  const i = resp?.evidence.boxes.findIndex((b) => b.id === evidenceId) ?? -1
  const box = resp?.evidence.boxes[i]
  if (!box) return evidenceId
  const n = noun(box.subject, 1, "en")
  return `${n.charAt(0).toUpperCase()}${n.slice(1)} ${i + 1}`
}

/** Toasts for rejects (with Undo) and fallbacks. Always mounted with the chat. */
function useChatSideEffects() {
  useEffect(() => {
    const seenFallback = new Set<string>()
    const unsub = appStore.subscribe((s, prev) => {
      if (s.undoStack.length > prev.undoStack.length) {
        const e = s.undoStack[s.undoStack.length - 1]
        const x = s.turns.find((t) => t.id === e.turn_id)
        if (x && x.role === "agent") {
          const name = evidenceName(s, e.turn_id, e.evidence_id)
          let verb: UiKey
          if (isRoomEvidenceId(e.evidence_id)) {
            const room = x.response?.evidence.rooms.find(
              (r) => `room-${r.room_id}` === e.evidence_id
            )
            const now = x.toggles.included[e.evidence_id] ?? room?.included
            verb = now ? "ev_added" : "ev_left_out"
          } else
            verb = x.toggles.rejected[e.evidence_id] ? "ev_rejected" : "ev_restored"
          const d = x.response
            ? deriveAnswer(x.response, x.toggles, s.session.drawing)
            : null
          // Formatted like the card (decimal comma in French, the unit or noun after it).
          const now =
            x.response && d ? valueParts(x.response, d, x.response.detected_lang) : null
          toast(tu(verb, { name }), {
            id: UNDO_TOAST,
            toasterId: CHAT_TOASTER,
            duration: REJECT_TOAST_MS,
            description: now ? tu("answer_now", { value: now.join(" ") }) : undefined,
            action: { label: tu("undo"), onClick: () => actions().undo() },
          })
        }
      } else if (s.undoStack.length < prev.undoStack.length) {
        toast.dismiss(UNDO_TOAST)
      }
      // A new question makes the reject toast stale.
      if (s.turns.length > prev.turns.length) toast.dismiss(UNDO_TOAST)
      for (const t of s.turns) {
        if (t.role === "agent" && t.fellBack && !seenFallback.has(t.id)) {
          seenFallback.add(t.id)
          toast("Backend unreachable. Answering from local data.", {
            id: "fell-back",
            toasterId: CHAT_TOASTER,
          })
        }
      }
    })
    return () => {
      unsub()
    }
  }, [])
}

interface Pair {
  user: UserTurn
  reply?: AgentTurn
}

/**
 * Bring the newest question and its answer into view: the pair's top when it fits; otherwise as much of the
 * answer card as possible (its bottom, i.e. actions and footer, when the card alone fits; else the card's top).
 */
function scrollPairToTop(
  root: HTMLElement | null,
  userId: string | undefined,
  smooth: boolean
) {
  if (!root || !userId) return
  const pair = root.querySelector<HTMLElement>(`[data-pair="${userId}"]`)
  const scroller = root.parentElement
  if (!pair || !scroller) return
  const behavior: ScrollBehavior = smooth ? "smooth" : "auto"
  const card = pair.querySelector<HTMLElement>("article[data-turn]")
  const room = scroller.clientHeight
  if (!card || pair.offsetHeight + 12 <= room)
    return pair.scrollIntoView({ block: "start", behavior })
  card.scrollIntoView({
    block: card.offsetHeight + 12 <= room ? "end" : "start",
    behavior,
  })
}

function usePairs(): Pair[] {
  const turns = useAppStore((s) => s.turns)
  return useMemo(() => {
    const replies = new Map<string, AgentTurn>()
    for (const t of turns) if (t.role === "agent") replies.set(t.replyTo, t)
    return turns
      .filter((t): t is UserTurn => t.role === "user")
      .map((u) => ({ user: u, reply: replies.get(u.id) }))
  }, [turns])
}

function LiveRegion() {
  const turns = useAppStore((s) => s.turns)
  const drawing = useAppStore((s) => s.session.drawing)
  const last = [...turns]
    .reverse()
    .find((t): t is AgentTurn => t.role === "agent")
  let text = ""
  if (last?.status === "done" && last.response)
    text = announce(
      last.response,
      deriveAnswer(last.response, last.toggles, drawing)
    )
  else if (last?.status === "error")
    text = "The question could not be answered."
  else if (last?.status === "pending") text = "Reading the drawing."
  return (
    <div className="sr-only" aria-live="polite" aria-atomic="true">
      {text}
    </div>
  )
}

export function ChatPanel() {
  useUi()
  useChatSideEffects()
  const pairs = usePairs()
  const root = useRef<HTMLDivElement>(null)
  const [portalHost, setPortalHost] = useState<HTMLElement | null>(null)
  const setRootRef = useCallback((node: HTMLDivElement | null) => {
    root.current = node
    setPortalHost(node?.closest("aside") ?? null)
  }, [])
  const count = useRef(0)
  const reduce = useReducedMotion()
  /** Follow the newest question until the user scrolls by hand. */
  const follow = useRef(false)

  // New question: bring it to the top of the scroller so the answer's number stays in view.
  useLayoutEffect(() => {
    const grew = pairs.length > count.current
    count.current = pairs.length
    if (!grew) return
    follow.current = true
    scrollPairToTop(root.current, pairs[pairs.length - 1]?.user.id, !reduce)
  })

  // The answer arrived (the card grew): keep the question at the top unless the user scrolled away.
  const lastId = pairs[pairs.length - 1]?.user.id
  const lastStatus = pairs[pairs.length - 1]?.reply?.status
  useLayoutEffect(() => {
    if (follow.current && lastStatus && lastStatus !== "pending")
      scrollPairToTop(root.current, lastId, false)
  }, [lastId, lastStatus])

  // Any manual scroll intent stops following.
  useEffect(() => {
    const scroller = root.current?.parentElement
    if (!scroller) return
    const stop = () => (follow.current = false)
    scroller.addEventListener("wheel", stop, { passive: true })
    scroller.addEventListener("touchmove", stop, { passive: true })
    scroller.addEventListener("keydown", stop)
    return () => {
      scroller.removeEventListener("wheel", stop)
      scroller.removeEventListener("touchmove", stop)
      scroller.removeEventListener("keydown", stop)
    }
  }, [])

  const liveAndToasts = (
    <>
      <LiveRegion />
      <ChatToaster />
    </>
  )

  return (
    <div ref={setRootRef} className="flex min-h-full flex-col">
      {portalHost ? createPortal(liveAndToasts, portalHost) : liveAndToasts}
      {pairs.length === 0 ? (
        <div className="flex flex-col gap-3 p-4 pt-5">
          <div className="flex flex-col gap-1">
            <h3 className="text-base font-semibold text-balance">
              {tu("chat_empty_title")}
            </h3>
            <p className="text-sm text-pretty text-muted-foreground">{tu("chat_empty_text")}</p>
          </div>
          <SuggestionList />
        </div>
      ) : (
        <ol className="flex flex-col gap-5 p-4" aria-label="Conversation">
          {pairs.map(({ user, reply }) => (
            // A new question rises in from the composer below (same path as the text it came from); fade only
            // with reduced motion. Transform/opacity only, so the scroll measurement above is unaffected.
            <motion.li
              key={user.id}
              data-pair={user.id}
              className="flex scroll-mt-3 flex-col gap-3"
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
              animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
              transition={
                reduce
                  ? { duration: 0.15 }
                  : { type: "spring", bounce: 0, duration: 0.35 }
              }
            >
              <UserBubble turn={user} reply={reply} />
              {reply ? <AgentCard turn={reply} lang={user.lang} /> : null}
            </motion.li>
          ))}
        </ol>
      )}
    </div>
  )
}
