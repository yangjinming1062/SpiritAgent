import { clamp } from '@runtime'

import type { Locomotion, Position } from './types'

const WALK_SPEED = 80
const FLY_SPEED = 400
const WALK_RANGE_PX = 400
const SCALE_TRANSITION_MS = 300

interface MotionPorts {
  getPosition: () => Position
  setPosition: (position: Position) => void
  getLocomotion: () => Locomotion
  setLocomotion: (locomotion: Locomotion) => void
  getScale: () => number
  setScale: (scale: number) => void
  canMove: () => boolean
  hasPeek: () => boolean
  clampPosition: (position: Position) => Position
  minScale: number
  maxScale: number
}

export interface FrameScheduler {
  request: (callback: (now: number) => void) => number
  cancel: (id: number) => void
  now: () => number
}

const browserFrames: FrameScheduler = {
  request: callback => requestAnimationFrame(callback),
  cancel: id => cancelAnimationFrame(id),
  now: () => performance.now()
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

export function locomotionForDistance(dist: number): 'walk' | 'fly' {
  return dist > WALK_RANGE_PX ? 'fly' : 'walk'
}

/** 每个空间运行时独占移动和缩放帧；回调只写所属 store，不持有窗口或 React 生命周期。 */
export function createSpatialMotion(ports: MotionPorts, frames: FrameScheduler = browserFrames) {
  let rafId: number | null = null
  let moveStart: Position | null = null
  let moveTarget: Position | null = null
  let moveStartTime = 0
  let moveDuration = 0
  let moveOnArrive: (() => void) | null = null

  let scaleRafId: number | null = null
  let scaleStartVal = 1
  let scaleTargetVal = 1
  let scaleStartTime = 0

  function cancelMovement(notifyArrive = false): void {
    if (rafId !== null) {
      frames.cancel(rafId)
      rafId = null
    }

    const cb = moveOnArrive
    moveStart = null
    moveTarget = null
    moveOnArrive = null

    if (ports.getLocomotion() !== 'drag') {
      ports.setLocomotion('still')
    }

    if (notifyArrive) {
      cb?.()
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

    ports.setPosition(t === 1 && !ports.hasPeek() ? ports.clampPosition(position) : position)

    if (t < 1) {
      rafId = frames.request(tick)
    } else {
      cancelMovement(true)
    }
  }

  function moveTo(target: Position, locomotion: 'walk' | 'fly', onArrive?: () => void): void {
    cancelMovement()

    const current = ports.getPosition()
    const dist = Math.hypot(target.x - current.x, target.y - current.y)

    if (dist < 2) {
      onArrive?.()

      return
    }

    const speed = locomotion === 'walk' ? WALK_SPEED : FLY_SPEED
    moveStart = { ...current }
    moveTarget = target
    moveStartTime = frames.now()
    moveDuration = Math.max((dist / speed) * 1000, 200)
    moveOnArrive = onArrive ?? null
    ports.setLocomotion(locomotion)
    rafId = frames.request(tick)
  }

  function stopScale(): void {
    if (scaleRafId !== null) {
      frames.cancel(scaleRafId)
      scaleRafId = null
    }
  }

  function tickScale(now: number): void {
    const t = Math.min(1, (now - scaleStartTime) / SCALE_TRANSITION_MS)
    const eased = easeInOut(t)

    ports.setScale(scaleStartVal + (scaleTargetVal - scaleStartVal) * eased)

    if (t < 1) {
      scaleRafId = frames.request(tickScale)
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
    scaleStartTime = frames.now()

    if (scaleRafId === null) {
      scaleRafId = frames.request(tickScale)
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
