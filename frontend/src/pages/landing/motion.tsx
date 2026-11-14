import { useEffect, useRef, useState, type ReactNode } from "react"
import {
  animate,
  motion,
  useInView,
  useReducedMotion,
  type Variants,
} from "motion/react"
import { cn } from "@/lib/utils"
import { SPRING } from "./springs"

/**
 * Motion vocabulary for the landing pages (apple-motion): critically damped springs, no bounce,
 * transform and opacity only. Under reduced motion `Reveal` and `RevealItem` bypass `whileInView`
 * completely and `CountUp` reports completion immediately, so content is never left waiting on scroll.
 */
const group: Variants = {
  hidden: {},
  shown: { transition: { staggerChildren: 0.07, delayChildren: 0.05 } },
}
const item: Variants = {
  hidden: { opacity: 0, y: 18 },
  shown: { opacity: 1, y: 0, transition: SPRING },
}

/** A block that settles in once as it enters the viewport; children marked RevealItem stagger. */
export function Reveal({
  children,
  className,
  as = "div",
  amount = 0.2,
}: {
  children: ReactNode
  className?: string
  as?: "div" | "ul" | "ol" | "dl"
  amount?: number
}) {
  const reduce = useReducedMotion()
  if (reduce) {
    const Plain = as
    return <Plain className={className}>{children}</Plain>
  }
  const Tag = motion[as]
  return (
    <Tag
      className={className}
      variants={group}
      initial="hidden"
      whileInView="shown"
      viewport={{ once: true, amount }}
    >
      {children}
    </Tag>
  )
}

export function RevealItem({
  children,
  className,
  as = "div",
}: {
  children: ReactNode
  className?: string
  as?: "div" | "li"
}) {
  const reduce = useReducedMotion()
  if (reduce) {
    const Plain = as
    return <Plain className={cn("min-w-0", className)}>{children}</Plain>
  }
  const Tag = motion[as]
  return (
    <Tag className={cn("min-w-0", className)} variants={item}>
      {children}
    </Tag>
  )
}

/**
 * Counts up to a measured value once it is on screen, in the value's own format ("99.0 %", "$0.0026").
 * The resting text is always exactly `value`; screen readers get the value, never the ticking.
 */
export function CountUp({
  value,
  className,
  onProgress,
}: {
  value: string
  className?: string
  onProgress?: (t: number) => void
}) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, amount: 0.25 })
  const reduce = useReducedMotion()
  const m = /^(\D*)(\d[\d,]*(?:\.\d+)?)(.*)$/.exec(value)
  const [text, setText] = useState(() =>
    m && !reduce ? `${m[1]}${fmt(0, m[2])}${m[3]}` : value
  )

  useEffect(() => {
    if (!m || reduce) {
      onProgress?.(1)
      return
    }
    if (!inView) return
    const target = Number(m[2].replace(/,/g, ""))
    const controls = animate(0, target, {
      type: "spring",
      bounce: 0,
      duration: 1.4,
      onUpdate: (v) => {
        setText(`${m[1]}${fmt(v, m[2])}${m[3]}`)
        onProgress?.(target ? v / target : 1)
      },
      onComplete: () => {
        setText(value)
        onProgress?.(1)
      },
    })
    return () => controls.stop()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once when it comes into view or reduced motion activates
  }, [inView, reduce, value])

  const shownText = !m || reduce ? value : text

  return (
    <span ref={ref} className={className}>
      <span aria-hidden>{shownText}</span>
      <span className="sr-only">{value}</span>
    </span>
  )
}

/** Formats `v` with the decimals and thousands separators of the template number. */
function fmt(v: number, template: string) {
  const dec = template.includes(".") ? template.split(".")[1].length : 0
  const s = v.toFixed(dec)
  if (!template.includes(",")) return s
  const [i, d] = s.split(".")
  return i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (d ? `.${d}` : "")
}
