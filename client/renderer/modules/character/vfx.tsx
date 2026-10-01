/** 精灵视觉特效与情绪粒子系统。 */

import { useEffect, useRef, useState } from 'react'

type VfxType = 'heart' | 'music_notes' | 'sleep_zzz'

interface VfxEmitOptions {
  count?: number
  /** 归一化 [0, 1] 相对精灵锚点 x（缺省 0.5） */
  nx?: number
  /** 归一化 [0, 1] 相对精灵锚点 y（缺省 0.25） */
  ny?: number
}

interface Particle {
  id: number
  type: VfxType
  x: number // % (相对 sprite stage)
  y: number // %
  vx: number
  vy: number
  scale: number
  opacity: number
  rotation: number
  vRot: number
  createdAt: number
  durationMs: number
}

let nextParticleId = 1
const activeParticles: Particle[] = []

// 唤醒回调：SpriteVfxOverlay 在 mount 时注册，emitVfx 在粒子清空后再次添加时调用——用于把已停止的 RAF 循环重新拉起。
let wakeTick: (() => void) | null = null

function registerVfxWake(fn: (() => void) | null): void {
  wakeTick = fn
}

export function emitVfx(type: VfxType, opts: VfxEmitOptions = {}): void {
  const count = opts.count ?? 3
  const now = performance.now()

  // 默认在精灵头部上方偏中间
  const baseX = (opts.nx ?? 0.5) * 100 // %
  const baseY = (opts.ny ?? 0.25) * 100 // %

  for (let i = 0; i < count; i++) {
    const p: Particle = {
      id: nextParticleId++,
      type,
      x: baseX + (Math.random() - 0.5) * 24,
      y: baseY + (Math.random() - 0.5) * 16,
      vx: (Math.random() - 0.5) * 20,
      vy: -(20 + Math.random() * 25),
      scale: 0.8 + Math.random() * 0.4,
      opacity: 1,
      rotation: (Math.random() - 0.5) * 0.4,
      vRot: (Math.random() - 0.5) * 0.6,
      createdAt: now,
      durationMs: 1600
    }

    activeParticles.push(p)
  }

  wakeTick?.()
}

// 立刻清除指定类型的所有粒子（用于状态切换 / 重连时强制收回瞬时特效）。
export function clearVfx(type: VfxType): void {
  for (let i = activeParticles.length - 1; i >= 0; i--) {
    if (activeParticles[i]!.type === type) {
      activeParticles.splice(i, 1)
    }
  }
}

function renderParticleIcon(p: Particle): React.JSX.Element {
  switch (p.type) {
    case 'heart':
      return <span style={{ fontSize: 24, filter: 'drop-shadow(0 2px 4px rgba(255,105,180,0.5))' }}>💖</span>

    case 'music_notes':
      return <span style={{ fontSize: 22, filter: 'drop-shadow(0 2px 4px rgba(147,112,219,0.6))' }}>🎵</span>

    case 'sleep_zzz':
      return (
        <span
          style={{
            fontSize: 22,
            fontWeight: 'bold',
            color: 'var(--color-vfx-sleep, #93c5fd)',
            textShadow: '0 2px 4px rgba(0,0,0,0.4)'
          }}
        >
          💤
        </span>
      )
  }
}

export function SpriteVfxOverlay(): React.JSX.Element | null {
  const [, setFrame] = useState(0)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    let running = true

    const tick = () => {
      if (!running) {
        rafRef.current = null

        return
      }

      if (activeParticles.length > 0) {
        const now = performance.now()

        for (let i = activeParticles.length - 1; i >= 0; i--) {
          const p = activeParticles[i]!
          const elapsed = now - p.createdAt
          const progress = Math.min(1, elapsed / p.durationMs)

          if (progress >= 1) {
            activeParticles.splice(i, 1)

            continue
          }

          // 物理更新
          p.x += (p.vx * 16) / 1000
          p.y += (p.vy * 16) / 1000
          p.rotation += (p.vRot * 16) / 1000
          p.opacity = progress < 0.2 ? progress / 0.2 : 1 - (progress - 0.2) / 0.8
        }

        setFrame(n => (n + 1) % 1000000)

        rafRef.current = requestAnimationFrame(tick)
      } else {
        // 粒子清空后停止 RAF 循环，避免无意义空转；emitVfx 会通过 wakeTick 再次拉起。
        rafRef.current = null
      }
    }

    // 注册唤醒回调：emitVfx 添加粒子时若循环已停止，重新启动。
    registerVfxWake(() => {
      if (!running) {
        return
      }

      if (rafRef.current === null && activeParticles.length > 0) {
        rafRef.current = requestAnimationFrame(tick)
      }
    })

    rafRef.current = requestAnimationFrame(tick)

    return () => {
      running = false
      registerVfxWake(null)

      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [])

  if (activeParticles.length === 0) {
    return null
  }

  return (
    <div
      className="sprite-vfx-overlay"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        overflow: 'visible',
        zIndex: 50
      }}
    >
      {activeParticles.map(p => (
        <div
          key={p.id}
          style={{
            position: 'absolute',
            left: `${p.x}%`,
            top: `${p.y}%`,
            transform: `translate(-50%, -50%) scale(${p.scale}) rotate(${p.rotation}rad)`,
            opacity: p.opacity,
            pointerEvents: 'none',
            userSelect: 'none',
            willChange: 'transform, opacity'
          }}
        >
          {renderParticleIcon(p)}
        </div>
      ))}
    </div>
  )
}
