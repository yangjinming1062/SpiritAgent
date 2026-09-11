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

// Halo 走 cyber-glass 主题：默认段 --ui-accent (#7d9bff 蓝紫)，运行中段加重发光滤镜。
// 失败段换 destructive 红；未完成段用 ui-line-standard 蓝紫低饱和（之前是白底浅蓝，对玻璃暗底来说会糊掉）。
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
      // 注意：根 div 不写 `position`——让外部传入的 className（`absolute inset-0` 或其它）独占控制定位。
      // Tailwind v4 utility 按字母序输出 CSS，`relative` 会后于 `absolute` 定义并覆盖，导致 absolute 失效、容器左对齐。
      // SVG 自身 width/height = size 已填满根 div，所以也不需要 `flex items-center justify-center`。
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
          {/* 段落发光滤镜：halo-glow-filter 给完成段蓝色发光，halo-fail-fliter 给失败段红色发光 */}
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
