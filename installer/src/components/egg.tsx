import type React from 'react'
import { useEffect, useState } from 'react'
import clsx from 'clsx'

export type EggPhase = 'idle' | 'cracking' | 'hatching' | 'failed'

export const EGG_PATH =
  'M 160 50 C 95 50 75 135 75 185 C 75 245 112 290 160 290 C 208 290 245 245 245 185 C 245 135 225 50 160 50 Z'

// 6 条预设裂纹 SVG 路径（向蛋中心辐射）
const CRACK_PATHS = [
  'M 160 70 L 148 95 L 155 115 L 140 135',
  'M 230 140 L 205 148 L 195 135 L 180 155',
  'M 90 210 L 115 200 L 125 215 L 145 195',
  'M 80 145 L 105 150 L 115 165 L 138 160',
  'M 175 60 L 180 88 L 168 105 L 175 125',
  'M 160 270 L 165 240 L 152 215 L 160 170'
]

interface EggProps {
  cracks: number
  progress: number
  phase: EggPhase
  size?: number
}

export function Egg({ cracks, progress, phase = 'idle', size = 320 }: EggProps): React.JSX.Element {
  const [flashingIdx, setFlashingIdx] = useState<number | null>(null)

  useEffect(() => {
    if (cracks > 0) {
      setFlashingIdx(cracks - 1)
      const timer = setTimeout(() => setFlashingIdx(null), 600)
      return () => clearTimeout(timer)
    }
    setFlashingIdx(null)
    return undefined
  }, [cracks])

  const visibleCracks = Math.min(6, Math.max(0, cracks))
  const pct = Math.min(1, Math.max(0, progress))
  const innerOpacity = (0.06 + 0.2 * pct).toFixed(3)
  const hotOpacity = (0.2 + 0.55 * pct).toFixed(3)
  const leakOpacity = (0.1 + 0.34 * pct).toFixed(3)
  const isFailed = phase === 'failed'
  const isCracking = phase === 'cracking'
  const isHatching = phase === 'hatching'

  return (
    <div
      className={clsx(
        'relative flex items-center justify-center select-none',
        isFailed && 'animate-egg-tremor',
        isCracking && 'scale-[1.01] transition-transform duration-300'
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
          <radialGradient id="egg-ambient-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#7d9bff" stopOpacity="0.42" />
            <stop offset="60%" stopColor="#7d9bff" stopOpacity="0.14" />
            <stop offset="100%" stopColor="#7d9bff" stopOpacity="0" />
          </radialGradient>

          <radialGradient id="egg-core-light" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
            <stop offset="35%" stopColor="#fff4d6" stopOpacity="0.95" />
            <stop offset="70%" stopColor="#ffd166" stopOpacity="0.8" />
            <stop offset="100%" stopColor="#7d9bff" stopOpacity="0" />
          </radialGradient>

          <linearGradient id="sa-egg-shell" x1="0.1" y1="0" x2="0.5" y2="1">
            <stop offset="0%" stopColor="#c6d2ff" stopOpacity="0.3" />
            <stop offset="48%" stopColor="#7e96eb" stopOpacity="0.14" />
            <stop offset="100%" stopColor="#5c70c8" stopOpacity="0.14" />
          </linearGradient>

          <radialGradient id="sa-egg-pool" cx="50%" cy="92%" r="48%">
            <stop offset="0%" stopColor="#ffe9b8" stopOpacity="0.8" />
            <stop offset="50%" stopColor="#ffb85c" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#ff9f43" stopOpacity="0" />
          </radialGradient>

          <radialGradient id="sa-egg-hot" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fff3d6" stopOpacity="0.9" />
            <stop offset="45%" stopColor="#ffb85c" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#ff9f43" stopOpacity="0" />
          </radialGradient>

          <radialGradient id="sa-egg-leak" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffcf8a" stopOpacity="0.55" />
            <stop offset="55%" stopColor="#ffb86b" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#ff9f5a" stopOpacity="0" />
          </radialGradient>

          <radialGradient id="sa-hatch-core" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.98" />
            <stop offset="45%" stopColor="#fff4d6" stopOpacity="0.8" />
            <stop offset="100%" stopColor="#ffd166" stopOpacity="0" />
          </radialGradient>

          <clipPath id="sa-egg-clip">
            <path d={EGG_PATH} />
          </clipPath>

          <filter id="crack-glow-filter" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <ellipse
          cx="160"
          cy="170"
          rx="125"
          ry="135"
          fill="url(#egg-ambient-glow)"
          className={clsx(
            'transition-opacity duration-700',
            !isFailed && !isHatching && 'animate-egg-pulse',
            isHatching && 'opacity-0'
          )}
        />

        {isHatching && (
          <g className="animate-warm-glow">
            <circle cx="160" cy="170" r="75" fill="url(#egg-core-light)" />
            <circle cx="160" cy="170" r="52" fill="url(#sa-hatch-core)" />
          </g>
        )}

        <g
          className={clsx(
            'transition-all duration-700 ease-out',
            isHatching && 'scale-125 opacity-0'
          )}
          style={{ transformOrigin: '160px 170px' }}
        >
          <ellipse
            cx="160"
            cy="280"
            rx="132"
            ry="76"
            fill="url(#sa-egg-leak)"
            style={{
              opacity: `calc(${leakOpacity} * var(--sa-egg-light, 1))`,
              transition: 'opacity 700ms ease-out'
            }}
          />

          <path
            d={EGG_PATH}
            fill="url(#sa-egg-shell)"
            stroke="rgba(198, 210, 255, 0.34)"
            strokeWidth="1.5"
          />

          <g clipPath="url(#sa-egg-clip)">
            <rect
              x="60"
              y="30"
              width="200"
              height="280"
              fill="url(#sa-egg-pool)"
              style={{
                opacity: `calc(${innerOpacity} * var(--sa-egg-light, 1))`,
                transition: 'opacity 700ms ease-out'
              }}
            />
            <ellipse
              cx="160"
              cy="262"
              rx="54"
              ry="42"
              fill="url(#sa-egg-hot)"
              style={{
                opacity: `calc(${hotOpacity} * var(--sa-egg-light, 1))`,
                transition: 'opacity 700ms ease-out'
              }}
            />
          </g>

          <path
            d="M 126 88 C 106 120 96 152 96 182"
            fill="none"
            stroke="rgba(255, 255, 255, 0.26)"
            strokeWidth="4"
            strokeLinecap="round"
          />

          {CRACK_PATHS.slice(0, visibleCracks).map((pathD, idx) => {
            const isFlashing = flashingIdx === idx
            const coreStroke = isFailed
              ? '#cf2d56'
              : isFlashing
                ? '#fff6e2'
                : 'rgba(255, 232, 190, 0.95)'

            return (
              <g key={idx}>
                <path
                  d={pathD}
                  fill="none"
                  stroke={isFailed ? 'rgba(207, 45, 86, 0.1)' : 'rgba(255, 190, 120, 0.16)'}
                  strokeWidth="9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d={pathD}
                  fill="none"
                  stroke={isFailed ? 'rgba(207, 45, 86, 0.3)' : 'rgba(255, 205, 140, 0.45)'}
                  strokeWidth="5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d={pathD}
                  fill="none"
                  stroke={coreStroke}
                  strokeWidth={isFlashing ? 4.5 : 2.4}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  filter={isFailed ? undefined : 'url(#crack-glow-filter)'}
                  className={clsx(
                    'transition-all duration-300',
                    isFlashing && 'animate-crack-flash'
                  )}
                />
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}
