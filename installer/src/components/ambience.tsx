import type React from 'react'

export type AmbienceTone = 'calm' | 'active' | 'bloom' | 'dim'

interface AmbienceProps {
  tone: AmbienceTone
  rise: number
}

// 全窗光层：极光只负责氛围，光面高度由真实阶段数决定。
export function Ambience({ tone, rise }: AmbienceProps): React.JSX.Element {
  const pct = Math.round(Math.min(1, Math.max(0, rise)) * 100)

  return (
    <div aria-hidden="true" className={`sa-ambience sa-ambience-${tone}`}>
      <span className="sa-beam sa-beam-a" />
      <span className="sa-beam sa-beam-b" />
      <span className="sa-beam sa-beam-c" />
      <span className="sa-streak sa-streak-a" />
      <span className="sa-streak sa-streak-b" />
      <div className="sa-rise" style={{ height: `${pct}%` }}>
        <span className="sa-rise-front" />
      </div>
    </div>
  )
}
