import type React from 'react'
import clsx from 'clsx'

interface HaloProps {
  total?: number
  done: number
  runningIdx?: number | null
  failedAt?: number | null
  size?: number
  className?: string
}

function polarToCartesian(
  centerX: number,
  centerY: number,
  radius: number,
  angleInDegrees: number
) {
  const angleInRadians = ((angleInDegrees - 90) * Math.PI) / 180.0
  return {
    x: centerX + radius * Math.cos(angleInRadians),
    y: centerY + radius * Math.sin(angleInRadians)
  }
}

function describeArc(
  x: number,
  y: number,
  radius: number,
  startAngle: number,
  endAngle: number
) {
  const start = polarToCartesian(x, y, radius, endAngle)
  const end = polarToCartesian(x, y, radius, startAngle)
  const largeArcFlag = endAngle - startAngle <= 180 ? '0' : '1'

  return [
    'M',
    start.x,
    start.y,
    'A',
    radius,
    radius,
    0,
    largeArcFlag,
    0,
    end.x,
    end.y
  ].join(' ')
}

export function Halo({
  total = 6,
  done,
  runningIdx = null,
  failedAt = null,
  size = 360,
  className
}: HaloProps): React.JSX.Element {
  const segments = Array.from({ length: total }, (_, i) => i)
  const segmentAngle = 360 / total
  const gapAngle = 4

  return (
    <div
      // 不写 position：Tailwind v4 下 relative 会覆盖外部 className 的 absolute
      className={clsx('select-none', className)}
      style={{ width: size, height: size }}
    >
      <svg
        viewBox="0 0 360 360"
        width={size}
        height={size}
        className="overflow-visible"
        aria-label="Installer Stage Halo"
      >
        <defs>
          <filter id="halo-glow-filter" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {segments.map((idx) => {
          const startAngle = idx * segmentAngle + gapAngle / 2
          const endAngle = (idx + 1) * segmentAngle - gapAngle / 2
          const d = describeArc(180, 180, 155, startAngle, endAngle)

          const isCompleted = idx < done
          const isRunning = idx === runningIdx
          const isFailed = failedAt === idx

          let strokeColor = 'var(--ui-line-standard)'
          let filter: string | undefined

          if (isFailed) {
            strokeColor = 'var(--color-destructive, #cf2d56)'
            filter = 'url(#halo-glow-filter)'
          } else if (isCompleted) {
            strokeColor = 'var(--ui-accent, #7d9bff)'
            filter = 'url(#halo-glow-filter)'
          } else if (isRunning) {
            strokeColor = 'var(--ui-accent, #7d9bff)'
          }

          return (
            <path
              key={idx}
              d={d}
              fill="none"
              stroke={strokeColor}
              strokeWidth={isRunning ? 12 : 10}
              strokeLinecap="round"
              filter={filter}
              className={clsx(
                'transition-all duration-500 ease-out',
                isRunning && 'animate-pulse opacity-90',
                !isCompleted && !isRunning && 'opacity-40'
              )}
            />
          )
        })}
      </svg>
    </div>
  )
}
