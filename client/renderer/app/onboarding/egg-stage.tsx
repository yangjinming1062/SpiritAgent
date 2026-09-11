import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import { useEffect, useRef, useState } from 'react'

import { openContextMenu } from '@/modules/character'
import { $surfaceOpen } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

// 桌面常驻「蛋」：透明置顶窗口里呼吸 / 裂纹闪光 / hover 注视，等待用户戳击启动 onboarding。
// 设计来源：docs/DESIGN.md §4 / §5.1——「蛋破碎后开始对话」意味着蛋必须作为独立桌面存在物，
// 而非跳过的中间态。Installer 端的 Egg 组件是流程动画，桌面这个是常驻精灵。
const CRACK_PATHS = [
  'M 160 70 L 148 95 L 155 115 L 140 135',
  'M 230 140 L 205 148 L 195 135 L 180 155',
  'M 90 210 L 115 200 L 125 215 L 145 195',
  'M 80 145 L 105 150 L 115 165 L 138 160',
  'M 175 60 L 180 88 L 168 105 L 175 125',
  'M 160 270 L 165 240 L 152 215 L 160 170'
]

const CLICK_PROMPT_MS = 2000
const IDLE_LOOK_BOB_MS = 3200

interface EggStageProps {
  size?: number
  onTap: () => void
}

export function EggStage({ size = 280, onTap }: EggStageProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const eyesRef = useRef<SVGGElement>(null)
  const surfaceOpen = useStore($surfaceOpen)
  const dict = useStrings()
  const [promptVisible, setPromptVisible] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setPromptVisible(true), CLICK_PROMPT_MS)

    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    let pointerActive = false
    let pointerLook = { x: 0, y: 0 }

    const updateEyes = (x: number, y: number) => {
      if (eyesRef.current) {
        eyesRef.current.style.transform = `translate3d(${x * 3.2}px, ${y * 3.2}px, 0)`
      }
    }

    const onMove = (e: PointerEvent) => {
      const rect = containerRef.current?.getBoundingClientRect()

      if (!rect) {
        return
      }

      const nx = clamp(((e.clientX - rect.left) / rect.width) * 2 - 1, -1, 1)
      const ny = clamp(((e.clientY - rect.top) / rect.height) * 2 - 1, -1, 1)
      pointerActive = true
      pointerLook = { x: nx, y: ny }
      updateEyes(nx, ny)
    }

    const onLeave = () => {
      pointerActive = false
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerleave', onLeave)

    let raf = 0
    const startedAt = performance.now()

    const tick = (now: number) => {
      if (!pointerActive) {
        const t = (now - startedAt) / IDLE_LOOK_BOB_MS
        updateEyes(Math.sin(t * Math.PI * 2) * 0.18, Math.cos(t * Math.PI) * 0.12)
      } else {
        updateEyes(pointerLook.x, pointerLook.y)
      }

      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerleave', onLeave)
      cancelAnimationFrame(raf)
    }
  }, [])

  return (
    <div
      className="relative flex items-center justify-center select-none transition-opacity duration-300"
      onContextMenu={e => {
        e.preventDefault()
        openContextMenu({ x: e.clientX, y: e.clientY })
      }}
      onPointerDown={e => {
        if (e.button !== 0) {
          return
        }

        e.stopPropagation()
        onTap()
      }}
      ref={containerRef}
      style={{ width: size, height: size, opacity: surfaceOpen !== null ? 0.25 : 1 }}
    >
      <svg
        aria-label={`${dict.brand.name} Egg`}
        className="overflow-visible"
        height={size}
        viewBox="0 0 320 320"
        width={size}
      >
        <defs>
          <radialGradient cx="50%" cy="50%" id="egg-ambient-glow" r="50%">
            <stop offset="0%" stopColor="var(--color-egg-glow, #ffd166)" stopOpacity="0.45" />
            <stop offset="60%" stopColor="var(--color-egg-glow, #ffd166)" stopOpacity="0.15" />
            <stop offset="100%" stopColor="var(--color-egg-glow, #ffd166)" stopOpacity="0" />
          </radialGradient>

          <filter height="140%" id="crack-glow-filter" width="140%" x="-20%" y="-20%">
            <feGaussianBlur result="blur" stdDeviation="2" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* 环境辉光 */}
        <ellipse className="animate-egg-pulse" cx="160" cy="170" fill="url(#egg-ambient-glow)" rx="125" ry="135" />

        {/* 蛋壳主体 */}
        <g className="animate-egg-breath">
          <path
            d="M 160 50 C 95 50 75 135 75 185 C 75 245 112 290 160 290 C 208 290 245 245 245 185 C 245 135 225 50 160 50 Z"
            fill="var(--color-egg-shell, #fff4d6)"
            stroke="color-mix(in srgb, var(--color-foreground, #17171a) 12%, transparent)"
            strokeWidth="2"
          />

          {/* 眼睛与高光组：通过 ref 硬件加速偏移，避免 60fps React 重渲染 */}
          <g ref={eyesRef} style={{ willChange: 'transform' }}>
            <ellipse cx={156} cy={155} fill="var(--color-egg-eye, #1a1a2e)" rx="6" ry="9" />
            <ellipse cx={184} cy={155} fill="var(--color-egg-eye, #1a1a2e)" rx="6" ry="9" />
            <circle cx={158} cy={152} fill="#fff" opacity="0.8" r="1.5" />
            <circle cx={186} cy={152} fill="#fff" opacity="0.8" r="1.5" />
          </g>

          {/* 嘴 */}
          <ellipse cx="170" cy="195" fill="var(--color-egg-mouth, #c89060)" opacity="0.85" rx="6" ry="3.5" />

          {/* 脸颊（粘人感） */}
          <ellipse cx="135" cy="180" fill="var(--color-egg-blush, #ff9999)" opacity="0.35" rx="9" ry="5" />
          <ellipse cx="205" cy="180" fill="var(--color-egg-blush, #ff9999)" opacity="0.35" rx="9" ry="5" />
        </g>

        {/* 裂纹装饰 */}
        <g filter="url(#crack-glow-filter)" opacity="0.55">
          {CRACK_PATHS.slice(0, 3).map((d, i) => (
            <path
              className="animate-crack-shimmer"
              d={d}
              fill="none"
              key={i}
              stroke="color-mix(in srgb, var(--color-foreground, #17171a) 55%, var(--color-egg-glow, #ffd166))"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              style={{ animationDelay: `${i * 0.6}s` }}
            />
          ))}
        </g>
      </svg>

      {promptVisible && (
        <div className="pointer-events-none absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs text-body animate-pulse">
          戳一戳，让我醒来
        </div>
      )}
    </div>
  )
}
