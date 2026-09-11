/** Mesh2D 多手势识别器。
 *
 * 识别以下手势：
 * 1. head_pat（摸头）：光标在 head/face/front_hair 区域内横向往复滑动（500ms 内 ≥2 次转向）。
 *
 * 注：拖拽与眩晕反馈互斥——拖拽只做位置移动（README §7），故 shake/dizzy
 * 检测已移除；眩晕反馈的触发入口仅剩精灵状态机的 `handleDizzyInteraction`。
 */

interface GestureCallbacks {
  onPetStart?: (nx: number, ny: number) => void
  onPetTick?: (nx: number, ny: number) => void
  onPetEnd?: () => void
}

interface PointerSample {
  x: number
  y: number
  time: number
}

export class Mesh2DGestureTracker {
  private samples: PointerSample[] = []
  private isPetting = false
  private lastPetTickAt = 0
  private lastPetDirX = 0
  private reversalCount = 0
  private lastReversalAt = 0
  private callbacks: GestureCallbacks

  constructor(callbacks: GestureCallbacks) {
    this.callbacks = callbacks
  }

  public feedPointerMove(nx: number, ny: number, region?: string | null): void {
    const now = performance.now()
    this.samples.push({ x: nx, y: ny, time: now })

    // 保留最近 800ms 的采样
    while (this.samples.length > 0 && now - this.samples[0]!.time > 800) {
      this.samples.shift()
    }

    if (this.samples.length < 3) {
      return
    }

    const prev = this.samples[this.samples.length - 2]!
    const dx = nx - prev.x

    const isHeadRegion = region === 'head' || region === 'face' || region === 'front_hair'

    if (isHeadRegion && Math.abs(dx) > 0.015) {
      const currentDirX = dx > 0 ? 1 : -1

      if (this.lastPetDirX !== 0 && currentDirX !== this.lastPetDirX) {
        if (now - this.lastReversalAt < 500) {
          this.reversalCount++
        } else {
          this.reversalCount = 1
        }

        this.lastReversalAt = now
      }

      this.lastPetDirX = currentDirX

      if (this.reversalCount >= 2) {
        if (!this.isPetting) {
          this.isPetting = true
          this.callbacks.onPetStart?.(nx, ny)
        }

        if (now - this.lastPetTickAt > 300) {
          this.lastPetTickAt = now
          this.callbacks.onPetTick?.(nx, ny)
        }
      }
    } else if (!isHeadRegion && this.isPetting) {
      this.endPetting()
    }
  }

  public feedPointerUp(): void {
    if (this.isPetting) {
      this.endPetting()
    }

    this.reversalCount = 0
    this.samples = []
    this.lastPetDirX = 0
  }

  public tick(now: number): void {
    if (this.isPetting && now - this.lastPetTickAt > 500) {
      this.endPetting()
    }
  }

  private endPetting(): void {
    this.isPetting = false
    this.reversalCount = 0
    this.lastPetDirX = 0
    this.callbacks.onPetEnd?.()
  }
}
