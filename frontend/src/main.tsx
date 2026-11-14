import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router"

import "./index.css"
import { createRouter } from "@/app/router"
import { myUploads, type StoredUpload } from "@/api/library"

// Dev only: uploads made by scripts (backend/scripts/upload_matrix.py) are listed in a local, git-ignored
// public/dev-uploads.json so they show up in this browser's library. Never part of a production build.
if (import.meta.env.DEV) {
  void fetch("/dev-uploads.json")
    .then((r) => (r.ok ? r.json() : []))
    .then((list: unknown) => {
      if (!Array.isArray(list)) return
      const known = new Set(myUploads.list().map((u) => u.id))
      for (const u of list as StoredUpload[]) if (u?.id && u.delete_token && !known.has(u.id)) myUploads.add(u)
    })
    .catch(() => {})
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={createRouter()} />
  </StrictMode>
)
