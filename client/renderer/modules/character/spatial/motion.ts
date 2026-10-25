import { clamp } from '@runtime'

import type { Locomotion, Position } from './types'

const FLY_SPEED = 400
const SCALE_TRANSITION_MS = 300

interface MotionPorts {
  getPosition: () => Position
  setPosition: (position: Position) => void
  getLocomotion: () => Locomotion
  setLocomotion: (locomotion: Locomotion) => void
  getScale: () => number
  setScale: (scale: number) => void
  canMove: () => boolean
  clampPosition: (position: Position) => Position
  minScale: number
  maxScale: number
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

/** 每个空间运行时独占移动和缩放帧；回调只写所属 store，不持有窗口或 React 生命周期。 */
export function createSpatialMotion(ports: MotionPorts) {
  let rafId: number | null = null
  let moveStart: Position | null = null
  let moveTarget: Position | null = null
  let moveStartTime = 0
  let moveDuration = 0

  let scaleRafId: number | null = null
  let scaleStartVal = 1
  let scaleTargetVal = 1
  let scaleStartTime = 0

  function cancelMovement(): void {
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }

    moveStart = null
    moveTarget = null

    if (ports.getLocomotion() !== 'drag') {
      ports.setLocomotion('still')
    }
  }

  function tick(now: number): void {
    if (!moveStart || !moveTarget || !ports.canMove()) {
      cancelMovement()

      return
    }

    const t = Math.min(1, (now - moveStartTime) / moveDuration)
    const eased = easeInOut(t)

    const position = {
      x: moveStart.x + (moveTarget.x - moveStart.x) * eased,
      y: moveStart.y + (moveTarget.y - moveStart.y) * eased
    }

    ports.setPosition(t === 1 ? ports.clampPosition(position) : position)

    if (t < 1) {
      rafId = requestAnimationFrame(tick)
    } else {
      cancelMovement()
    }
  }

  function moveTo(target: Position): void {
    cancelMovement()

    const current = ports.getPosition()
    const dist = Math.hypot(target.x - current.x, target.y - current.y)

    if (dist < 2) {
      return
    }

    moveStart = { ...current }
    moveTarget = target
    moveStartTime = performance.now()
    moveDuration = Math.max((dist / FLY_SPEED) * 1000, 200)
    ports.setLocomotion('fly')
    rafId = requestAnimationFrame(tick)
  }

  function stopScale(): void {
    if (scaleRafId !== null) {
      cancelAnimationFrame(scaleRafId)
      scaleRafId = null
    }
  }

  function tickScale(now: number): void {
    const t = Math.min(1, (now - scaleStartTime) / SCALE_TRANSITION_MS)
    const eased = easeInOut(t)

    ports.setScale(scaleStartVal + (scaleTargetVal - scaleStartVal) * eased)

    if (t < 1) {
      scaleRafId = requestAnimationFrame(tickScale)
    } else {
      scaleRafId = null
    }
  }

  function setScaleTarget(scale: number, instant = false): void {
    const clamped = clamp(scale, ports.minScale, ports.maxScale)

    if (instant || Math.abs(clamped - ports.getScale()) < 0.01) {
      stopScale()
      ports.setScale(clamped)

      return
    }

    scaleStartVal = ports.getScale()
    scaleTargetVal = clamped
    scaleStartTime = performance.now()

    if (scaleRafId === null) {
      scaleRafId = requestAnimationFrame(tickScale)
    }
  }

  return {
    cancelMovement,
    isMoving: () => rafId !== null,
    moveTo,
    setScaleTarget,
    dispose: (): void => {
      cancelMovement()
      stopScale()
    }
  }
}
