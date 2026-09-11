import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { $spriteState } from '@/companion/companion-store'

type FootGlowPulse = 'completed' | 'failed' | null

const $footGlowPulse = atom<FootGlowPulse>(null)

let pulseTimer: ReturnType<typeof setTimeout> | null = null

export function triggerFootGlowPulse(pulse: 'completed' | 'failed', durationMs = 1200): void {
  if (pulseTimer) {
    clearTimeout(pulseTimer)
  }

  $footGlowPulse.set(pulse)

  pulseTimer = setTimeout(() => {
    pulseTimer = null
    $footGlowPulse.set(null)
  }, durationMs)
}

export function FootGlow(): React.JSX.Element {
  const state = useStore($spriteState)
  const pulse = useStore($footGlowPulse)

  return <div aria-hidden="true" className="foot-glow" data-pulse={pulse ?? undefined} data-state={state} />
}
