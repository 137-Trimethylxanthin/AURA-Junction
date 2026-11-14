export { cn } from "cn"

/** True when the drawing has an original that differs from the plan shown (else "Compare" would show one image twice). */
export function hasDistinctOriginal(images: { rebuilt: string; original?: string }): boolean {
  if (!images.original) return false
  const bare = (u: string) => u.split('?')[0]
  return bare(images.original) !== bare(images.rebuilt)
}
