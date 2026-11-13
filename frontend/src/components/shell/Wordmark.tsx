/** Two-room plan outline; the right room is filled in the evidence colour with a proof dot. */
function AuraMark() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      className="size-6 shrink-0 text-foreground"
      fill="none"
    >
      <path d="M13 4h5.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H13z" className="fill-info" />
      <path
        d="M5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4zM13 4v6.5M13 14.5V20"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="16.5" cy="12" r="2" className="fill-background" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div
      className="flex shrink-0 items-center gap-2"
      title="Automated Understanding of Rooms & Areas"
    >
      <AuraMark />
      <span className="text-sm font-semibold tracking-[0.08em]">AURA</span>
      <span className="hidden text-xs text-muted-foreground min-[1280px]:inline">
        Automated Understanding of Rooms &amp; Areas
      </span>
    </div>
  )
}
