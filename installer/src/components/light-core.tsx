import type React from 'react'
import { EGG_PATH } from './egg'

interface LightCoreProps {
  variant: 'dormant' | 'hatched'
  size: number
}

// 光核：欢迎页是尚未孵化的蛋壳微光，完成页是破壳后的暖光。
export function LightCore({ variant, size }: LightCoreProps): React.JSX.Element {
  if (variant === 'dormant') {
    return (
      <div className="sa-core sa-core-dormant" style={{ width: size, height: size }}>
        <span className="sa-core-glow" />
        <span className="sa-orbit" />
        <svg
          viewBox="60 30 200 280"
          width={size}
          height={size}
          className="sa-core-shell"
          aria-hidden="true"
        >
          <defs>
            <linearGradient
              id="sa-core-grad"
              gradientUnits="userSpaceOnUse"
              x1="90"
              y1="60"
              x2="240"
              y2="280"
            >
              <stop offset="0%" style={{ stopColor: 'var(--sa-arc-1)' }} />
              <stop offset="50%" style={{ stopColor: 'var(--sa-arc-2)' }} />
              <stop offset="100%" style={{ stopColor: 'var(--sa-arc-3)' }} />
            </linearGradient>
            <linearGradient id="sa-core-shell" x1="0.1" y1="0" x2="0.5" y2="1">
              <stop offset="0%" stopColor="#c6d2ff" stopOpacity="0.3" />
              <stop offset="48%" stopColor="#7e96eb" stopOpacity="0.14" />
              <stop offset="100%" stopColor="#5c70c8" stopOpacity="0.14" />
            </linearGradient>
            <radialGradient id="sa-core-pool" cx="50%" cy="90%" r="48%">
              <stop offset="0%" stopColor="#ffe9b8" stopOpacity="0.8" />
              <stop offset="48%" stopColor="#ffb85c" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#ff9f43" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="sa-core-hot" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#fff3d6" stopOpacity="0.9" />
              <stop offset="45%" stopColor="#ffb85c" stopOpacity="0.5" />
              <stop offset="100%" stopColor="#ff9f43" stopOpacity="0" />
            </radialGradient>
            <clipPath id="sa-core-clip">
              <path d={EGG_PATH} />
            </clipPath>
          </defs>
          <path
            d={EGG_PATH}
            fill="url(#sa-core-shell)"
            stroke="url(#sa-core-grad)"
            strokeWidth="2.5"
          />
          <g clipPath="url(#sa-core-clip)">
            <rect x="60" y="30" width="200" height="280" fill="url(#sa-core-pool)" opacity="0.8" />
            <ellipse
              cx="160"
              cy="256"
              rx="40"
              ry="34"
              fill="url(#sa-core-hot)"
              className="sa-core-breathe"
            />
          </g>
        </svg>
      </div>
    )
  }

  return (
    <div className="sa-core sa-core-hatched" style={{ width: size, height: size }}>
      <span className="sa-core-glow" />
      <span className="sa-orbit" />
      <span className="sa-rays" />
      <span className="sa-core-orb" />
      <span className="sa-bloom" />
    </div>
  )
}
