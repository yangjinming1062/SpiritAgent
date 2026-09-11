import type React from 'react'
import { useEffect, useState } from 'react'
import clsx from 'clsx'

export type EggPhase = 'idle' | 'cracking' | 'hatching' | 'hatched' | 'failed'

interface EggProps {
  cracks: number
  phase?: EggPhase
  size?: number
  className?: string
}

// 6 条预设裂纹 SVG 路径（向蛋中心辐射）
const CRACK_PATHS = [
  // 裂纹 1：左上裂缝
  'M 160 70 L 148 95 L 155 115 L 140 135',
  // 裂纹 2：右上分支
  'M 230 140 L 205 148 L 195 135 L 180 155',
  // 裂纹 3：左下分叉
  'M 90 210 L 115 200 L 125 215 L 145 195',
  // 裂纹 4：左中曲折
  'M 80 145 L 105 150 L 115 165 L 138 160',
  // 裂纹 5：右上深凹
  'M 175 60 L 180 88 L 168 105 L 175 125',
  // 裂纹 6：贯穿核心的中央裂缝
  'M 160 270 L 165 240 L 152 215 L 160 170'
]

export function Egg({
  cracks,
  phase = 'idle',
  size = 320,
  className
}: EggProps): React.JSX.Element {
  const [flashingIdx, setFlashingIdx] = useState<number | null>(null)

  useEffect(() => {
    if (cracks > 0) {
      setFlashingIdx(cracks - 1)
      const timer = setTimeout(() => setFlashingIdx(null), 600)
      return () => clearTimeout(timer)
    } else {
      setFlashingIdx(null)
    }
  }, [cracks])

  const visibleCracks = Math.min(6, Math.max(0, cracks))
  const isFailed = phase === 'failed'
  const isCracking = phase === 'cracking'
  const isHatching = phase === 'hatching'
  const isHatched = phase === 'hatched'

  return (
    <div
      className={clsx(
        'relative flex items-center justify-center select-none',
        isFailed && 'animate-egg-tremor',
        isCracking && !isFailed && 'scale-[1.01] transition-transform duration-300',
        className
      )}
      style={{ width: size, height: size }}
    >
      <svg
        viewBox="0 0 320 320"
        width={size}
        height={size}
        className="overflow-visible"
        aria-label="唤生-蛋"
      >
        <defs>
          {/* 环境氛围光：cyber-glass 蓝紫（--ui-accent #7d9bff）；蛋本身保留琥珀色，与冷调玻璃底色形成温暖锚点。 */}
          <radialGradient id="egg-ambient-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#7d9bff" stopOpacity="0.42" />
            <stop offset="60%" stopColor="#7d9bff" stopOpacity="0.14" />
            <stop offset="100%" stopColor="#7d9bff" stopOpacity="0" />
          </radialGradient>

          {/* 破壳时的核心暖光：蛋的"温暖"签名色——玻璃冷调外的情感锚点。 */}
          <radialGradient id="egg-core-light" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
            <stop offset="35%" stopColor="#fff4d6" stopOpacity="0.95" />
            <stop offset="70%" stopColor="#ffd166" stopOpacity="0.8" />
            <stop offset="100%" stopColor="#7d9bff" stopOpacity="0" />
          </radialGradient>

          {/* 裂纹辉光滤镜 */}
          <filter id="crack-glow-filter" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* 蛋后方的环境辉光 */}
        {!isHatched && (
          <ellipse
            cx="160"
            cy="170"
            rx="125"
            ry="135"
            fill="url(#egg-ambient-glow)"
            className={clsx(!isFailed && 'animate-egg-pulse')}
          />
        )}

        {/* 破壳时显形的核心暖光球 */}
        {(isHatching || isHatched) && (
          <g className="animate-warm-glow">
            <circle cx="160" cy="170" r="75" fill="url(#egg-core-light)" />
            <circle cx="160" cy="170" r="35" fill="#ffffff" opacity="0.9" />
          </g>
        )}

        {/* 主蛋壳 */}
        {!isHatched && (
          <g
            className={clsx(
              'transition-all duration-700 ease-out',
              isHatching && 'scale-125 opacity-0'
            )}
            style={{ transformOrigin: '160px 170px' }}
          >
            {/* 外壳路径 */}
            <path
              d="M 160 50 C 95 50 75 135 75 185 C 75 245 112 290 160 290 C 208 290 245 245 245 185 C 245 135 225 50 160 50 Z"
              fill="var(--color-egg-shell, #fff4d6)"
              stroke="color-mix(in srgb, var(--color-foreground, #17171a) 12%, transparent)"
              strokeWidth="2"
            />

            {/* 渲染裂纹 */}
            {CRACK_PATHS.slice(0, visibleCracks).map((pathD, idx) => {
              const isFlashing = flashingIdx === idx
              const strokeColor = isFailed
                ? 'var(--color-destructive, #cf2d56)'
                : isFlashing
                  ? 'var(--color-crack-glow, #ffd166)'
                  : 'color-mix(in srgb, var(--color-foreground, #17171a) 55%, #ffd166)'

              return (
                <path
                  key={idx}
                  d={pathD}
                  fill="none"
                  stroke={strokeColor}
                  strokeWidth={isFlashing ? 4 : 2.5}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  filter={isFailed ? undefined : 'url(#crack-glow-filter)'}
                  className={clsx(
                    'transition-all duration-300',
                    isFlashing && 'animate-crack-flash'
                  )}
                />
              )
            })}
          </g>
        )}
      </svg>
    </div>
  )
}
