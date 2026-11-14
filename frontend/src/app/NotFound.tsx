import { Link } from "react-router"
import { MapPinOffIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { SiteHeader } from "@/components/shell/SiteHeader"
import { useTitle } from "@/hooks/use-title"
import { BUNDLED_DRAWING_ID } from "@/data/ids"
import { useUi } from "@/i18n/uiLang"

export default function NotFound() {
  const { tu } = useUi()
  useTitle(tu("nf_title"))
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main className="flex flex-1 items-center justify-center p-6">
        <Empty data-not-found>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <MapPinOffIcon />
            </EmptyMedia>
            <EmptyTitle>
              <h1>{tu("nf_title")}</h1>
            </EmptyTitle>
            <EmptyDescription>
              {tu("nf_desc")}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent className="flex-row justify-center gap-2">
            <Button asChild>
              <Link to="/app">{tu("all_drawings")}</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to={`/d/${BUNDLED_DRAWING_ID}`}>
                {tu("open_sample")}
              </Link>
            </Button>
          </EmptyContent>
        </Empty>
      </main>
    </div>
  )
}
