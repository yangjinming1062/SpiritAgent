import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
import type { DesktopScreenRect, DesktopWindowSceneSnapshot } from '@ipc/contracts'
import { clamp } from '@runtime'

import { $actionCatalog, $activePlayInstance, ensurePeekAction } from '../actions'
import type { ActiveActionCatalog, PeekGeometry } from '../actions'
import { $focusContext } from '../activity'
import { $effectiveTier } from '../companion-store'
import { computeScreenPeekLayout, computeWindowPeekLayout } from '../spatial-peek'
import type { SpatialPeek, WindowPeekLayout } from '../spatial-peek'

import { baseSpriteSize } from './geometry'
import type { PerchPlacement } from './geometry'
import type {
  PeekPreparation,
  Position,
  ScreenEdge,
  SpatialLocale,
  SpatialLocaleOptions,
  SpatialState,
  ViewportSize,
  WindowPeekBinding,
  WindowPeekIntent
} from './types'
import { createWindowPeekTracker, findBoundWindow } from './window-tracker'

interface PeekControllerPorts {
  state: SpatialState
  minScale: number
  getViewport: () => ViewportSize
  isStageActive: () => boolean
  cancelMovement: () => void
  moveTo: (position: Position, locomotion: 'walk' | 'fly', onArrive?: () => void) => void
  setScaleTarget: (scale: number, instant?: boolean) => void
  computeTargetScale: () => number
  clearPerchScaleLimit: () => void
  setPerchScaleLimit: (scale: number) => void
  clampPosToViewport: (position: Position, scale?: number) => Position
  computePerchPlacement: (geometry: DesktopScreenRect, maxScale: number) => PerchPlacement | null
  setSpatialLocale: (locale: SpatialLocale, options?: SpatialLocaleOptions) => void
  settleHome: (fromHome: boolean) => Position
  stopRoam: () => void
}

interface PendingWindowPeek extends WindowPeekBinding {
  action: 'peek_left' | 'peek_right'
  generation: number
}

/** 探身准备、表演返回目标和意图代次只有一个所有者；播放器仍负责素材首帧。 */
export function createSpatialPeekController(ports: PeekControllerPorts) {
  const {
    $defaultScale,
    $homePosition,
    $peekPreparation,
    $spatialLocale,
    $spatialLocomotion,
    $spatialPeek,
    $spatialPos,
    $spatialScale,
    $spriteCanvasRect
  } = ports.state

  const {
    cancelMovement,
    moveTo,
    setScaleTarget,
    computeTargetScale,
    clearPerchScaleLimit,
    clampPosToViewport,
    computePerchPlacement,
    setSpatialLocale,
    settleHome,
    stopRoam,
    isStageActive
  } = ports

  const MIN_SCALE = ports.minScale
  let peekIntentGeneration = 0
  let screenEdgeHome: ScreenEdge | null = null
  let expressionExitToken = 0
  let expressionPeekReturn: { peek: SpatialPeek | PeekPreparation; packId: number } | null = null
  let pendingWindowPeek: PendingWindowPeek | null = null

  const getBaseSpriteWidth = (): number => baseSpriteSize(ports.getViewport().height).width
  const getBaseSpriteHeight = (): number => baseSpriteSize(ports.getViewport().height).height

  const tracker = createWindowPeekTracker({
    getPeek: () => $spatialPeek.get(),
    getPosition: () => $spatialPos.get(),
    getScale: () => $spatialScale.get(),
    getGeneration: () => peekIntentGeneration,
    isStageActive,
    readScene: readWindowScene,
    moveToDisplay: position => shellApi().sprite.moveToDisplay(position),
    buildLayout: buildWindowPeekLayout,
    applyLayout: layout => {
      ports.setPerchScaleLimit(layout.scale)
      setScaleTarget(layout.scale, true)
      $spatialPeek.set(layout.peek)
      $spatialPos.set(layout.position)
    },
    leave: leaveWindowPeek
  })

  function sameScreenEdge(
    a: { side: 'left' | 'right'; yRatio: number },
    b: { side: 'left' | 'right'; yRatio: number }
  ): boolean {
    return a.side === b.side && a.yRatio === b.yRatio
  }

  function applyScreenPeek(
    target: { side: 'left' | 'right'; yRatio: number },
    geometry: PeekGeometry,
    persist: boolean,
    animate = false
  ): void {
    const canvasRect = $spriteCanvasRect.get()

    if (!geometry || !canvasRect) {
      return
    }

    const { peek, position } = computeScreenPeekLayout(
      target,
      geometry,
      canvasRect,
      { width: ports.getViewport().width, height: ports.getViewport().height },
      { width: getBaseSpriteWidth(), height: getBaseSpriteHeight() },
      $spatialScale.get()
    )

    cancelMovement()
    $spatialPeek.set(peek)
    $spatialLocale.set('screen_peek')
    $homePosition.set(position)

    const savePosition = (savedPosition: { x: number; y: number } = position): void => {
      void shellApi().sprite.setPosition({
        ...savedPosition,
        screenEdge: { side: target.side, yRatio: target.yRatio }
      })
    }

    if (animate) {
      moveTo(position, 'fly', () => {
        const latestGeometry = $actionCatalog
          .get()
          ?.clipsBySlot.get(target.side === 'right' ? 'peek_left' : 'peek_right')?.peek_geometry

        if (latestGeometry && screenEdgeHome && sameScreenEdge(screenEdgeHome, target)) {
          applyScreenPeek(target, latestGeometry, false)
        }

        if (persist) {
          savePosition($spatialPos.get())
        }
      })
    } else {
      $spatialPos.set(position)
      $spatialLocomotion.set('still')

      if (persist) {
        savePosition()
      }
    }
  }

  async function activateScreenPeek(
    target: { side: 'left' | 'right'; yRatio: number },
    animate = false
  ): Promise<void> {
    if (!isStageActive()) {
      return
    }

    const action = target.side === 'right' ? 'peek_left' : 'peek_right'
    const existing = $peekPreparation.get()

    if (
      existing?.mode === 'screen' &&
      existing.action === action &&
      sameScreenEdge(existing.screenEdge, target) &&
      existing.packId === $actionCatalog.get()?.packId
    ) {
      return
    }

    const generation = ++peekIntentGeneration
    const ready = await ensurePeekAction(action)

    if (!ready || generation !== peekIntentGeneration || !screenEdgeHome || !sameScreenEdge(screenEdgeHome, target)) {
      return
    }

    const catalog = $actionCatalog.get()

    if (catalog?.clipsBySlot.get(action)?.peek_geometry) {
      $peekPreparation.set({ action, animate, generation, mode: 'screen', packId: catalog.packId, screenEdge: target })
    }
  }

  async function readWindowScene(): Promise<DesktopWindowSceneSnapshot | null> {
    return shellApi()
      .sprite.getWindowScene()
      .catch(error => {
        log.warn('spatial', 'Could not read window scene', error)

        return null
      })
  }

  function buildWindowPeekLayout(
    scene: DesktopWindowSceneSnapshot,
    target: DesktopWindowSceneSnapshot['windows'][number],
    action: 'peek_left' | 'peek_right'
  ): WindowPeekLayout | null {
    return computeWindowPeekLayout(
      scene,
      target,
      action,
      $actionCatalog.get()?.clipsBySlot.get(action),
      $spriteCanvasRect.get(),
      getBaseSpriteWidth(),
      getBaseSpriteHeight(),
      $defaultScale.get(),
      MIN_SCALE
    )
  }

  // 窗口探身的 canEnterWindowPeek 已含锁屏、舞台与播放判断，屏幕探身单独检查。
  function isCurrentPeekPreparation(preparation: PeekPreparation): boolean {
    return (
      $peekPreparation.get() === preparation &&
      preparation.generation === peekIntentGeneration &&
      $actionCatalog.get()?.packId === preparation.packId &&
      (preparation.mode === 'screen' ? isStageActive() && !$activePlayInstance.get() : canEnterWindowPeek())
    )
  }

  function cancelPeekPreparation(action: 'peek_left' | 'peek_right', generation?: number): void {
    const preparation = $peekPreparation.get()

    if (
      !preparation ||
      preparation.action !== action ||
      (generation !== undefined && preparation.generation !== generation)
    ) {
      return
    }

    $peekPreparation.set(null)
    peekIntentGeneration += 1

    if (preparation.mode === 'window' && screenEdgeHome && isStageActive()) {
      void activateScreenPeek(screenEdgeHome)
    }
  }

  /** 素材就绪后同步提交探身位置、遮挡与播放器。 */
  async function commitPeekPreparation(
    action: 'peek_left' | 'peek_right',
    generation: number,
    prepareFirstFrame: () => Promise<boolean>,
    showFirstFrame: () => void
  ): Promise<boolean> {
    const preparation = $peekPreparation.get()

    if (!preparation || preparation.action !== action || preparation.generation !== generation) {
      return false
    }

    const abort = (): false => {
      cancelPeekPreparation(action, generation)

      return false
    }

    if (!isCurrentPeekPreparation(preparation)) {
      return abort()
    }

    const catalog = $actionCatalog.get()
    const geometry = catalog?.clipsBySlot.get(action)?.peek_geometry

    if (!geometry) {
      return abort()
    }

    if (preparation.mode === 'screen' && (!screenEdgeHome || !sameScreenEdge(screenEdgeHome, preparation.screenEdge))) {
      return abort()
    }

    // 解码失败不能先移动宿主窗口；后续窗口快照也须取自解码完成后。
    if (!(await prepareFirstFrame())) {
      return abort()
    }

    if (!isCurrentPeekPreparation(preparation)) {
      return false
    }

    if (preparation.mode === 'screen') {
      applyScreenPeek(preparation.screenEdge, geometry, true, preparation.animate)
      $peekPreparation.set(null)
      showFirstFrame()

      return true
    }

    let scene = await readWindowScene()
    let target = findBoundWindow(scene, preparation)

    if (!isCurrentPeekPreparation(preparation) || !scene || !target) {
      return abort()
    }

    if (target.displayId !== scene.viewport.displayId) {
      await shellApi().sprite.moveToDisplay({ x: target.x + target.w / 2, y: target.y + target.h / 2 })
      scene = await readWindowScene()
      target = findBoundWindow(scene, preparation)

      if (
        !isCurrentPeekPreparation(preparation) ||
        !scene ||
        !target ||
        target.displayId !== scene.viewport.displayId
      ) {
        return abort()
      }
    }

    const layout = buildWindowPeekLayout(scene, target, action)

    if (!layout || !isCurrentPeekPreparation(preparation)) {
      const tryOtherSide = !layout && action === 'peek_right' && isCurrentPeekPreparation(preparation)
      cancelPeekPreparation(action, generation)

      if (tryOtherSide) {
        preparePendingWindowPeek({
          action: 'peek_left',
          generation: ++peekIntentGeneration,
          windowId: preparation.windowId,
          windowPid: preparation.windowPid,
          runnerInstanceId: preparation.runnerInstanceId
        })
      }

      return false
    }

    tracker.stop()
    ports.setPerchScaleLimit(layout.scale)
    setScaleTarget(layout.scale, true)
    $spatialPeek.set(layout.peek)
    setSpatialLocale('window_peek', { instant: true, position: layout.position, scaleLimit: layout.scale })
    $peekPreparation.set(null)
    tracker.start()
    showFirstFrame()

    return true
  }

  /** 撤销探身意图、表演返回目标与窗口跟踪，保留栖息地和当前位置；keepPendingWindow 时保留待启动的窗口探身意图及其代次。 */
  function clearPeekState(keepPendingWindow = false): SpatialPeek | null {
    const peek = $spatialPeek.get()

    if (!keepPendingWindow) {
      peekIntentGeneration += 1
      pendingWindowPeek = null
    }

    expressionExitToken += 1
    expressionPeekReturn = null
    $peekPreparation.set(null)
    tracker.stop()
    $spatialPeek.set(null)

    return peek
  }

  function abandonPeekMode(): void {
    const peek = clearPeekState()
    const wasPeeking = peek !== null || $spatialLocale.get() === 'screen_peek' || $spatialLocale.get() === 'window_peek'

    if (!wasPeeking) {
      return
    }

    cancelMovement()

    const position = settleHome(peek?.mode === 'window')

    void shellApi().sprite.setPosition({
      ...position,
      ...(screenEdgeHome ? { screenEdge: screenEdgeHome } : {})
    })
  }

  function leaveWindowPeek(): void {
    clearPeekState()
    settleHome(true)

    if (screenEdgeHome) {
      void activateScreenPeek(screenEdgeHome, true)
    }
  }

  let activatingWindowPeek = false

  function preparePendingWindowPeek(intent: PendingWindowPeek): void {
    pendingWindowPeek = intent
    void ensurePeekAction(intent.action).then(ready => {
      if (pendingWindowPeek !== intent || intent.generation !== peekIntentGeneration) {
        return
      }

      if (ready) {
        void tryStartPendingWindowPeek()
      } else {
        pendingWindowPeek = null

        if ($spatialLocale.get() === 'home' && screenEdgeHome) {
          void activateScreenPeek(screenEdgeHome)
        }
      }
    })
  }

  async function tryStartPendingWindowPeek(): Promise<boolean> {
    const intent = pendingWindowPeek

    if (activatingWindowPeek || !intent || intent.generation !== peekIntentGeneration || !isStageActive()) {
      return false
    }

    const clip = $actionCatalog.get()?.clipsBySlot.get(intent.action)
    const packId = $actionCatalog.get()?.packId

    if (!clip?.peek_geometry || packId === undefined) {
      return false
    }

    activatingWindowPeek = true

    try {
      const scene = await readWindowScene()

      if (pendingWindowPeek !== intent || intent.generation !== peekIntentGeneration) {
        return false
      }

      const target = findBoundWindow(scene, intent)

      if (!scene || !target) {
        pendingWindowPeek = null

        return false
      }

      if (target.displayId === scene.viewport.displayId && !buildWindowPeekLayout(scene, target, intent.action)) {
        if (intent.action === 'peek_right') {
          preparePendingWindowPeek({ ...intent, action: 'peek_left' })
        } else {
          pendingWindowPeek = null
        }

        return false
      }

      pendingWindowPeek = null
      stopRoam()
      tracker.stop()

      if ($spatialPeek.get() || $peekPreparation.get()) {
        const previousPeek = $spatialPeek.get()
        $spatialPeek.set(null)
        $peekPreparation.set(null)
        $spatialLocale.set('home')
        $spatialPos.set(clampPosToViewport(previousPeek?.mode === 'window' ? $homePosition.get() : $spatialPos.get()))
        clearPerchScaleLimit()
      }

      $peekPreparation.set({
        action: intent.action,
        generation: intent.generation,
        mode: 'window',
        packId,
        runnerInstanceId: intent.runnerInstanceId,
        windowId: intent.windowId,
        windowPid: intent.windowPid
      })

      return true
    } catch (error) {
      log.warn('spatial', 'Could not prepare window peek', error)

      if (pendingWindowPeek === intent) {
        pendingWindowPeek = null
      }

      return false
    } finally {
      activatingWindowPeek = false
    }
  }

  function canEnterWindowPeek(): boolean {
    return (
      isStageActive() &&
      $effectiveTier.get() === 'autonomous' &&
      $spatialLocomotion.get() !== 'drag' &&
      !$activePlayInstance.get() &&
      !$focusContext.get()?.fullscreen
    )
  }

  // 冻结的探身决策仍有效：仍可进入窗口探身，且意图代次与动作包均未变化。
  function isPeekIntentCurrent(generation: number, packId: number): boolean {
    return canEnterWindowPeek() && generation === peekIntentGeneration && packId === $actionCatalog.get()?.packId
  }

  /** 冻结决策发起时的窗口，避免迟到结果跟随新焦点。 */
  async function captureWindowPeekIntent(): Promise<WindowPeekIntent | null> {
    const category = $focusContext.get()?.category
    const generation = peekIntentGeneration
    const packId = $actionCatalog.get()?.packId

    if (!canEnterWindowPeek() || category === 'unknown' || category === 'gaming' || packId === undefined) {
      return null
    }

    const scene = await readWindowScene()
    const focused = scene?.windows.find(item => item.focused && item.visible)

    if (!scene || !focused || !isPeekIntentCurrent(generation, packId)) {
      return null
    }

    return {
      windowId: focused.id,
      windowPid: focused.pid,
      runnerInstanceId: scene.runnerInstanceId,
      packId,
      generation
    }
  }

  async function enterWindowPeek(captured?: WindowPeekIntent): Promise<boolean> {
    const target = captured ?? (await captureWindowPeekIntent())

    if (!target || !isPeekIntentCurrent(target.generation, target.packId)) {
      return false
    }

    const activePeek = $spatialPeek.get()
    const preparation = $peekPreparation.get()

    if (activePeek?.mode === 'window' || preparation?.mode === 'window' || pendingWindowPeek) {
      return true
    }

    const scene = await readWindowScene()

    const focused = findBoundWindow(scene, target)

    if (!scene || !focused || !isPeekIntentCurrent(target.generation, target.packId)) {
      return false
    }

    const intent: PendingWindowPeek = {
      action: 'peek_right',
      generation: ++peekIntentGeneration,
      windowId: focused.id,
      windowPid: focused.pid,
      runnerInstanceId: scene.runnerInstanceId
    }

    preparePendingWindowPeek(intent)
    stopRoam()

    const perch =
      focused.displayId === scene.viewport.displayId
        ? computePerchPlacement(
            { x: focused.x - scene.viewport.x, y: focused.y - scene.viewport.y, w: focused.w, h: focused.h },
            $defaultScale.get()
          )
        : null

    setSpatialLocale('perch', {
      position: perch?.pos ?? clampPosToViewport($spatialPos.get()),
      scaleLimit: perch?.scale
    })

    return true
  }

  function leavePeekForExpression(): boolean {
    const peek = $spatialPeek.get() ?? $peekPreparation.get()
    const packId = $actionCatalog.get()?.packId

    if (!peek || packId === undefined) {
      return false
    }

    if (expressionPeekReturn) {
      return true
    }

    const token = ++expressionExitToken
    expressionPeekReturn = { peek, packId }
    peekIntentGeneration += 1
    pendingWindowPeek = null
    $peekPreparation.set(null)
    tracker.stop()

    if (!$spatialPeek.get()) {
      return true
    }

    $spatialLocale.set('home')
    clearPerchScaleLimit()
    const targetScale = computeTargetScale()
    const activePeek = $spatialPeek.get()

    const visiblePosition =
      activePeek?.mode === 'window' ? computePerchPlacement(activePeek.targetRect, targetScale)?.pos : null

    const raw = visiblePosition ?? $spatialPos.get()
    // 表达可能比探身更宽，离开遮挡时按完整画布预留空间。
    const canvas = $spriteCanvasRect.get()
    const width = getBaseSpriteWidth() * targetScale
    const height = getBaseSpriteHeight() * targetScale

    const destination = {
      x: clamp(raw.x, -(canvas?.left ?? 0) * width, ports.getViewport().width - (canvas?.right ?? 1) * width),
      y: clamp(raw.y, -(canvas?.top ?? 0) * height, ports.getViewport().height - (canvas?.bottom ?? 1) * height)
    }

    moveTo(destination, 'fly', () => {
      if (token === expressionExitToken && expressionPeekReturn) {
        $spatialPeek.set(null)
        $spatialLocale.set('home')
      }
    })

    return true
  }

  async function restorePeekAfterExpression(): Promise<void> {
    const previous = expressionPeekReturn
    expressionPeekReturn = null
    const token = ++expressionExitToken

    if (
      !previous ||
      previous.packId !== $actionCatalog.get()?.packId ||
      !isStageActive() ||
      $spatialLocomotion.get() === 'drag'
    ) {
      return
    }

    if (previous.peek.mode === 'screen') {
      if (screenEdgeHome) {
        await activateScreenPeek(screenEdgeHome, true)
      }

      return
    }

    if (!canEnterWindowPeek()) {
      return
    }

    const scene = await readWindowScene()

    if (token !== expressionExitToken || !canEnterWindowPeek()) {
      return
    }

    const target = findBoundWindow(scene, previous.peek)

    if (!scene || !target) {
      setSpatialLocale('home', { instant: true })

      return
    }

    const layout = buildWindowPeekLayout(scene, target, previous.peek.action)

    if (target.displayId === scene.viewport.displayId && !layout) {
      setSpatialLocale('home', { instant: true })

      return
    }

    const packId = $actionCatalog.get()?.packId

    if (packId === undefined) {
      return
    }

    const generation = ++peekIntentGeneration
    $peekPreparation.set({
      action: previous.peek.action,
      generation,
      mode: 'window',
      packId,
      runnerInstanceId: scene.runnerInstanceId,
      windowId: target.id,
      windowPid: target.pid
    })
  }

  const restoreScreenDock = (): void => {
    if (
      screenEdgeHome &&
      $spatialLocale.get() === 'home' &&
      !$spatialPeek.get() &&
      !$peekPreparation.get() &&
      !expressionPeekReturn &&
      !$activePlayInstance.get() &&
      $spatialLocomotion.get() === 'still'
    ) {
      void activateScreenPeek(screenEdgeHome)
    }
  }

  const reprepareWindowPeek = (
    peek: Pick<Extract<SpatialPeek, { mode: 'window' }>, 'action' | 'runnerInstanceId' | 'windowId' | 'windowPid'>
  ): void => {
    clearPeekState()
    settleHome(true)

    if (!peek.windowId || !peek.windowPid || !peek.runnerInstanceId) {
      return
    }

    const intent = {
      action: peek.action,
      generation: ++peekIntentGeneration,
      windowId: peek.windowId,
      windowPid: peek.windowPid,
      runnerInstanceId: peek.runnerInstanceId
    }

    preparePendingWindowPeek(intent)
  }

  function handleCatalogChanged(previous: ActiveActionCatalog | null, catalog: ActiveActionCatalog | null): void {
    if (previous?.packId !== catalog?.packId) {
      abandonPeekMode()
      restoreScreenDock()

      return
    }

    const peek = $spatialPeek.get()
    const preparation = $peekPreparation.get()
    const action = peek?.action ?? preparation?.action
    const before = action ? previous?.clipsBySlot.get(action) : null
    const after = action ? catalog?.clipsBySlot.get(action) : null

    if (before && after && before.asset_revision === after.asset_revision && before.media_ref === after.media_ref) {
      return
    }

    if (preparation?.mode === 'screen' && screenEdgeHome) {
      cancelPeekPreparation(preparation.action, preparation.generation)
      void activateScreenPeek(screenEdgeHome)
    } else if (preparation?.mode === 'window') {
      reprepareWindowPeek({
        action: preparation.action,
        runnerInstanceId: preparation.runnerInstanceId,
        windowId: preparation.windowId,
        windowPid: preparation.windowPid
      })
    } else if (peek?.mode === 'screen' && screenEdgeHome) {
      const edge = screenEdgeHome
      const visible = clampPosToViewport($spatialPos.get())
      peekIntentGeneration += 1
      $spatialPeek.set(null)
      $spatialLocale.set('home')
      $spatialPos.set(visible)
      void activateScreenPeek(edge)
    } else if (peek?.mode === 'window') {
      reprepareWindowPeek(peek)
    } else {
      restoreScreenDock()
    }

    void tryStartPendingWindowPeek()
  }

  return {
    activateScreenPeek,
    applyScreenPeek,
    abandonPeekMode,
    cancelPeekPreparation,
    captureWindowPeekIntent,
    clearPeekState,
    commitPeekPreparation,
    enterWindowPeek,
    handleCatalogChanged,
    hasExpressionReturn: () => expressionPeekReturn !== null,
    hasPendingWindowPeek: () => pendingWindowPeek !== null,
    invalidateIntent: (): void => {
      peekIntentGeneration += 1
    },
    cancelPendingWindowPeek: (): void => {
      if (pendingWindowPeek) {
        peekIntentGeneration += 1
        pendingWindowPeek = null
      }
    },
    leavePeekForExpression,
    leaveWindowPeek,
    restorePeekAfterExpression,
    restoreScreenDock,
    get screenEdgeHome(): ScreenEdge | null {
      return screenEdgeHome
    },
    set screenEdgeHome(edge: ScreenEdge | null) {
      screenEdgeHome = edge
    },
    stopWindowPeekTracker: tracker.stop,
    updateWindowPeek: tracker.update
  }
}
