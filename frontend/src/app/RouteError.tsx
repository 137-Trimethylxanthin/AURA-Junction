import { isRouteErrorResponse, Link, useRouteError } from "react-router"
import { AlertTriangleIcon, RotateCwIcon } from "lucide-react"
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
import { useUi } from "@/i18n/uiLang"
import NotFound from "./NotFound"

/** Root error boundary: a failed page load (e.g. a chunk that did not download) or a render error. */
export function RouteError() {
  const error = useRouteError()
  const { tu } = useUi()
  useTitle(tu("err_title"))
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFound />
  const chunk =
    error instanceof Error &&
    /dynamically imported module|Importing a module script failed|error loading/i.test(
      error.message
    )
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <main className="flex flex-1 items-center justify-center p-6">
        <Empty data-route-error>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <AlertTriangleIcon />
            </EmptyMedia>
            <EmptyTitle>
              <h1>
                {chunk ? tu("err_chunk_title") : tu("err_title")}
              </h1>
            </EmptyTitle>
            <EmptyDescription>
              {chunk ? tu("err_chunk_desc") : tu("err_desc")}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent className="flex-row justify-center gap-2">
            <Button onClick={() => window.location.reload()}>
              <RotateCwIcon data-icon="inline-start" />
              {tu("reload")}
            </Button>
            <Button variant="outline" asChild>
              <Link to="/app" reloadDocument>
                {tu("all_drawings")}
              </Link>
            </Button>
          </EmptyContent>
        </Empty>
      </main>
    </div>
  )
}
