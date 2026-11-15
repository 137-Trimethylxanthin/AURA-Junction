import { createBrowserRouter, redirect, type RouteObject } from "react-router"
import { sampleDrawingId } from "@/api/sample"
import { RootLayout, RootFallback } from "./RootLayout"
import { RouteError } from "./RouteError"
import NotFound from "./NotFound"

/** Lazy route helper: the page module's default export becomes the route component. */
const page =
  (load: () => Promise<{ default: React.ComponentType }>) => async () => ({
    Component: (await load()).default,
  })

export const routes: RouteObject[] = [
  {
    path: "/",
    Component: RootLayout,
    HydrateFallback: RootFallback,
    errorElement: <RouteError />,
    children: [
      // L4 owns these three pages; they are mounted here by path.
      { index: true, lazy: page(() => import("@/pages/landing/LandingPage")) },
      {
        path: "credits",
        lazy: page(() => import("@/pages/credits/CreditsPage")),
      },
      {
        path: "privacy",
        lazy: page(() => import("@/pages/privacy/PrivacyPage")),
      },
      { path: "app", lazy: page(() => import("@/pages/library/LibraryPage")) },
      { path: "upload", lazy: page(() => import("@/pages/upload/UploadPage")) },
      { path: "upload/:drawingId", lazy: page(() => import("@/pages/upload/UploadPage")) },
      { path: "d/:drawingId", lazy: page(() => import("./Workspace")) },
      // Old and short links keep working.
      { path: "d", loader: () => redirect("/app") },
      { path: "demo", loader: async () => redirect(`/d/${encodeURIComponent(await sampleDrawingId())}`) },
      { path: "*", Component: NotFound },
    ],
  },
]

export const createRouter = () => createBrowserRouter(routes)
