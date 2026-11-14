import { useUi } from "@/i18n/uiLang"
import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "react-router"
import { CloudOffIcon, LayersIcon } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import { StageTimeline } from "@/pages/upload/StageTimeline"
import { stageView } from "@/pages/upload/stages"
import { acceptAttr, uploadHandoff, useAccepted, useStoreMode, useUploads } from "@/pages/upload/useUploads"

export interface ReadyUploadSummary {
  id: string
  name: string
}

export interface UploadPanelProps {
  base: string | null
  onReadyUploadsChange?: (uploads: ReadyUploadSummary[]) => void
  onBundleUploads?: (uploadIds: string[]) => void
  onProjectsDiscovered?: () => void
  onOpenUpload?: (uploadId: string) => void
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
 * The library's upload card: a drop target and a short list of the user's uploads. Uploading itself happens on
 * /upload (files dropped here are handed over), where every file and PDF page shows its full progress.
 */
export function UploadPanel({
  base,
  onReadyUploadsChange,
  onBundleUploads,
  onProjectsDiscovered,
  onOpenUpload,
}: UploadPanelProps) {
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
          <Uploader
            base={base}
            onReadyUploadsChange={onReadyUploadsChange}
            onBundleUploads={onBundleUploads}
            onProjectsDiscovered={onProjectsDiscovered}
            onOpenUpload={onOpenUpload}
          />
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

function Uploader({
  base,
  onReadyUploadsChange,
  onBundleUploads,
  onProjectsDiscovered,
  onOpenUpload,
}: Omit<UploadPanelProps, "base"> & { base: string }) {
  const { tu } = useUi()
  const navigate = useNavigate()
  const mode = useStoreMode(base)
  const { rows } = useUploads(base, { onProjectsDiscovered, mode })
  const accepted = useAccepted(base)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const handOff = (files: FileList | null) => {
    const all = Array.from(files ?? [])
    if (!all.length) return
    uploadHandoff.files = all
    navigate("/upload")
  }

  const readyUploads = useMemo(
    () => rows.filter((r) => r.phase === "ready" && !r.leaving).map((r) => ({ id: r.id, name: r.name })),
    [rows]
  )
  const readySignature = readyUploads.map((u) => `${u.id}:${u.name}`).join("|")
  useEffect(() => {
    onReadyUploadsChange?.(readyUploads)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readySignature, onReadyUploadsChange])

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

      <UsageNote />

      {rows.length ? (
        <div className="flex flex-col gap-2">
          <Separator />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">{tu("your_uploads")}</h3>
            {onBundleUploads && readyUploads.length >= 2 ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => onBundleUploads(readyUploads.map((u) => u.id))}
                className="min-h-10 text-xs"
                data-bundle-uploads
              >
                <LayersIcon data-icon="inline-start" />
                {tu("proj_bundle_uploads")}
              </Button>
            ) : null}
          </div>
          <ul className="flex flex-col gap-1" aria-label={tu("your_uploads")}>
            {rows.map((r) => {
              const view = stageView(r.phase, r.status?.stage, { mode: r.status?.mode ?? mode ?? undefined, last: r.last, slow: r.slow })
              return (
                <li key={r.uid ?? r.id} data-upload={r.phase} className="flex flex-col gap-1.5 rounded-lg px-1 py-1.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <Link
                      to={`/upload/${encodeURIComponent(r.id)}`}
                      className="min-w-0 flex-1 truncate text-sm underline-offset-2 hover:underline"
                      title={r.name}
                    >
                      {r.name}
                    </Link>
                    {view.openable ? (
                      <Button variant="outline" asChild>
                        <Link to={`/d/${encodeURIComponent(r.id)}`} onClick={() => onOpenUpload?.(r.id)}>
                          {tu("open")}
                        </Link>
                      </Button>
                    ) : null}
                  </div>
                  <StageTimeline view={view} fraction={null} dense />
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
    </>
  )
}
