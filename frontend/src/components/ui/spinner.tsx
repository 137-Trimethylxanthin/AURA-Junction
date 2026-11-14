import * as React from "react"
import { Loader2Icon } from "lucide-react"
import { cn } from "@/lib/utils"

function Spinner({ className, ...props }: React.ComponentProps<"svg">) {
  const hidden = Boolean(props["aria-hidden"])
  return (
    <Loader2Icon
      data-slot="spinner"
      role={hidden ? undefined : "status"}
      aria-label={hidden ? undefined : "Loading"}
      className={cn(
        "size-4 shrink-0 motion-safe:animate-spin motion-reduce:animate-[aura-pulse_1.6s_ease-in-out_infinite] forced-colors:text-[CanvasText]",
        className
      )}
      {...props}
    />
  )
}

export { Spinner }
