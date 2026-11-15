import { useUi } from "@/i18n/uiLang"
import { Link } from "react-router"
import { ArrowRightIcon, CloudOffIcon } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"

export interface UploadPanelProps {
  base: string | null
  /** Uploads still being sent or parsed (finished ones are cards in the library). */
  uploading?: number
}

function UsageNote() {
  const { tu } = useUi()
  return (
    <p className="text-xs text-muted-foreground">
      {tu("up_usage")}{" "}
      <Link to="/privacy" className="font-medium text-foreground underline underline-offset-2">
        {tu("nav_privacy")}
      </Link>
    </p>
  )
}

/**
 * The library's upload card: a link to /upload (uploads happen only there), and while something is in progress a
 * compact "N uploading" line. Finished uploads are library cards, not a list here.
 */
export function UploadPanel({ base, uploading = 0 }: UploadPanelProps) {
  const { tu } = useUi()
  return (
    <Card size="sm" role="region" aria-labelledby="upload-title">
      <CardHeader>
        <CardTitle>
          <h2 id="upload-title">{tu("upload_title")}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {base ? (
          <Uploader uploading={uploading} />
        ) : (
          <>
            <Alert data-uploads-off>
              <CloudOffIcon />
              <AlertTitle>{tu("up_off_title")}</AlertTitle>
              <AlertDescription>{tu("up_off_desc")}</AlertDescription>
            </Alert>
            <UsageNote />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Uploader({ uploading }: { uploading: number }) {
  const { tu } = useUi()
  return (
    <>
      <p className="text-sm text-muted-foreground">{tu("ul_lib_desc")}</p>
      <Button asChild>
        <Link to="/upload" data-upload-link>
          {tu("upload_title")}
          <ArrowRightIcon data-icon="inline-end" aria-hidden />
        </Link>
      </Button>

      {uploading > 0 ? (
        <Link
          to="/upload"
          className="flex min-h-10 items-center gap-2 rounded-lg bg-muted/60 px-3 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
          data-uploading={uploading}
        >
          <Spinner className="size-4 text-muted-foreground" />
          <span className="flex-1">{tu("lib_uploading", { n: uploading })}</span>
          <ArrowRightIcon className="size-4 text-muted-foreground" aria-hidden />
        </Link>
      ) : null}

      <UsageNote />
    </>
  )
}
