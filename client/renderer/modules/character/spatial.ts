import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import { persistString, registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { shellApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceOpen, $surfaceSpriteVisible, isSpriteStageShown } from '@/shared/store/surfaces'
import { type DesktopSpriteScalePayload, SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { clamp } from '@runtime'

import { $screenLocked } from './activity'
import { $effectiveTier, $spriteState, holdInteracting, setSpriteState } from './companion-store'
import {
  baseSpriteSize,
  clampPositionToViewport,
  computeHomePosition,
  perchToEdge,
  spriteContentBox
} from './spatial/geometry'
import { createSpatialMotion } from './spatial/motion'
import type { Locomotion, SpriteRect, ViewportSize } from './spatial/types'
import { clearSpriteGesture, playSpriteGesture } from './sprite/gesture'

export { baseSpriteSize, computeOverlayAnchorBesideSprite } from './spatial/geometry'
export type { Locomotion, SpriteRect } from './spatial/types'

export const EXPRESSION_BOOST_SCALE = 1.12

export function getBaseSpriteHeight(): number {
  return baseSpriteSize(window.innerHeight).height
}

export function getBaseSpriteWidth(): number {
  return baseSpriteSize(window.innerHeight).width
}

const SCALE_KEY = 'da.companion.defaultScale'
const MIN_SCALE = SPRITE_SCALE_LIMITS.min
const MAX_SCALE = SPRITE_SCALE_LIMITS.max

// 舞台内归一化内容范围；未上报时按整盒处理。初始 home 会读取它，须先声明。
export const $spriteContentRect = atom<SpriteRect | null>(null)
export const $spriteHeadRect = atom<SpriteRect | null>(null)
export const $spriteFootAnchor = atom<{ x: number; y: number } | null>(null)

export const $defaultScale = atom<number>(readDefaultScale())
export const $spatialPos = atom<{ x: number; y: number }>(getHomePosition())
export const $homePosition = atom<{ x: number; y: number }>(getHomePosition())
export const $spatialScale = atom<number>($defaultScale.get())
export const $spatialLocomotion = atom<Locomotion>('still')
export const $spriteCanvasRect = atom<{ left: number; top: number; right: number; bottom: number } | null>(null)

export const $viewport = atom<ViewportSize>({ width: window.innerWidth, height: window.innerHeight })

// 情绪反馈以轮廓脚底为原点放大，静止档保留原尺寸。
export const $expressionBoost = computed(
  [$spriteState, $effectiveTier],
  (state, tier) => state === 'emotional' && tier !== 'still'
)

let userInteracted = false

const motion = createSpatialMotion({
  getPosition: () => $spatialPos.get(),
  setPosition: position => $spatialPos.set(position),
  getLocomotion: () => $spatialLocomotion.get(),
  setLocomotion: locomotion => $spatialLocomotion.set(locomotion),
  getScale: () => $spatialScale.get(),
  setScale: scale => $spatialScale.set(scale),
  canMove: () => !$surfaceOpen.get(),
  clampPosition: position => clampPosToViewport(position),
  minScale: MIN_SCALE,
  maxScale: MAX_SCALE
})

const { cancelMovement, moveTo, setScaleTarget } = motion

export { cancelMovement }

function getViewport(): ViewportSize {
  return { width: window.innerWidth, height: window.innerHeight }
}

function getHomePosition(): { x: number; y: number } {
  return computeHomePosition(getViewport(), contentBounds(), $defaultScale.get())
}

function contentBox(scale = $spatialScale.get()): SpriteRect {
  return spriteContentBox(getViewport(), contentBounds(), scale)
}

function contentBounds(): SpriteRect {
  return $spriteContentRect.get() ?? { left: 0, top: 0, right: 1, bottom: 1 }
}

function clampPosToViewport(pos: { x: number; y: number }, scale = $spatialScale.get()): { x: number; y: number } {
  return clampPositionToViewport(pos, getViewport(), contentBounds(), scale)
}

function readDefaultScale(): number {
  const stored = storedString(SCALE_KEY)

  if (stored) {
    const n = Number(stored)

    if (!Number.isNaN(n) && n >= MIN_SCALE && n <= MAX_SCALE) {
      return n
    }
  }

  return 1
}

function applyDefaultScale(scale: number): number | null {
  if (!Number.isFinite(scale)) {
    return null
  }

  const clamped = clamp(scale, MIN_SCALE, MAX_SCALE)
  $defaultScale.set(clamped)
  persistString(SCALE_KEY, String(clamped))
  setScaleTarget($defaultScale.get())

  return clamped
}

export function syncDefaultScale({ scale }: DesktopSpriteScalePayload): void {
  applyDefaultScale(scale)
}

export function setDefaultScale(scale: number): void {
  const clamped = applyDefaultScale(scale)

  if (clamped === null) {
    return
  }

  shellApi().sprite.setDefaultScale({ scale: clamped })
}

export function returnToHomePosition(instant = false): void {
  const target = clampPosToViewport($homePosition.get())

  if (instant) {
    cancelMovement()
    $spatialPos.set(target)
    $spatialLocomotion.set('still')
  } else {
    moveTo(target)
  }
}

export function startDrag(): void {
  userInteracted = true
  clearSpriteGesture()
  cancelMovement()

  $spatialLocomotion.set('drag')
  holdInteracting()
}

export function updateDragPosition(pos: { x: number; y: number }): void {
  const c = contentBox()
  const w = getBaseSpriteWidth() * $spatialScale.get()
  $spatialPos.set({
    x: clamp(pos.x, -w * 0.8, window.innerWidth - w * 0.2),
    y: clamp(pos.y, -c.top, window.innerHeight - c.bottom)
  })
}

export function endDragAt(pos: { x: number; y: number }, cancelled = false): void {
  const safe = clampPosToViewport(pos)
  // 边缘栖息：贴近左右边缘时吸附贴边；取消拖拽不改变栖息决定。
  const perched = cancelled ? null : perchToEdge(safe, getViewport(), contentBounds(), $spatialScale.get())
  const settled = perched ?? safe
  $spatialPos.set(settled)
  $homePosition.set(settled)
  $spatialLocomotion.set('still')

  if (!cancelled) {
    playSpriteGesture({ kind: 'land' })
  }

  setSpriteState('interacting', { durationMs: cancelled ? 0 : 500 })
  setScaleTarget($defaultScale.get())
  void shellApi().sprite.setPosition(settled)
}

export function resetToHomePosition(): void {
  userInteracted = false
  cancelMovement()

  const home = getHomePosition()
  $homePosition.set(home)
  $spatialLocomotion.set('still')

  $spatialPos.set(home)
  void shellApi().sprite.setPosition(home)
}

export function initSpatial(): () => void {
  let disposed = false
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  const offs: Array<() => void> = [shellApi().sprite.onDefaultScaleChanged(syncDefaultScale)]
  offs.push(
    registerStorageClearHandler(() => {
      userInteracted = false
      cancelMovement()
      clearSpriteGesture()
    })
  )

  // 轻量漫步：本地随机的短距离移动，不经 Life Engine；静止档与拖拽/飞行期间不触发。
  let strollTimer: ReturnType<typeof setTimeout> | null = null

  const scheduleStroll = (): void => {
    if (strollTimer !== null) {
      clearTimeout(strollTimer)
    }

    // 3–8 分钟一次的偶尔挪动；命中概率再打折扣，保持"安静陪伴"的默认体感。
    strollTimer = setTimeout(
      () => {
        strollTimer = null

        if (
          disposed ||
          $effectiveTier.get() === 'still' ||
          $screenLocked.get() ||
          motionPreference.matches ||
          $presentation.get().fullscreen ||
          $spriteState.get() !== 'idle' ||
          $surfaceOpen.get() ||
          !isSpriteStageShown() ||
          motion.isMoving() ||
          $spatialLocomotion.get() !== 'still' ||
          document.visibilityState !== 'visible'
        ) {
          scheduleStroll()

          return
        }

        if (Math.random() > 0.5) {
          scheduleStroll()

          return
        }

        const drift = 40 + Math.random() * 80

        const target = clampPosToViewport({
          x: $homePosition.get().x + (Math.random() < 0.5 ? -drift : drift),
          y: $homePosition.get().y + (Math.random() - 0.5) * drift * 0.5
        })

        // 漫步是临时漂移：不落盘静止位置，重启仍回到用户安放的家。
        moveTo(target, { stroll: true })
        scheduleStroll()
      },
      (3 + Math.random() * 5) * 60_000
    )
  }

  scheduleStroll()
  let restoreStop: (() => void) | null = null
  let restoreTimer: ReturnType<typeof setTimeout> | null = null

  const stopRestore = (): void => {
    restoreStop?.()
    restoreStop = null

    if (restoreTimer !== null) {
      clearTimeout(restoreTimer)
      restoreTimer = null
    }
  }

  void shellApi()
    .sprite.getPosition()
    .then(saved => {
      if (disposed || !saved || userInteracted) {
        return
      }

      const restore = (): void => {
        stopRestore()

        if (disposed || userInteracted) {
          return
        }

        const next = clampPosToViewport(saved)
        $homePosition.set(next)

        $spatialPos.set(next)

        void shellApi().sprite.setPosition(next)
      }

      if ($spriteContentRect.get()) {
        restore()
      } else {
        restoreStop = $spriteContentRect.listen(rect => {
          if (rect) {
            restore()
          }
        })
        restoreTimer = setTimeout(restore, 3000)
      }
    })
    .catch(error => {
      log.warn('spatial', 'Could not restore saved position', error)
      stopRestore()
    })

  const pauseStage = (): void => {
    clearSpriteGesture()
    cancelMovement()
    $spatialLocomotion.set('still')
  }

  const reevaluate = (): void => {
    if (
      $screenLocked.get() ||
      !isSpriteStageShown() ||
      $presentation.get().fullscreen ||
      motionPreference.matches ||
      document.visibilityState === 'hidden' ||
      ($spatialLocomotion.get() === 'stroll' && $effectiveTier.get() === 'still')
    ) {
      pauseStage()
    }
  }

  offs.push(
    $presentation.listen(reevaluate),
    $surfaceOpen.listen(reevaluate),
    $surfaceSpriteVisible.listen(reevaluate),
    $screenLocked.listen(reevaluate)
  )
  offs.push($effectiveTier.listen(reevaluate))
  motionPreference.addEventListener('change', reevaluate)
  document.addEventListener('visibilitychange', reevaluate)

  const clampCurrent = (): void => {
    if ($spatialLocomotion.get() !== 'drag' && !motion.isMoving()) {
      $spatialPos.set(clampPosToViewport($spatialPos.get()))
    }
  }

  offs.push($spatialScale.listen(clampCurrent))
  offs.push(
    $spriteContentRect.listen(rect => {
      if (!rect) {
        return
      }

      $homePosition.set(clampPosToViewport($homePosition.get()))
      clampCurrent()
    })
  )

  const resize = (): void => {
    $viewport.set(getViewport())

    if ($spatialLocomotion.get() === 'drag') {
      return
    }

    $homePosition.set(clampPosToViewport($homePosition.get()))

    returnToHomePosition(true)
  }

  window.addEventListener('resize', resize)

  return () => {
    disposed = true

    if (strollTimer !== null) {
      clearTimeout(strollTimer)
      strollTimer = null
    }

    stopRestore()
    offs.forEach(off => off())
    window.removeEventListener('resize', resize)
    motionPreference.removeEventListener('change', reevaluate)
    document.removeEventListener('visibilitychange', reevaluate)
    cancelMovement()
    motion.dispose()
  }
}
