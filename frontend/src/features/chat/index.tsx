import { AnimatePresence } from 'motion/react'
import { useAppStore } from '@/store'
import { TracePanel } from '@/features/trace/TracePanel'

export { ChatPanel } from './ChatPanel'
export { Composer } from './Composer'
export { DrawingTitle, HeaderActions } from './Header'

/** Reasoning tree over the chat region; renders nothing once closed (after its exit) so the shell hides its wrapper. */
export function SidebarOverlay() {
  const open = useAppStore((s) => s.ui.traceOpen)
  return <AnimatePresence>{open ? <TracePanel key="trace" /> : null}</AnimatePresence>
}
