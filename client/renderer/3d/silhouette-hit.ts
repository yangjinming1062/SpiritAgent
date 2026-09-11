import { clamp } from '@runtime'
import { atom } from 'nanostores'

import { probeInteractiveRegions } from '@/companion'

import type { Engine, SilhouetteHitmap } from './Engine'

// 精灵舞台在 3D 模式下用这个命中谓词进一步细化命中区域。启动/加载空隙期间为 null，回退到矩形判定。
export const $sprite3DHitTest = atom<((x: number, y: number) => boolean | null) | null>(null)

const HIT_ALPHA_MIN = 16

/** alpha 命中图 → 可见内容归一化包围盒（canvas 铺满舞台盒，坐标直接归一）。
 * 取采样时刻待机姿态的外接矩形——发束/动作瞬态的越界量级远小于画布留白，可接受。 */
export function alphaMapContentRect(
  map: SilhouetteHitmap
): { left: number; top: number; right: number; bottom: number } | null {
  let minX = map.width
  let minY = map.height
  let maxX = -1
  let maxY = -1

  for (let py = 0; py < map.height; py++) {
    const row = py * map.width

    for (let px = 0; px < map.width; px++) {
      if (map.alpha[row + px] < HIT_ALPHA_MIN) {
        continue
      }

      minX = Math.min(minX, px)
      minY = Math.min(minY, py)
      maxX = Math.max(maxX, px)
      maxY = Math.max(maxY, py)
    }
  }

  if (maxX < 0) {
    return null
  }

  return {
    left: minX / map.width,
    top: minY / map.height,
    right: (maxX + 1) / map.width,
    bottom: (maxY + 1) / map.height
  }
}

export function attachSilhouetteHitProbe(
  engine: Pick<Engine, 'canvas' | 'silhouetteHitmap'> & { getSilhouetteHitmap?: () => SilhouetteHitmap | null }
): () => void {
  let latestMap: SilhouetteHitmap | null = null
  let refreshing = false
  let disposed = false
  let rafId: number | null = null

  const test = (x: number, y: number): boolean | null => {
    const map = engine.getSilhouetteHitmap?.() ?? latestMap

    if (!map) {
      return null
    }

    const canvas = engine.canvas
    const rect = canvas.getBoundingClientRect()

    if (rect.width <= 0 || rect.height <= 0) {
      return false
    }

    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
      return false
    }

    const relX = (x - rect.left) / rect.width
    const relY = (y - rect.top) / rect.height
    // 引擎把命中图的 alpha 归一化为自顶向下的行序，与 DOM client 空间一致。
    const px = clamp(Math.floor(relX * map.width), 0, map.width - 1)
    const py = clamp(Math.floor(relY * map.height), 0, map.height - 1)

    return map.alpha[py * map.width + px] >= HIT_ALPHA_MIN
  }

  const refresh = (): void => {
    if (refreshing || disposed) {
      return
    }

    refreshing = true
    void engine
      .silhouetteHitmap()
      .then(map => {
        if (disposed) {
          return
        }

        if (map) {
          latestMap = map
          probeInteractiveRegions()
        }
      })
      .finally(() => {
        refreshing = false
      })
  }

  const onMove = (e: MouseEvent): void => {
    const rect = engine.canvas.getBoundingClientRect()

    // 当光标进入或接近画布边界时，若命中图过期则刷新
    if (
      e.clientX >= rect.left - 50 &&
      e.clientX <= rect.right + 50 &&
      e.clientY >= rect.top - 50 &&
      e.clientY <= rect.bottom + 50
    ) {
      if (rafId === null) {
        rafId = requestAnimationFrame(() => {
          rafId = null
          refresh()
        })
      }
    }
  }

  // 立刻请求初始命中图，让引擎一渲染出来就能命中
  refresh()

  window.addEventListener('mousemove', onMove)
  $sprite3DHitTest.set(test)

  return () => {
    disposed = true
    window.removeEventListener('mousemove', onMove)

    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }

    $sprite3DHitTest.set(null)
    latestMap = null
  }
}
