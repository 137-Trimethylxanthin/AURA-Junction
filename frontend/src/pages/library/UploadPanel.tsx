import { useUi } from "@/i18n/uiLang"
import { useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { ArrowRightIcon, CloudOffIcon } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Spinner } from "@/components/ui/spinner"
import { cn } from "@/lib/utils"
import { acceptAttr, uploadHandoff, useAccepted } from "@/pages/upload/useUploads"

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
 * The library's upload card: a drop target that hands files to /upload, a link there, and while something is in
 * progress a compact "N uploading" line. Finished uploads are library cards, not a list here.
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
          <Uploader base={base} uploading={uploading} />
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

function Uploader({ base, uploading }: { base: string; uploading: number }) {
  const { tu } = useUi()
  const navigate = useNavigate()
  const accepted = useAccepted(base)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const handOff = (files: FileList | null) => {
    const all = Array.from(files ?? [])
    if (!all.length) return
    uploadHandoff.files = all
    navigate("/upload")
  }

  return (
    <>
      <div
        data-dropzone
        data-over={over || undefined}
        onDragOver={(e) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = "copy"
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          handOff(e.dataTransfer.files)
        }}
        className={cn(
          "flex flex-col items-center gap-3 rounded-lg border border-dashed border-input px-4 py-6 text-center transition-colors",
          over && "border-foreground bg-muted"
        )}
      >
        <p className="text-sm text-muted-foreground">{tu("ul_lib_desc")}</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button onClick={() => input.current?.click()}>{tu("choose_file")}</Button>
          <Button variant="outline" asChild>
            <Link to="/upload">{tu("ul_lib_details")}</Link>
          </Button>
        </div>
        <input
          ref={input}
          type="file"
          multiple
          accept={acceptAttr(accepted)}
          className="sr-only"
          tabIndex={-1}
          aria-label={tu("up_file_aria")}
          data-upload-input
          onChange={(e) => {
            handOff(e.currentTarget.files)
            e.currentTarget.value = ""
          }}
        />
      </div>

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
