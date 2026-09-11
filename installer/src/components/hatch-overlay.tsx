import type React from 'react'
interface HatchOverlayProps {
  active: boolean
}

export function HatchOverlay({ active }: HatchOverlayProps): React.JSX.Element | null {
  if (!active) return null

  return (
    <div
      className="pointer-events-none absolute -inset-8 rounded-full bg-radial from-amber-200/35 via-amber-100/10 to-transparent opacity-0 animate-warm-glow"
      aria-hidden="true"
    />
  )
}
