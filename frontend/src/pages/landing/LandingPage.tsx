import { Suspense, lazy, useRef, useState, type ReactNode } from "react"
import { Link } from "react-router"
import {
  MotionConfig,
  motion,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
} from "motion/react"
import {
  ArrowDownIcon,
  ArrowRightIcon,
  MousePointerClickIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { PAGE_BTN, PageFrame, WRAP, useTitle } from "./chrome"
import {
  LIMIT_FACTS,
  RESULTS,
  factById,
  fmtDate,
  fmtInt,
  type Fact,
} from "./facts"
import { DEMO_ROUTE } from "./links"
import { CountUp, Reveal, RevealItem } from "./motion"

// The replay and the story pull in the drawing data and the local answer code: load them after the shell.
const HeroDemo = lazy(() => import("./HeroDemo"))
const ScrollStory = lazy(() => import("./ScrollStory"))

const NAV = [
  { href: "#how", label: "How it works" },
  { href: "#measured", label: "Measured" },
  { href: "#limits", label: "Limits" },
  { href: "#use", label: "Use" },
]

/** Plugin status: flip to "ready" when the API lane ships each one. */
const PLUGINS: { name: string; body: string; status: "ready" | "building" }[] =
  [
    {
      name: "CSV",
      body: "Every answer exports its evidence: number, item, room, position and whether you kept it.",
      status: "ready",
    },
    {
      name: "REST",
      body: "POST /ask with a drawing id and a question; the same answer and evidence come back as JSON.",
      status: "building",
    },
    {
      name: "MCP",
      body: "The same ask() as a tool, so an agent can query a drawing and cite the numbered boxes.",
      status: "building",
    },
  ]

export default function LandingPage() {
  useTitle("AURA: answers from construction drawings, drawn on the plan")
  return (
    <MotionConfig reducedMotion="user">
      <PageFrame nav={NAV}>
        <Hero />
        <HowItWorks />
        <Measured />
        <Limits />
        <Use />
      </PageFrame>
    </MotionConfig>
  )
}

// ---- hero ----------------------------------------------------------------------------------------

function Hero() {
  const swiss = factById("swiss-full")
  const ref = useRef<HTMLElement>(null)
  const reduce = useReducedMotion()
  // Scroll-linked: as the hero leaves, the live demo sinks back a little (transform only, springed).
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start start", "end start"],
  })
  const smooth = useSpring(scrollYProgress, {
    stiffness: 260,
    damping: 40,
    mass: 0.6,
  })
  const y = useTransform(smooth, [0, 1], [0, 72])
  const scale = useTransform(smooth, [0, 1], [1, 0.955])
  const fade = useTransform(smooth, [0, 0.8], [1, 0.6])

  return (
    <section ref={ref} aria-labelledby="hero-h" className="border-b">
      <div
        className={cn(
          WRAP,
          "grid items-center gap-8 py-8 lg:min-h-[calc(100dvh-4rem-1px)] lg:grid-cols-[minmax(0,21rem)_minmax(0,1fr)] lg:gap-10 lg:py-6 xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] xl:gap-12"
        )}
      >
        <div className="flex min-w-0 flex-col gap-5">
          <h1
            id="hero-h"
            className="l-enter text-[2.25rem] leading-[1.05] font-semibold tracking-[-0.025em] text-balance sm:text-5xl lg:text-[2.75rem] xl:text-5xl"
          >
            Answers from construction drawings, drawn on the plan
          </h1>
          <p
            className="l-enter max-w-[34rem] text-base text-pretty text-muted-foreground sm:text-lg"
            style={{ ["--d" as string]: "60ms" }}
          >
            Ask how many windows, doors or square metres a drawing holds. AURA
            computes the answer from the CAD geometry and numbers every item it
            counted, so you can check it in seconds.
          </p>
          {swiss ? (
            <p
              className="l-enter border-l-2 border-success pl-3 text-sm"
              style={{ ["--d" as string]: "100ms" }}
            >
              <span className="font-semibold text-success tabular-nums">
                {swiss.value}
              </span>{" "}
              of <span className="tabular-nums">{fmtInt(swiss.n)}</span> Swiss
              dataset questions answered correctly from the DXF geometry, with{" "}
              <span className="tabular-nums">{swiss.model_calls ?? 0}</span>{" "}
              model calls.{" "}
              <a
                href="#measured"
                className="text-muted-foreground underline decoration-border underline-offset-4 outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring"
              >
                How this was measured
              </a>
            </p>
          ) : null}
          <div
            className="l-enter flex flex-col gap-2 sm:flex-row sm:flex-wrap"
            style={{ ["--d" as string]: "140ms" }}
          >
            <Button asChild className={PAGE_BTN}>
              <Link to={DEMO_ROUTE}>
                Try it on the sample drawing
                <ArrowRightIcon data-icon="inline-end" />
              </Link>
            </Button>
            <Button asChild variant="outline" className={PAGE_BTN}>
              <a href="#how">
                See how it works
                <ArrowDownIcon data-icon="inline-end" />
              </a>
            </Button>
          </div>
          <p
            className="l-enter hidden items-center gap-2 text-sm text-muted-foreground lg:flex"
            style={{ ["--d" as string]: "180ms" }}
          >
            <MousePointerClickIcon
              className="size-4 shrink-0 text-info"
              aria-hidden
            />
            Click a numbered box to reject it. The count follows; undo puts it
            back.
          </p>
        </div>
        <div className="min-w-0">
          <p className="mb-3 flex items-center gap-2 text-sm text-muted-foreground lg:hidden">
            <MousePointerClickIcon
              className="size-4 shrink-0 text-info"
              aria-hidden
            />
            Tap a numbered box to reject it. The count follows; undo puts it
            back.
          </p>
          <motion.div
            style={reduce ? undefined : { y, scale, opacity: fade }}
            className="origin-top"
          >
            <Suspense
              fallback={
                <Skeleton className="aspect-[4/3] w-full rounded-2xl md:aspect-auto md:h-[min(calc(100dvh-8rem),40rem)]" />
              }
            >
              <HeroDemo />
            </Suspense>
          </motion.div>
        </div>
      </div>
    </section>
  )
}

// ---- section frame -------------------------------------------------------------------------------

function Section({
  id,
  title,
  lead,
  children,
}: {
  id: string
  title: string
  lead?: ReactNode
  children: ReactNode
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-h`}
      className="border-b py-16 sm:py-20 lg:py-24"
    >
      <div className={cn(WRAP, "flex flex-col gap-10")}>
        <Reveal className="flex max-w-[44rem] flex-col gap-3" amount={0.6}>
          <RevealItem>
            <h2
              id={`${id}-h`}
              className="text-3xl leading-tight font-semibold tracking-[-0.02em] text-balance sm:text-4xl"
            >
              {title}
            </h2>
          </RevealItem>
          {lead ? (
            <RevealItem>
              <p className="text-base text-pretty text-muted-foreground sm:text-lg">
                {lead}
              </p>
            </RevealItem>
          ) : null}
        </Reveal>
        {children}
      </div>
    </section>
  )
}

// ---- how it works --------------------------------------------------------------------------------

function HowItWorks() {
  const check = factById("tile-check-cost")
  const cells = [
    {
      term: "The answer",
      value: "0 model calls",
      detail: "Geometry and arithmetic in code. $0.00 per question.",
    },
    {
      term: "The check",
      value: "1 request per drawing",
      detail: check
        ? `All boxes in one Decisions API call; ${check.value} measured once (n=${check.n}).`
        : "All boxes in one Decisions API call.",
    },
    {
      term: "Without CAD data",
      value: "A model estimate",
      detail:
        "Scans and images have no geometry, so their counts come from Gemini and are labelled that way.",
    },
  ]
  return (
    <Section
      id="how"
      title="How AURA answers a question"
      lead="Code answers first and shows its work. A model only looks at what the code has drawn, and you have the last word."
    >
      <Suspense
        fallback={
          <Skeleton className="h-[min(80dvh,40rem)] w-full rounded-xl" />
        }
      >
        <ScrollStory />
      </Suspense>
      <Reveal className="grid gap-4 md:grid-cols-3">
        {cells.map((c) => (
          <RevealItem key={c.term}>
            <Card className="h-full">
              <CardHeader>
                <CardDescription>{c.term}</CardDescription>
                <CardTitle className="text-xl font-semibold tracking-tight tabular-nums">
                  {c.value}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-pretty text-muted-foreground">
                  {c.detail}
                </p>
              </CardContent>
            </Card>
          </RevealItem>
        ))}
      </Reveal>
    </Section>
  )
}

// ---- measured ------------------------------------------------------------------------------------

function FactMeta({ fact }: { fact: Fact }) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
      <dt className="text-muted-foreground">n</dt>
      <dd className="tabular-nums">
        {fmtInt(fact.n)} {fact.n_unit}
      </dd>
      {fact.model_calls !== undefined && fact.kind === "result" ? (
        <>
          <dt className="text-muted-foreground">Model calls</dt>
          <dd className="tabular-nums">{fmtInt(fact.model_calls)}</dd>
        </>
      ) : null}
      <dt className="text-muted-foreground">Measured on</dt>
      <dd className="text-pretty">{fact.scope}</dd>
      <dt className="text-muted-foreground">How</dt>
      <dd className="text-pretty">{fact.method}</dd>
      <dt className="text-muted-foreground">Date</dt>
      <dd className="tabular-nums">{fmtDate(fact.measured)}</dd>
    </dl>
  )
}

function FactCard({
  fact,
  tone = "success",
}: {
  fact: Fact
  tone?: "success" | "warning"
}) {
  const [t, setT] = useState(0)
  const frac =
    fact.numerator !== undefined && fact.denominator
      ? fact.numerator / fact.denominator
      : null
  return (
    <Card data-fact={fact.id} className="h-full">
      <CardHeader>
        <CountUp
          value={fact.value}
          onProgress={setT}
          className="text-5xl leading-none font-semibold tracking-[-0.03em] tabular-nums"
        />
        <CardTitle className="pt-2 font-semibold text-pretty">
          {fact.label}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {frac !== null ? (
          <div className="flex flex-col gap-2">
            <Progress
              value={frac * 100 * t}
              aria-label={`${fmtInt(fact.numerator!)} of ${fmtInt(fact.denominator!)} ${fact.n_unit}`}
              className={cn(
                "h-2",
                "[&>*]:transition-none",
                tone === "warning" ? "[&>*]:bg-warning" : "[&>*]:bg-success"
              )}
            />
            <p className="text-sm text-muted-foreground tabular-nums">
              {fmtInt(fact.numerator!)} of {fmtInt(fact.denominator!)}{" "}
              {fact.n_unit}
            </p>
          </div>
        ) : null}
        <FactMeta fact={fact} />
      </CardContent>
      {fact.caveat ? (
        <CardFooter className="mt-auto text-sm text-pretty text-muted-foreground">
          {fact.caveat}
        </CardFooter>
      ) : null}
    </Card>
  )
}

function Measured() {
  if (!RESULTS.length) return null
  return (
    <Section
      id="measured"
      title="Accuracy and cost, measured with their n"
      lead="Only numbers we measured appear here, with the sample size and the date. Results still being measured stay off this page until they have an n."
    >
      <Reveal
        as="ul"
        className={cn(
          "grid gap-4",
          RESULTS.length > 1 && "lg:grid-cols-2",
          RESULTS.length > 2 && "xl:grid-cols-3"
        )}
      >
        {RESULTS.map((f) => (
          <RevealItem as="li" key={f.id}>
            <FactCard fact={f} />
          </RevealItem>
        ))}
      </Reveal>
    </Section>
  )
}

// ---- limits --------------------------------------------------------------------------------------

const LIMITS: { title: string; body: string }[] = [
  {
    title: "The check sees drawn boxes, not missed items",
    body: "Gemini can doubt a box that is there. It cannot point to a window that was never boxed, so a missed item stays missed.",
  },
  {
    title: "Check scores are ranked within one request",
    body: "The same tile scores differently from one request to the next, so AURA compares boxes against each other and shows the least certain one.",
  },
  {
    title: "Box positions are not scored yet",
    body: "The 99.0 % figure scores the numbers in the answers. Where each evidence box sits on the plan has no measurement of its own yet.",
  },
]

function Limits() {
  return (
    <Section
      id="limits"
      title="What AURA cannot do yet"
      lead="Without CAD data there is no geometry to compute from, so counts on scans and images are a model estimate for you to check, not an exact answer. Here is how they did on held-out test images."
    >
      {LIMIT_FACTS.length ? (
        <Reveal
          as="ul"
          className={cn(
            "grid gap-4 lg:grid-cols-2",
            LIMIT_FACTS.length > 2 && "xl:grid-cols-3"
          )}
        >
          {LIMIT_FACTS.map((f) => (
            <RevealItem as="li" key={f.id}>
              <FactCard fact={f} tone="warning" />
            </RevealItem>
          ))}
        </Reveal>
      ) : null}
      <Reveal as="ul" className="grid gap-x-10 gap-y-6 md:grid-cols-3">
        {LIMITS.map((l) => (
          <RevealItem
            as="li"
            key={l.title}
            className="flex flex-col gap-1.5 border-t-2 border-warning/60 pt-4"
          >
            <h3 className="text-base font-semibold text-pretty">{l.title}</h3>
            <p className="text-sm text-pretty text-muted-foreground">
              {l.body}
            </p>
          </RevealItem>
        ))}
      </Reveal>
    </Section>
  )
}

// ---- use -----------------------------------------------------------------------------------------

const ROLES: { role: string; task: string; body: string }[] = [
  {
    role: "Site manager",
    task: "Count doors and windows for an order",
    body: "On a tablet at the site: ask for the count per apartment, then send the supplier the numbered plan with the order.",
  },
  {
    role: "Project engineer",
    task: "Check areas room by room",
    body: "Sum an apartment, leave the terrace out with one tap, and export the evidence as CSV for the cost plan.",
  },
]

const LANGS = [
  { code: "EN", q: "How many windows are drawn?", lang: "en" },
  { code: "FR", q: "Combien de fenêtres?", lang: "fr" },
  { code: "FI", q: "Montako ikkunaa?", lang: "fi" },
  { code: "DE", q: "Wie viele Fenster?", lang: "de" },
]

function Use() {
  return (
    <Section
      id="use"
      title="AURA for site and project teams"
      lead="Ask in English, French, Finnish or German. The answer comes back in the language you asked in."
    >
      <Reveal className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)]">
        {ROLES.map((r) => (
          <RevealItem key={r.role}>
            <Card className="h-full">
              <CardHeader>
                <CardDescription className="font-medium text-user">
                  {r.role}
                </CardDescription>
                <CardTitle className="text-lg font-semibold">
                  {r.task}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-sm text-pretty text-muted-foreground">
                {r.body}
              </CardContent>
            </Card>
          </RevealItem>
        ))}
        <RevealItem>
          <Card className="h-full">
            <CardContent>
              <ul
                aria-label="Questions in four languages"
                className="flex flex-col gap-2.5"
              >
                {LANGS.map((l) => (
                  <li key={l.code} className="flex items-center gap-3 text-sm">
                    <Badge variant="secondary" className="w-9 font-mono">
                      {l.code}
                    </Badge>
                    <span lang={l.lang} className="min-w-0 truncate">
                      {l.q}
                    </span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </RevealItem>
      </Reveal>

      <div className="flex flex-col gap-4">
        <h3 className="text-lg font-semibold">Plugs into other tools</h3>
        <Reveal as="ul" className="grid gap-4 md:grid-cols-3">
          {PLUGINS.map((p) => (
            <RevealItem as="li" key={p.name}>
              <Card className="h-full">
                <CardHeader>
                  <CardTitle className="font-mono font-semibold">
                    {p.name}
                  </CardTitle>
                  <Badge
                    variant={p.status === "ready" ? "secondary" : "outline"}
                    className={cn(
                      "col-start-2 row-start-1 justify-self-end",
                      p.status === "ready"
                        ? "bg-success-surface text-success"
                        : "text-muted-foreground"
                    )}
                  >
                    {p.status === "ready" ? "In the app" : "Being built"}
                  </Badge>
                </CardHeader>
                <CardContent className="text-sm text-pretty text-muted-foreground">
                  {p.body}
                </CardContent>
              </Card>
            </RevealItem>
          ))}
        </Reveal>
      </div>

      <Reveal>
        <RevealItem>
          <Card>
            <CardContent className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between sm:py-2">
              <div className="flex flex-col gap-1">
                <p className="text-xl font-semibold">
                  Open the sample drawing and ask it something.
                </p>
                <p className="text-sm text-muted-foreground">
                  A floor plan from the Swiss Dwellings dataset, ready to ask.
                  No account needed.
                </p>
              </div>
              <Button asChild className={PAGE_BTN}>
                <Link to={DEMO_ROUTE}>
                  Try it on the sample drawing
                  <ArrowRightIcon data-icon="inline-end" />
                </Link>
              </Button>
            </CardContent>
          </Card>
        </RevealItem>
      </Reveal>
    </Section>
  )
}
