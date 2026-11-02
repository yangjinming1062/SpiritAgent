import type React from 'react'
import clsx from 'clsx'

interface HaloProps {
  total: number
  done: number
  runningIdx: number | null
  failedAt: number | null
  size: number
  className?: string
}

const CENTER = 180
const RADIUS = 150
const GAP = 9

function polar(angle: number): { x: number; y: number } {
  const rad = ((angle - 90) * Math.PI) / 180
  return {
    x: CENTER + RADIUS * Math.cos(rad),
    y: CENTER + RADIUS * Math.sin(rad)
  }
}

function arcPath(startAngle: number, endAngle: number): string {
  const start = polar(startAngle)
  const end = polar(endAngle)
  const large = endAngle - startAngle > 180 ? 1 : 0
  return [
    'M',
    start.x.toFixed(2),
    start.y.toFixed(2),
    'A',
    RADIUS,
    RADIUS,
    0,
    large,
    1,
    end.x.toFixed(2),
    end.y.toFixed(2)
  ].join(' ')
}

// 光轨：每段按真实阶段点亮，完成段由 sa-ring-fill 从起点灌满。
export function Halo({
  total,
  done,
  runningIdx,
  failedAt,
  size,
  className
}: HaloProps): React.JSX.Element {
  const segments = Array.from({ length: total }, (_, i) => i)
  const step = 360 / total

  return (
    <div className={clsx('select-none', className)} style={{ width: size, height: size }}>
      <svg
        viewBox="0 0 360 360"
        width={size}
        height={size}
        className="overflow-visible"
        aria-hidden="true"
      >
        <defs>
          <linearGradient
            id="sa-arc-grad"
            gradientUnits="userSpaceOnUse"
            x1="40"
            y1="30"
            x2="320"
            y2="330"
          >
            <stop offset="0%" style={{ stopColor: 'var(--sa-arc-1)' }} />
            <stop offset="45%" style={{ stopColor: 'var(--sa-arc-2)' }} />
            <stop offset="100%" style={{ stopColor: 'var(--sa-arc-3)' }} />
          </linearGradient>
          <radialGradient id="sa-head-glow">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
            <stop offset="42%" stopColor="#9db6ff" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#7d9bff" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="sa-head-danger">
            <stop offset="0%" stopColor="#ffe3ea" stopOpacity="0.95" />
            <stop offset="45%" stopColor="#ff7b9c" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#cf2d56" stopOpacity="0" />
          </radialGradient>
        </defs>

        {segments.map((idx) => {
          const start = idx * step + GAP / 2
          const end = (idx + 1) * step - GAP / 2
          const d = arcPath(start, end)
          const isDone = idx < done
          const isRunning = runningIdx === idx
          const isFailed = failedAt === idx
          const head = polar(start)

          return (
            <g key={idx}>
              {!isDone && (
                <path
                  d={d}
                  fill="none"
                  stroke="rgba(139, 158, 255, 0.26)"
                  strokeWidth={isRunning ? 10 : 7}
                  strokeLinecap="round"
                  opacity={isFailed ? 0.25 : isRunning ? 0.85 : 0.55}
                />
              )}

              {isDone && (
                <>
                  <path
                    d={d}
                    fill="none"
                    stroke="rgba(139, 158, 255, 0.12)"
                    strokeWidth={10}
                    strokeLinecap="round"
                  />
                  <path
                    className="sa-arc-fill"
                    d={d}
                    pathLength={100}
                    fill="none"
                    stroke="url(#sa-arc-grad)"
                    strokeWidth={24}
                    strokeLinecap="round"
                    opacity={0.18}
                  />
                  <path
                    className="sa-arc-fill"
                    d={d}
                    pathLength={100}
                    fill="none"
                    stroke="url(#sa-arc-grad)"
                    strokeWidth={9.5}
                    strokeLinecap="round"
                  />
                  <path
                    className="sa-arc-fill"
                    d={d}
                    pathLength={100}
                    fill="none"
                    stroke="var(--ui-stream-core)"
                    strokeWidth={2.4}
                    strokeLinecap="round"
                    opacity={0.6}
                  />
                  <path
                    className="sa-arc-sheen"
                    d={d}
                    pathLength={100}
                    fill="none"
                    stroke="#ffffff"
                    strokeWidth={5}
                    strokeLinecap="round"
                    strokeDasharray="7 93"
                  />
                </>
              )}

              {isRunning && (
                <>
                  <path
                    className="sa-arc-comet"
                    d={d}
                    pathLength={100}
                    fill="none"
                    stroke="url(#sa-arc-grad)"
                    strokeWidth={10}
                    strokeLinecap="round"
                    strokeDasharray="12 88"
                  />
                  <circle className="sa-head" cx={head.x} cy={head.y} r={9} fill="url(#sa-head-glow)" />
                </>
              )}

              {isFailed && (
                <>
                  <path
                    d={d}
                    fill="none"
                    stroke="rgba(207, 45, 86, 0.2)"
                    strokeWidth={24}
                    strokeLinecap="round"
                  />
                  <path d={d} fill="none" stroke="#cf2d56" strokeWidth={9.5} strokeLinecap="round" />
                  <path
                    d={d}
                    fill="none"
                    stroke="#ffd7e0"
                    strokeWidth={2.4}
                    strokeLinecap="round"
                    opacity={0.75}
                  />
                  <circle
                    className="sa-head"
                    cx={head.x}
                    cy={head.y}
                    r={9}
                    fill="url(#sa-head-danger)"
                  />
                </>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
