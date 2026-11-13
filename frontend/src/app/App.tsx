import { useEffect } from "react"
import { AppShell } from "@/components/shell"
import { Toaster } from "@/components/ui/sonner"
import { useMediaQuery } from "@/hooks/use-media-query"
import { ChatPanel, Composer, DrawingTitle, HeaderActions, SidebarOverlay } from "@/features/chat"
import { Credit, PlanStage, PlanToolbar } from "@/features/plan"
import { actions } from "@/store"

export function App() {
  const narrow = useMediaQuery("(max-width: 899px)")
  useEffect(() => {
    void actions().initSource()
  }, [])
  return (
    <>
      <AppShell
        title={<DrawingTitle />}
        headerActions={<HeaderActions />}
        toolbar={<PlanToolbar />}
        stage={<PlanStage />}
        credit={<Credit />}
        chat={<ChatPanel />}
        composer={<Composer />}
        sidebarOverlay={<SidebarOverlay />}
      />
      <Toaster position={narrow ? "bottom-center" : "bottom-right"} />
    </>
  )
}

export default App
