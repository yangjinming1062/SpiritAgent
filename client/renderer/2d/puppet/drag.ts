import { clamp } from '@runtime'

export class DragDriver {
  weight = 0
  angle = 0
  lift = 0
  private angularVelocity = 0
  private liftVelocity = 0

  update(dt: number, dragging: boolean, vx: number, vy: number): void {
    const duration = clamp(dt, 0, 0.05)
    this.weight += ((dragging ? 1 : 0) - this.weight) * (1 - Math.exp(-duration / (dragging ? 0.12 : 0.24)))
    const angleTarget = dragging ? clamp(vx * 0.16, -0.18, 0.18) : 0
    const liftTarget = dragging ? 1 + clamp(-vy * 0.25, -0.35, 0.5) : 0

    // 固定上限子步保证低帧率时弹簧稳定；松手保留速度，连续回落。
    for (let remaining = duration; remaining > 0; ) {
      const step = Math.min(remaining, 1 / 120)
      this.angularVelocity += (90 * (angleTarget - this.angle) - 12 * this.angularVelocity) * step
      this.angle += this.angularVelocity * step
      this.liftVelocity += (120 * (liftTarget - this.lift) - 16 * this.liftVelocity) * step
      this.lift += this.liftVelocity * step
      remaining -= step
    }
  }
}
