import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import { persistString, registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { shellApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceOpen, $surfaceSpriteVisible, isSpriteStageShown } from '@/shared/store/surfaces'
import { type DesktopScreenRect, type DesktopSpriteScalePayload, SPRITE_SCALE_LIMITS } from '@ipc/contracts'
import { clamp } from '@runtime'

import { $actionCatalog, $activePlayInstance } from './actions'
import { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import { $effectiveTier, $spriteState, holdInteracting, setSpriteState } from './companion-store'
import { $llmAutonomy } from './prefs'
import type { SpatialPeek } from './spatial-peek'
import {
  baseSpriteSize,
  computePerchPlacement as calculatePerchPlacement,
  generateRoamWaypoint as calculateRoamWaypoint,
  clampPositionToViewport,
  computeHomePosition,
  dragScreenEdge,
  REST_MARGIN,
  spriteContentBox
} from './spatial/geometry'
import type { PerchPlacement } from './spatial/geometry'
import { createSpatialMotion } from './spatial/motion'
import { createSpatialPeekController } from './spatial/peek-controller'
import type {
  Locomotion,
  PeekPreparation,
  SpatialLocale,
  SpatialLocaleOptions,
  SpriteRect,
  ViewportSize
} from './spatial/types'
import { clearSpriteGesture, playSpriteGesture } from './sprite/gesture'

export { baseSpriteSize, computeOverlayAnchorBesideSprite } from './spatial/geometry'
export type { PerchPlacement } from './spatial/geometry'
export { locomotionForDistance } from './spatial/motion'
export type { Locomotion, PeekPreparation, SpriteRect, WindowPeekIntent } from './spatial/types'

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
export const $spatialPeek = atom<SpatialPeek | null>(null)
export const $peekPreparation = atom<PeekPreparation | null>(null)
export const $spriteCanvasRect = atom<{ left: number; top: number; right: number; bottom: number } | null>(null)

export const $viewport = atom<ViewportSize>({ width: window.innerWidth, height: window.innerHeight })

// 情绪放大：表达片段真实可见（emotional）时由形象层以脚底为原点放大；静止档、栖息与探身时不放大，保证不挤占目标与遮挡线。
export const $expressionBoost = computed(
  [$spriteState, $effectiveTier, $spatialLocale, $spatialPeek],
  (state, tier, locale, peek) =>
    state === 'emotional' &&
    tier !== 'still' &&
    peek === null &&
    locale !== 'perch' &&
    locale !== 'window_peek' &&
    locale !== 'screen_peek'
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
  hasPeek: () => $spatialPeek.get() !== null,
  clampPosition: position => clampPosToViewport(position),
  minScale: MIN_SCALE,
  maxScale: MAX_SCALE
})

const { cancelMovement, moveTo, setScaleTarget } = motion

const peekController = createSpatialPeekController({
  state: {
    $defaultScale,
    $homePosition,
    $peekPreparation,
    $spatialLocale,
    $spatialLocomotion,
    $spatialPeek,
    $spatialPos,
    $spatialScale,
    $spriteCanvasRect
  },
  minScale: MIN_SCALE,
  getViewport,
  isStageActive,
  cancelMovement,
  moveTo,
  setScaleTarget,
  computeTargetScale,
  clearPerchScaleLimit,
  setPerchScaleLimit: scale => {
    perchScaleLimit = scale
  },
  clampPosToViewport,
  computePerchPlacement,
  setSpatialLocale,
  settleHome,
  stopRoam
})

const {
  activateScreenPeek,
  applyScreenPeek,
  abandonPeekMode,
  cancelPeekPreparation,
  captureWindowPeekIntent,
  clearPeekState,
  commitPeekPreparation,
  enterWindowPeek,
  leavePeekForExpression,
  leaveWindowPeek,
  restorePeekAfterExpression,
  stopWindowPeekTracker,
  updateWindowPeek
} = peekController

export {
  cancelMovement,
  cancelPeekPreparation,
  captureWindowPeekIntent,
  commitPeekPreparation,
  enterWindowPeek,
  leavePeekForExpression,
  restorePeekAfterExpression
}

function getViewport(): ViewportSize {
  return { width: window.innerWidth, height: window.innerHeight }
}

// 探身流程的舞台前提：未锁屏且桌面精灵舞台展示中（未被完整入口收起、精灵窗未隐藏或最小化）。
function isStageActive(): boolean {
  return !$screenLocked.get() && isSpriteStageShown()
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
  const hasScaleLimit =
    $effectiveTier.get() !== 'still' && ($spatialLocale.get() === 'perch' || $spatialLocale.get() === 'window_peek')

  const cap = hasScaleLimit ? perchScaleLimit : null

  return cap !== null ? Math.min(base, cap) : base
}

function updateAdaptiveScale(): void {
  setScaleTarget(computeTargetScale())
}

// 撤销栖息/探身的缩放上限并立即重算缩放；updateAdaptiveScale 同步触发缩放监听，读取落点须在其后。
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

// 收回 home：撤销缩放上限并按新比例把落点钳回视口后静止；fromHome 时以 home 为落点，否则沿用当前位置。updateAdaptiveScale 会同步触发缩放监听，须先于读取落点。
function settleHome(fromHome: boolean): { x: number; y: number } {
  clearPerchScaleLimit()
  const position = clampPosToViewport(fromHome ? $homePosition.get() : $spatialPos.get())
  $spatialLocale.set('home')
  $spatialPos.set(position)
  $spatialLocomotion.set('still')

  return position
}

// 离开探身后按当前目标比例把位置钳回视口：窗口探身回 home，屏幕探身沿用当前位置。
function clampAfterPeek(previousPeek: SpatialPeek | null): void {
  if (previousPeek) {
    const raw = previousPeek.mode === 'window' ? $homePosition.get() : $spatialPos.get()
    $spatialPos.set(clampPosToViewport(raw, computeTargetScale()))
  }
}

export function setSpatialLocale(locale: SpatialLocale, opts?: SpatialLocaleOptions): void {
  if (locale === 'home' && peekController.screenEdgeHome) {
    if ($spatialLocale.get() === 'screen_peek' || $peekPreparation.get()?.mode === 'screen') {
      return
    }

    abandonPeekMode()
    stopRoam()
    $spatialLocale.set('home')
    clearPerchScaleLimit()
    const home = clampPosToViewport($homePosition.get(), computeTargetScale())

    const returnToEdge = (): void => {
      if (peekController.screenEdgeHome) {
        void activateScreenPeek(peekController.screenEdgeHome, !opts?.instant)
      }
    }

    if (opts?.instant) {
      $spatialPos.set(home)
      returnToEdge()
    } else {
      moveTo(home, opts?.locomotion ?? 'walk', returnToEdge)
    }

    return
  }

  // 非探身落点先撤销探身；perch 保留已在途的窗口探身意图。
  const previousPeek =
    locale !== 'screen_peek' && locale !== 'window_peek'
      ? clearPeekState(locale === 'perch' && peekController.hasPendingWindowPeek())
      : null

  const hasScaleLimit = locale === 'perch' || locale === 'window_peek'
  const limitChanged = perchScaleLimit !== (hasScaleLimit ? (opts?.scaleLimit ?? null) : null)
  perchScaleLimit = hasScaleLimit ? (opts?.scaleLimit ?? null) : null
  $spatialLocale.set(locale)

  if (limitChanged) {
    updateAdaptiveScale()
  }

  clampAfterPeek(previousPeek)

  // home 落点可能记录于更低 scale 的时期；按当前 scale 重钳，情绪放大期间回 home 不裁脚。
  const rawTarget = opts?.position ?? $homePosition.get()

  const target = locale === 'home' ? clampPosToViewport(rawTarget) : rawTarget

  const locomotion = opts?.locomotion ?? (locale === 'target' ? 'fly' : 'walk')

  if (opts?.instant) {
    cancelMovement()
    $spatialPos.set(target)
    $spatialLocomotion.set('still')
  } else {
    moveTo(target, locomotion)
  }
}

export function updateSpatialDecision(): void {
  if ($spatialLocomotion.get() === 'drag' || !isSpriteStageShown()) {
    return
  }

  if (peekController.hasExpressionReturn() || $activePlayInstance.get()) {
    return
  }

  const tier = $effectiveTier.get()
  const preparation = $peekPreparation.get()

  if (preparation?.mode === 'window' && tier !== 'autonomous') {
    cancelPeekPreparation(preparation.action, preparation.generation)

    return
  }

  if (preparation) {
    return
  }

  const state = $spriteState.get()

  // 静止档恢复栖息地；常规档只停止自主移动。
  if (tier === 'still') {
    stopRoam()

    if ($spatialLocale.get() === 'window_peek') {
      leaveWindowPeek()
    }

    if ($spatialLocale.get() !== 'home' && $spatialLocale.get() !== 'screen_peek') {
      setSpatialLocale('home')
    }

    return
  }

  if (tier !== 'autonomous') {
    stopRoam()

    peekController.cancelPendingWindowPeek()

    if ($spatialLocale.get() === 'window_peek') {
      leaveWindowPeek()
    }

    return
  }

  if ($spatialLocale.get() === 'window_peek') {
    return
  }

  if ($llmAutonomy.get()) {
    return
  }

  const ctx = $focusContext.get()
  const canPerch = ctx?.windowGeom && ctx.category !== 'unknown' && ctx.category !== 'gaming' && !ctx.fullscreen

  if (canPerch) {
    stopRoam()

    if ($spatialLocale.get() !== 'perch' && $spatialLocale.get() !== 'window_peek' && state === 'idle') {
      void enterWindowPeek()
    }

    return
  }

  if (state === 'idle' && $lastIdleSeconds.get() >= ROAM_IDLE_THRESHOLD_SECONDS) {
    // 暂停（收起、隐藏或降档）后 locale 可能仍是 roam 但漫游已停；startRoam 对进行中的漫游幂等。
    startRoam()

    return
  }

  stopRoam()

  if ($spatialLocale.get() === 'perch' || $spatialLocale.get() === 'roam') {
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
  const previousPeek = clearPeekState()
  $spatialLocale.set('roam')
  clearPerchScaleLimit()
  clampAfterPeek(previousPeek)
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
  clearPeekState()
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

  const side = dragScreenEdge(pos, getViewport(), contentBounds(), $spatialScale.get(), cancelled)

  $spatialPos.set(safe)
  $homePosition.set(safe)
  $spatialLocomotion.set('still')

  // 落地形变只用于普通落位；贴边释放由探身过渡承接。
  if (!cancelled && !side) {
    playSpriteGesture({ kind: 'land' })
  }

  setSpriteState('interacting', { durationMs: cancelled ? 0 : 500 })
  $spatialLocale.set('home')
  clearPerchScaleLimit()

  if (side) {
    const yRatio = safe.y / Math.max(1, window.innerHeight)
    peekController.screenEdgeHome = { side, yRatio }
    void shellApi().sprite.setPosition({ ...safe, screenEdge: { side, yRatio } })
    void activateScreenPeek(peekController.screenEdgeHome, true)

    return
  }

  if (!cancelled) {
    peekController.screenEdgeHome = null
  } else if (peekController.screenEdgeHome) {
    void activateScreenPeek(peekController.screenEdgeHome)

    return
  }

  void shellApi().sprite.setPosition(safe)
}

export function resetToHomePosition(): void {
  userInteracted = false
  clearPeekState()
  peekController.screenEdgeHome = null
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
  const offs: Array<() => void> = []

  offs.push(shellApi().sprite.onDefaultScaleChanged(syncDefaultScale))

  offs.push(
    registerStorageClearHandler(() => {
      abandonPeekMode()
      peekController.screenEdgeHome = null
      userInteracted = false
    })
  )

  // 等待可见内容包围盒后恢复；保存位置超出可见区域时收回并回写。
  const restoreSavedPosition = (saved: {
    x: number
    y: number
    screenEdge?: { side: 'left' | 'right'; yRatio: number }
  }): void => {
    if (disposed || userInteracted) {
      return
    }

    const next = clampPosToViewport(saved)
    $homePosition.set(next)

    if ($spatialLocale.get() === 'home') {
      $spatialPos.set(next)
    }

    if (next.x !== saved.x || next.y !== saved.y) {
      void shellApi().sprite.setPosition({
        ...next,
        ...(saved.screenEdge ? { screenEdge: saved.screenEdge } : {})
      })
    }

    if (saved.screenEdge) {
      peekController.screenEdgeHome = saved.screenEdge
      void activateScreenPeek(saved.screenEdge)
    }
  }

  let unlistenSavedRect: (() => void) | null = null
  let savedRectTimer: ReturnType<typeof setTimeout> | null = null

  const settleSavedRectWait = (): void => {
    if (unlistenSavedRect) {
      unlistenSavedRect()
      unlistenSavedRect = null
    }

    if (savedRectTimer) {
      clearTimeout(savedRectTimer)
      savedRectTimer = null
    }
  }

  void shellApi()
    .sprite.getPosition()
    .then(saved => {
      if (disposed || !saved || userInteracted) {
        return
      }

      if ($spriteContentRect.get()) {
        restoreSavedPosition(saved)

        return
      }

      unlistenSavedRect = $spriteContentRect.listen(() => {
        if (!$spriteContentRect.get()) {
          return
        }

        settleSavedRectWait()
        restoreSavedPosition(saved)
      })

      savedRectTimer = setTimeout(() => {
        savedRectTimer = null
        settleSavedRectWait()
        restoreSavedPosition(saved)
      }, 3000)
    })
    .catch(error => {
      log.warn('spatial', 'Could not restore saved position', error)
      settleSavedRectWait()
    })

  // 桌面舞台收起、精灵窗隐藏或最小化时暂停走位、漫游与探身；重新展示后收回栖身、恢复贴边并重新裁决。
  const pauseDesktopStage = (): void => {
    peekController.invalidateIntent()
    clearSpriteGesture()

    if ($spatialPeek.get() || peekController.hasPendingWindowPeek() || $peekPreparation.get()) {
      abandonPeekMode()
    }

    stopRoam()
    cancelMovement()
    $spatialLocomotion.set('still')
  }

  const resumeDesktopStage = (): void => {
    if (!isSpriteStageShown()) {
      return
    }

    if (peekController.screenEdgeHome) {
      void activateScreenPeek(peekController.screenEdgeHome)
    }

    if ($spatialLocale.get() === 'perch') {
      setSpatialLocale('home')
    }

    updateSpatialDecision()
  }

  let previousStageVisible = isSpriteStageShown()
  let previousStageEpoch = $presentation.get().stageEpoch
  offs.push(
    $presentation.listen(state => {
      const visible = isSpriteStageShown()

      if (visible === previousStageVisible && state.stageEpoch === previousStageEpoch) {
        return
      }

      previousStageVisible = visible
      previousStageEpoch = state.stageEpoch

      if (visible) {
        resumeDesktopStage()
      } else {
        pauseDesktopStage()
      }
    })
  )

  offs.push(
    $surfaceOpen.listen(open => {
      if (open) {
        pauseDesktopStage()
      } else {
        resumeDesktopStage()
      }
    })
  )

  offs.push(
    $surfaceSpriteVisible.listen(visible => {
      if (visible) {
        resumeDesktopStage()
      } else {
        pauseDesktopStage()
      }
    })
  )

  const reevaluate = (): void => {
    updateAdaptiveScale()
    updateSpatialDecision()
  }

  offs.push($spriteState.listen(reevaluate))
  offs.push($effectiveTier.listen(reevaluate))
  offs.push($focusContext.listen(() => updateSpatialDecision()))

  offs.push(
    $screenLocked.listen(locked => {
      if (locked) {
        abandonPeekMode()
      } else {
        if (peekController.screenEdgeHome) {
          void activateScreenPeek(peekController.screenEdgeHome)
        }

        updateSpatialDecision()
      }
    })
  )

  // 拖拽逐帧限制位置；移动完成时按最终比例重新落位。
  offs.push(
    $spatialScale.listen(() => {
      if ($spatialLocomotion.get() === 'drag' || motion.isMoving()) {
        return
      }

      const locale = $spatialLocale.get()

      if (locale === 'screen_peek' && peekController.screenEdgeHome) {
        const action = peekController.screenEdgeHome.side === 'right' ? 'peek_left' : 'peek_right'
        const geometry = $actionCatalog.get()?.clipsBySlot.get(action)?.peek_geometry

        if (geometry) {
          applyScreenPeek(peekController.screenEdgeHome, geometry, false)
        }

        return
      }

      if (locale === 'window_peek') {
        void updateWindowPeek()

        return
      }

      const cur = $spatialPos.get()
      const next = clampPosToViewport(cur)

      $spatialPos.set(next)
    })
  )

  // 片段切换只重钳当前位置，不能把表演落点或离开探身的路径拉回 home。
  offs.push(
    $spriteContentRect.listen(() => {
      if (!peekController.screenEdgeHome) {
        $homePosition.set(clampPosToViewport($homePosition.get()))
      }

      if (!$spatialPeek.get() && $spatialLocomotion.get() === 'still' && !motion.isMoving()) {
        $spatialPos.set(clampPosToViewport($spatialPos.get()))
      }
    })
  )

  offs.push(
    $spriteCanvasRect.listen(() => {
      const peek = $spatialPeek.get()

      if (peek?.mode === 'screen' && peekController.screenEdgeHome) {
        const geometry = $actionCatalog.get()?.clipsBySlot.get(peek.action)?.peek_geometry

        if (geometry) {
          applyScreenPeek(peekController.screenEdgeHome, geometry, false)
        }
      } else if (peek?.mode === 'window') {
        void updateWindowPeek()
      } else {
        peekController.restoreScreenDock()
      }
    })
  )

  let previousCatalog = $actionCatalog.get()

  offs.push(
    $actionCatalog.listen(catalog => {
      const previous = previousCatalog
      previousCatalog = catalog
      peekController.handleCatalogChanged(previous, catalog)
    })
  )

  const onResize = () => {
    $viewport.set({ width: window.innerWidth, height: window.innerHeight })

    // 跨屏拖拽由指针路径重映射位置，resize 不接管。
    if ($spatialLocomotion.get() === 'drag') {
      return
    }

    const home = $homePosition.get()
    const c = contentBox()

    const clamped = {
      x: clamp(home.x, REST_MARGIN, window.innerWidth - c.right - REST_MARGIN),
      y: clamp(home.y, -c.top, window.innerHeight - c.bottom)
    }

    $homePosition.set(clamped)

    // 跟随目标跨屏也会触发 resize；保留已冻结的窗口和首帧准备代次。
    if ($peekPreparation.get()?.mode === 'window' || peekController.hasPendingWindowPeek()) {
      $spatialPos.set(clampPosToViewport($spatialPos.get()))

      return
    }

    const locale = $spatialLocale.get()

    if (locale === 'home') {
      setSpatialLocale('home', { instant: true })
    } else if (locale === 'screen_peek' && peekController.screenEdgeHome) {
      const action = peekController.screenEdgeHome.side === 'right' ? 'peek_left' : 'peek_right'
      const geometry = $actionCatalog.get()?.clipsBySlot.get(action)?.peek_geometry

      if (geometry) {
        applyScreenPeek(peekController.screenEdgeHome, geometry, true)
      } else {
        void activateScreenPeek(peekController.screenEdgeHome)
      }
    } else if (locale === 'window_peek') {
      void updateWindowPeek()
    }
  }

  window.addEventListener('resize', onResize)

  return () => {
    disposed = true
    abandonPeekMode()
    settleSavedRectWait()
    offs.forEach(off => off())
    stopWindowPeekTracker()
    window.removeEventListener('resize', onResize)
    stopRoam()
    cancelMovement()

    motion.dispose()
  }
}
