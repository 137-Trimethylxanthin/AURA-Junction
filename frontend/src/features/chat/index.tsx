import { Suspense, lazy } from "react"
import { AnimatePresence } from "motion/react"
import { useAppStore } from "@/store"

/** Loaded the first time the reasoning tree opens. */
const TracePanel = lazy(() =>
  import("@/features/trace/TracePanel").then((m) => ({ default: m.TracePanel }))
)

export { ChatPanel } from "./ChatPanel"
export { Composer } from "./Composer"
export { DrawingTitle, HeaderActions } from "./Header"

/** Reasoning tree over the chat region; renders nothing once closed (after its exit) so the shell hides its wrapper. */
export function SidebarOverlay() {
  const open = useAppStore((s) => s.ui.traceOpen)
  return (
    <AnimatePresence>
      {open ? (
        <Suspense key="trace" fallback={null}>
          <TracePanel />
        </Suspense>
      ) : null}
    </AnimatePresence>
  )
}
