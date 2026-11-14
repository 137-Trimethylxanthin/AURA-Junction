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

export default function NotFound() {
  useTitle("Page not found")
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
              <h1>Page not found</h1>
            </EmptyTitle>
            <EmptyDescription>
              There is nothing at this address. The drawing list and the sample
              drawing are one click away.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent className="flex-row justify-center gap-2">
            <Button asChild>
              <Link to="/app">All drawings</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to={`/d/${BUNDLED_DRAWING_ID}`}>
                Open the sample drawing
              </Link>
            </Button>
          </EmptyContent>
        </Empty>
      </main>
    </div>
  )
}
