import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import { persistString, registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { shellApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceOpen, $surfaceSpriteVisible, isSpriteStageShown } from '@/shared/store/surfaces'
import { type DesktopScreenRect, type DesktopSpriteScalePayload, SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { clamp } from '@runtime'

import { $lastIdleSeconds, $screenLocked } from './activity'
import { $effectiveTier, $spriteState, holdInteracting, setSpriteState } from './companion-store'
import {
  baseSpriteSize,
  computePerchPlacement as calculatePerchPlacement,
  generateRoamWaypoint as calculateRoamWaypoint,
  clampPositionToViewport,
  computeHomePosition,
  spriteContentBox
} from './spatial/geometry'
import type { PerchPlacement } from './spatial/geometry'
import { createSpatialMotion } from './spatial/motion'
import type { Locomotion, SpatialLocale, SpatialLocaleOptions, SpriteRect, ViewportSize } from './spatial/types'
import { clearSpriteGesture, playSpriteGesture } from './sprite/gesture'

export { baseSpriteSize, computeOverlayAnchorBesideSprite } from './spatial/geometry'
export type { PerchPlacement } from './spatial/geometry'
export { locomotionForDistance } from './spatial/motion'
export type { Locomotion, SpriteRect } from './spatial/types'

export const EXPRESSION_BOOST_SCALE = 1.12

export function getBaseSpriteHeight(): number {
  return baseSpriteSize(window.innerHeight).height
}

export function getBaseSpriteWidth(): number {
  return baseSpriteSize(window.innerHeight).width
}

// Runner 离线或空闲时间未知（-1）时不漫游。
const ROAM_IDLE_THRESHOLD_SECONDS = 90
const SCALE_KEY = 'da.companion.defaultScale'
const MIN_SCALE = SPRITE_SCALE_LIMITS.min
const MAX_SCALE = SPRITE_SCALE_LIMITS.max

const $spatialLocale = atom<SpatialLocale>('home')

// 舞台内归一化内容范围；未上报时按整盒处理。初始 home 会读取它，须先声明。
export const $spriteContentRect = atom<SpriteRect | null>(null)
export const $spriteHeadRect = atom<SpriteRect | null>(null)

export const $defaultScale = atom<number>(readDefaultScale())
export const $spatialPos = atom<{ x: number; y: number }>(getHomePosition())
export const $homePosition = atom<{ x: number; y: number }>(getHomePosition())
export const $spatialScale = atom<number>($defaultScale.get())
export const $spatialLocomotion = atom<Locomotion>('still')
export const $spriteCanvasRect = atom<{ left: number; top: number; right: number; bottom: number } | null>(null)

export const $viewport = atom<ViewportSize>({ width: window.innerWidth, height: window.innerHeight })

// 情绪反馈以轮廓脚底为原点放大；静止档与栖息时保留原尺寸。
export const $expressionBoost = computed(
  [$spriteState, $effectiveTier, $spatialLocale],
  (state, tier, locale) => state === 'emotional' && tier !== 'still' && locale !== 'perch'
)

let perchScaleLimit: number | null = null
let userInteracted = false
let roamTimer: ReturnType<typeof setTimeout> | null = null
let roaming = false

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

export function computePerchPlacement(geom: DesktopScreenRect, maxScale: number): PerchPlacement | null {
  return calculatePerchPlacement(geom, maxScale, getViewport(), contentBounds(), MIN_SCALE)
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

function computeTargetScale(): number {
  const base = $defaultScale.get()

  // 栖息缩身上限：空间不够时先保证舒适栖身
  const hasScaleLimit = $effectiveTier.get() !== 'still' && $spatialLocale.get() === 'perch'

  const cap = hasScaleLimit ? perchScaleLimit : null

  return cap !== null ? Math.min(base, cap) : base
}

function updateAdaptiveScale(): void {
  setScaleTarget(computeTargetScale())
}

// 撤销栖息缩放上限并立即重算缩放；updateAdaptiveScale 同步触发缩放监听，读取落点须在其后。
function clearPerchScaleLimit(): void {
  perchScaleLimit = null
  updateAdaptiveScale()
}

function applyDefaultScale(scale: number): number | null {
  if (!Number.isFinite(scale)) {
    return null
  }

  const clamped = clamp(scale, MIN_SCALE, MAX_SCALE)
  $defaultScale.set(clamped)
  persistString(SCALE_KEY, String(clamped))
  updateAdaptiveScale()

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

export function setSpatialLocale(locale: SpatialLocale, opts?: SpatialLocaleOptions): void {
  perchScaleLimit = locale === 'perch' ? (opts?.scaleLimit ?? null) : null
  $spatialLocale.set(locale)
  updateAdaptiveScale()
  const raw = opts?.position ?? $homePosition.get()
  const target = locale === 'home' ? clampPosToViewport(raw) : raw

  if (opts?.instant) {
    cancelMovement()
    $spatialPos.set(target)
    $spatialLocomotion.set('still')
  } else {
    moveTo(target, opts?.locomotion ?? (locale === 'target' ? 'fly' : 'walk'))
  }
}

export function updateSpatialDecision(): void {
  if ($spatialLocomotion.get() === 'drag' || !isSpriteStageShown()) {
    return
  }

  const tier = $effectiveTier.get()

  if (tier !== 'autonomous') {
    stopRoam()
  }

  if (tier === 'still' && $spatialLocale.get() !== 'home') {
    setSpatialLocale('home')
  }
}

function generateRoamWaypoint(): { x: number; y: number } {
  return calculateRoamWaypoint(getViewport(), $spatialScale.get())
}

export function startRoam(): void {
  if (roaming || !isSpriteStageShown()) {
    return
  }

  roaming = true
  $spatialLocale.set('roam')
  clearPerchScaleLimit()
  roamStep()
}

function roamStep(): void {
  moveTo(generateRoamWaypoint(), 'walk', () => {
    if (!roaming) {
      return
    }

    roamTimer = setTimeout(
      () => {
        roamTimer = null

        if (!roaming) {
          return
        }

        // 每次续行前重验空闲条件。
        if ($spriteState.get() !== 'idle' || $lastIdleSeconds.get() < ROAM_IDLE_THRESHOLD_SECONDS) {
          stopRoam()
          setSpatialLocale('home')

          return
        }

        roamStep()
      },
      5000 + Math.random() * 10000
    )
  })
}

function stopRoam(): void {
  roaming = false

  if (roamTimer !== null) {
    clearTimeout(roamTimer)
    roamTimer = null
  }

  cancelMovement()
}

export function startDrag(): void {
  userInteracted = true
  clearSpriteGesture()
  stopRoam()

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
  $spatialPos.set(safe)
  $homePosition.set(safe)
  $spatialLocomotion.set('still')

  if (!cancelled) {
    playSpriteGesture({ kind: 'land' })
  }

  setSpriteState('interacting', { durationMs: cancelled ? 0 : 500 })
  $spatialLocale.set('home')
  clearPerchScaleLimit()
  void shellApi().sprite.setPosition(safe)
}

export function resetToHomePosition(): void {
  userInteracted = false
  stopRoam()

  const home = getHomePosition()
  $homePosition.set(home)
  $spatialLocale.set('home')
  $spatialLocomotion.set('still')

  $spatialPos.set(home)
  void shellApi().sprite.setPosition(home)
}

export function initSpatial(): () => void {
  let disposed = false
  const offs: Array<() => void> = [shellApi().sprite.onDefaultScaleChanged(syncDefaultScale)]
  offs.push(
    registerStorageClearHandler(() => {
      userInteracted = false
      stopRoam()
      clearSpriteGesture()
    })
  )
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

        if ($spatialLocale.get() === 'home') {
          $spatialPos.set(next)
        }

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
    stopRoam()
    $spatialLocomotion.set('still')
  }

  let stageActive = !$screenLocked.get() && isSpriteStageShown()
  let stageEpoch = $presentation.get().stageEpoch

  const reevaluate = (): void => {
    const nextActive = !$screenLocked.get() && isSpriteStageShown()
    const nextEpoch = $presentation.get().stageEpoch
    const resumed = nextActive && (!stageActive || nextEpoch !== stageEpoch)
    stageActive = nextActive
    stageEpoch = nextEpoch

    if (!nextActive) {
      pauseStage()

      return
    }

    if (resumed && $spatialLocale.get() === 'perch') {
      setSpatialLocale('home')
    }

    updateAdaptiveScale()
    updateSpatialDecision()
  }

  offs.push(
    $presentation.listen(reevaluate),
    $surfaceOpen.listen(reevaluate),
    $surfaceSpriteVisible.listen(reevaluate),
    $screenLocked.listen(reevaluate),
    $spriteState.listen(reevaluate),
    $effectiveTier.listen(reevaluate)
  )

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

    if ($spatialLocale.get() === 'home') {
      setSpatialLocale('home', { instant: true })
    } else {
      clampCurrent()
    }
  }

  window.addEventListener('resize', resize)

  return () => {
    disposed = true
    stopRestore()
    offs.forEach(off => off())
    window.removeEventListener('resize', resize)
    stopRoam()
    motion.dispose()
  }
}
