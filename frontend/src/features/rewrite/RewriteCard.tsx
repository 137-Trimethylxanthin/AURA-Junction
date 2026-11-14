import { useEffect, useRef } from "react"
import { AnimatePresence, motion, useReducedMotion } from "motion/react"
import { CheckIcon, PencilIcon, SparklesIcon, XIcon } from "lucide-react"
import type { BoxSubjectAny, Lang } from "@/api/contract"
import { BOX_SUBJECT_NAMES } from "@/api/contract"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Spinner } from "@/components/ui/spinner"
import { t } from "@/i18n/strings"
import { fmtCount, noun } from "@/lib/format"
import { cn } from "@/lib/utils"
import { fmtUsd, scopeChipText } from "@/features/chat/view"
import { useDraft } from "@/features/chat/draft"
import { selectIsPending, useAppStore } from "@/store"
import { useRewrite, type RewriteBlockState } from "./state"

const VERB = { count: "rw_count", area: "rw_area", other: "rw_ask" } as const

function objectText(subject: string | undefined, lang: Lang): string | null {
  if (!subject) return null
  return (BOX_SUBJECT_NAMES as readonly string[]).includes(subject)
    ? noun(subject as BoxSubjectAny, 2, lang)
    : subject
}

/**
 * The confirm card for /rewrite (or a local split): the original text, then one editable block per proposed
 * question (verb · object · scope + the sentence). Nothing is sent to the drawing agent until the user confirms.
 * Keyboard: Tab through the blocks, Enter in a block asks it, Escape dismisses the card.
 */
export function RewriteCard() {
  const st = useRewrite()
  const pending = useAppStore(selectIsPending)
  const reduce = useReducedMotion()
  const root = useRef<HTMLDivElement>(null)
  const open = st.status !== "idle"

  // Move focus into the card once its questions are ready, so Tab/Enter work right away.
  useEffect(() => {
    if (st.status === "ready")
      root.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus()
  }, [st.status])

  const lang = st.lang
  const spring = reduce
    ? { duration: 0.12 }
    : ({ type: "spring", bounce: 0, duration: 0.35 } as const)
  const routable = st.blocks.filter((b) => b.routable && !b.asked)

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          key="rewrite"
          ref={root}
          role="group"
          aria-label={t(lang, "rw_title")}
          data-rewrite-card={st.via}
          lang={lang}
          // Rises out of the composer it came from, and goes back the same way.
          style={{ transformOrigin: "50% 100%" }}
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.98 }}
          transition={spring}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation()
              st.dismiss()
              useDraft.getState().focus()
            }
          }}
          className="flex max-h-[min(50dvh,34rem)] flex-col overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm"
        >
          <div className="flex items-start gap-2 p-3 pb-2">
            <SparklesIcon
              className="mt-0.5 size-4 shrink-0 text-muted-foreground"
              aria-hidden
            />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <h3 className="text-sm font-semibold">{t(lang, "rw_title")}</h3>
              <p
                className="line-clamp-3 text-xs [overflow-wrap:anywhere] text-muted-foreground"
                data-rewrite-original
              >
                “{st.original}”
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t(lang, "rw_dismiss")}
              onClick={() => {
                st.dismiss()
                useDraft.getState().focus()
              }}
            >
              <XIcon />
            </Button>
          </div>

          {/* Only the questions scroll; the header and the confirm actions stay in view. */}
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-3 pb-3">
            {st.status === "loading" ? (
              <p
                className="flex items-center gap-2 text-sm text-muted-foreground"
                aria-live="polite"
              >
                <Spinner />
                {t(lang, "rw_working")}
              </p>
            ) : null}

            {st.status === "error" ? (
              <p className="text-sm text-destructive" role="alert">
                {st.note}
              </p>
            ) : null}

            {st.blocks.length ? (
              <ol
                className="flex flex-col gap-2"
                aria-label={t(lang, "rw_title")}
              >
                <AnimatePresence initial={false}>
                  {st.blocks.map((b, i) => (
                    <motion.li
                      key={b.key}
                      layout={!reduce}
                      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={
                        reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97 }
                      }
                      transition={{
                        ...spring,
                        ...(reduce ? {} : { delay: Math.min(i, 5) * 0.04 }),
                      }}
                    >
                      <Block b={b} n={i + 1} lang={lang} pending={pending} />
                    </motion.li>
                  ))}
                </AnimatePresence>
              </ol>
            ) : null}

            {st.notAnswerable.length ? (
              <div
                className="flex flex-col gap-1 text-xs text-muted-foreground"
                data-rewrite-not-here
              >
                <span className="font-medium">{t(lang, "rw_not_here")}:</span>
                <ul className="flex flex-col gap-0.5">
                  {st.notAnswerable.map((x) => (
                    <li key={x} className="[overflow-wrap:anywhere]">
                      “{x}”
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          {st.status === "ready" ? (
            <div className="flex flex-wrap items-center gap-2 border-t bg-muted/40 px-3 py-2">
              <p
                className="num mr-auto text-xs text-muted-foreground"
                data-rewrite-cost
              >
                {st.via === "model" && st.cost
                  ? `${t(lang, "rw_by_model")} · ${t(lang, st.cost.calls === 1 ? "req_one" : "req_other", { n: fmtCount(st.cost.calls, lang) })} · ${fmtUsd(st.cost.cost_usd)}`
                  : t(lang, "rw_local")}
                {st.note && st.via === "local" ? ` · ${st.note}` : ""}
              </p>
              <Button variant="ghost" onClick={() => st.dismiss()}>
                {t(lang, "rw_dismiss")}
              </Button>
              <Button
                disabled={!routable.length || pending}
                onClick={() => void st.askAll()}
              >
                {t(lang, "rw_ask_all")}
              </Button>
            </div>
          ) : null}
        </motion.div>
      ) : null}
    </AnimatePresence>
  )
}

function Block({
  b,
  n,
  lang,
  pending,
}: {
  b: RewriteBlockState
  n: number
  lang: Lang
  pending: boolean
}) {
  const st = useRewrite.getState()
  const object = objectText(b.subject, lang)
  const sc = b.scope
  const scope = !sc
    ? null
    : sc.kind === "floor"
      ? t(lang, "rw_whole")
      : sc.kind === "unit"
        ? t(lang, "rw_unit", { unit: sc.unit })
        : sc.kind === "room"
          ? t(lang, "rw_room", { room: sc.room_id })
          : sc.kind === "label" || sc.kind === "region"
            ? sc.label
            : scopeChipText(sc, lang)
  const disabled = !b.routable || b.asked
  return (
    <div
      data-rewrite-block={n}
      data-routable={b.routable}
      data-asked={b.asked || undefined}
      className={cn(
        "flex flex-col gap-2 rounded-lg border p-2.5",
        !b.routable && "opacity-60"
      )}
    >
      {b.intent || object || scope ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {b.intent ? (
            <Badge variant="secondary">{t(lang, VERB[b.intent])}</Badge>
          ) : null}
          {object ? (
            <span className="font-medium text-foreground">{object}</span>
          ) : null}
          {scope ? (
            <>
              <span aria-hidden className="text-muted-foreground">
                ·
              </span>
              {/* Scope is the user's choice of place: the user-input colour. */}
              <span className="rounded-md bg-user-surface px-1.5 py-0.5 font-medium text-user">
                {scope}
              </span>
            </>
          ) : null}
        </div>
      ) : null}
      {/* Grows with the sentence (up to about 12 lines) so the whole question can be read before it is sent. */}
      <Textarea
        rows={1}
        value={b.question}
        aria-label={`Question ${n}`}
        disabled={b.asked}
        maxLength={2000}
        onChange={(e) => st.edit(b.key, e.target.value)}
        onKeyDown={(e) => {
          if (
            e.key === "Enter" &&
            !e.shiftKey &&
            !e.nativeEvent.isComposing &&
            !disabled &&
            !pending
          ) {
            e.preventDefault()
            void st.askOne(b.key)
          }
        }}
        className="max-h-64 min-h-10 resize-none [overflow-wrap:anywhere]"
      />
      {!b.routable ? (
        <p className="text-xs text-muted-foreground">
          {t(lang, "rw_cant_place")}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="outline"
          disabled={disabled || pending}
          onClick={() => void st.askOne(b.key)}
        >
          {b.asked ? <CheckIcon data-icon="inline-start" /> : null}
          {b.asked ? t(lang, "rw_asked") : t(lang, "rw_ask_one")}
        </Button>
        <Button
          variant="ghost"
          disabled={b.asked}
          onClick={() => {
            useDraft.getState().fill(b.question)
            st.remove(b.key)
          }}
        >
          <PencilIcon data-icon="inline-start" />
          {t(lang, "rw_edit")}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto"
          aria-label={`Remove question ${n}`}
          disabled={b.asked}
          onClick={() => st.remove(b.key)}
        >
          <XIcon />
        </Button>
      </div>
    </div>
  )
}
