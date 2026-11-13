import type { ReactNode } from "react"
import { cn } from "@/lib/utils"
import { WRAP } from "./chrome"

/** Text page layout shared by /credits and /privacy: one narrow reading column. */
export function ProsePage({
  title,
  lead,
  updated,
  children,
}: {
  title: string
  lead: ReactNode
  updated: string
  children: ReactNode
}) {
  return (
    <div className={cn(WRAP, "py-12 sm:py-16 lg:py-20")}>
      <div className="mx-auto flex max-w-[44rem] flex-col gap-10">
        <header className="l-enter flex flex-col gap-3 border-b pb-8">
          <h1 className="text-4xl leading-tight font-semibold tracking-[-0.02em] sm:text-5xl">
            {title}
          </h1>
          <div className="text-lg text-pretty text-muted-foreground">
            {lead}
          </div>
          <p className="num text-xs text-muted-foreground">
            Last updated {updated}
          </p>
        </header>
        <div
          className="l-enter flex flex-col gap-10"
          style={{ ["--d" as string]: "80ms" }}
        >
          {children}
        </div>
      </div>
    </div>
  )
}

export function ProseSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  const id = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-xl font-semibold tracking-[-0.01em]">
        {title}
      </h2>
      <div className="flex flex-col gap-3 text-base text-pretty [&_a]:text-info [&_a]:underline [&_a]:decoration-info/40 [&_a]:underline-offset-4 [&_a:hover]:decoration-info [&_li]:pl-1">
        {children}
      </div>
    </section>
  )
}

/** A term and its value, for licence and dataset rows. */
export function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1 rounded-xl border bg-card p-5 text-sm sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-y-2">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="pt-2 text-muted-foreground first:pt-0 sm:pt-0">{k}</dt>
          <dd className="min-w-0 [overflow-wrap:anywhere]">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
