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
import NotFound from "./NotFound"

/** Root error boundary: a failed page load (e.g. a chunk that did not download) or a render error. */
export function RouteError() {
  const error = useRouteError()
  useTitle("Something went wrong")
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
                {chunk
                  ? "This page did not finish loading"
                  : "Something went wrong"}
              </h1>
            </EmptyTitle>
            <EmptyDescription>
              {chunk
                ? "Part of the app could not be downloaded. Check the connection and reload."
                : "The page hit an error. Reloading usually fixes it; your uploads are kept."}
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent className="flex-row justify-center gap-2">
            <Button onClick={() => window.location.reload()}>
              <RotateCwIcon data-icon="inline-start" />
              Reload
            </Button>
            <Button variant="outline" asChild>
              <Link to="/app" reloadDocument>
                All drawings
              </Link>
            </Button>
          </EmptyContent>
        </Empty>
      </main>
    </div>
  )
}
