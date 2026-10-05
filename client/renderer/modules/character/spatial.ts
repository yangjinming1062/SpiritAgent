import {
  type DesktopScreenRect,
  type DesktopSpriteScalePayload,
  type DesktopWindowSceneSnapshot,
  SPRITE_SCALE_LIMITS
} from '@ipc/contracts'
import { clamp } from '@runtime'
import { atom, computed } from 'nanostores'

import { log } from '@/shared/lib/log'
import {
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceOpen, $surfaceRole, $surfaceSpriteVisible, isSpriteStageShown } from '@/shared/store/surfaces'

import { $actionCatalog, $activePlayInstance, ensurePeekAction } from './actions'
import type { PeekGeometry } from './actions'
import { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import { $effectiveTier, $spriteState, holdInteracting, setSpriteState } from './companion-store'
import { $llmAutonomy } from './prefs'
import {
  computeScreenPeekLayout,
  computeWindowPeekLayout,
  type SpatialPeek,
  type WindowPeekLayout
} from './spatial-peek'
import { clearSpriteGesture, playSpriteGesture } from './sprite/gesture'

export const EXPRESSION_BOOST_SCALE = 1.12

/** 未缩放舞台尺寸，只随视口高度变化；落位与视频画布共用。 */
export function baseSpriteSize(viewportHeight: number): { width: number; height: number } {
  const height = Math.round(clamp(viewportHeight / 3, 260, 960))

  return { width: Math.round(height * 0.85), height }
}

export function getBaseSpriteHeight(): number {
  return baseSpriteSize(window.innerHeight).height
}

export function getBaseSpriteWidth(): number {
  return baseSpriteSize(window.innerHeight).width
}

let stageInsets = { top: 0, bottom: 0, left: 0, right: 0 }
const DESKTOP_POSITION_KEY = registerCompanionStorageKey('da.desktop.sprite.position')

function saveRestPosition(position: {
  x: number
  y: number
  screenEdge?: { side: 'left' | 'right'; yRatio: number }
}): Promise<void> {
  if ($surfaceRole.get() === 'desktop-companion') {
    persistString(DESKTOP_POSITION_KEY, JSON.stringify(position))

    return Promise.resolve()
  }

  return window.spiritagent.sprite.setPosition(position)
}

function loadRestPosition(): Promise<{
  x: number
  y: number
  screenEdge?: { side: 'left' | 'right'; yRatio: number }
} | null> {
  if ($surfaceRole.get() !== 'desktop-companion') {
    return window.spiritagent.sprite.getPosition()
  }

  const raw = storedString(DESKTOP_POSITION_KEY)

  try {
    const value: unknown = raw ? JSON.parse(raw) : null

    if (
      value &&
      typeof value === 'object' &&
      'x' in value &&
      'y' in value &&
      typeof value.x === 'number' &&
      typeof value.y === 'number' &&
      Number.isFinite(value.x) &&
      Number.isFinite(value.y)
    ) {
      return Promise.resolve({ x: value.x, y: value.y })
    }
  } catch {
    /* 损坏位置恢复默认落点。 */
  }

  return Promise.resolve(null)
}

export function setSpatialInsets(insets: { top: number; bottom: number; left: number; right: number }): void {
  stageInsets = insets

  if ($surfaceRole.get() === 'desktop-companion') {
    setScaleTarget(computeTargetScale(), true)
    $homePosition.set(clampPosToViewport($homePosition.get()))

    if (moveStart) {
      moveStart = clampPosToViewport(moveStart)
    }

    if (moveTarget) {
      moveTarget = clampPosToViewport(moveTarget)
    }
  }

  $spatialPos.set(clampPosToViewport($spatialPos.get()))
}

const REST_MARGIN = 24

const WALK_SPEED = 80
const FLY_SPEED = 400
// 超过该距离飞行，近处步行。
const WALK_RANGE_PX = 400
const SCALE_TRANSITION_MS = 300
// Runner 离线或空闲时间未知（-1）时不漫游。
const ROAM_IDLE_THRESHOLD_SECONDS = 90
const SCALE_KEY = 'da.companion.defaultScale'

const MIN_SCALE = SPRITE_SCALE_LIMITS.min
const MAX_SCALE = SPRITE_SCALE_LIMITS.max

type SpatialLocale = 'home' | 'perch' | 'roam' | 'screen_peek' | 'window_peek' | 'target' | 'workbench'

type WindowPeekBinding = Pick<Extract<SpatialPeek, { mode: 'window' }>, 'runnerInstanceId' | 'windowId' | 'windowPid'>

export type PeekPreparation = {
  action: 'peek_left' | 'peek_right'
  generation: number
  packId: number
} & (
  | { mode: 'screen'; animate: boolean; screenEdge: { side: 'left' | 'right'; yRatio: number } }
  | ({ mode: 'window' } & WindowPeekBinding)
)

export type Locomotion = 'still' | 'walk' | 'fly' | 'drag'

const $spatialLocale = atom<SpatialLocale>('home')

export interface SpriteRect {
  left: number
  top: number
  right: number
  bottom: number
}

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

interface ViewportSize {
  width: number
  height: number
}

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

let rafId: number | null = null
let moveStart: { x: number; y: number } | null = null
let moveTarget: { x: number; y: number } | null = null
let moveStartTime = 0
let moveDuration = 0
let moveOnArrive: (() => void) | null = null

let scaleRafId: number | null = null
let scaleStartVal = 1
let scaleTargetVal = 1
let scaleStartTime = 0
let perchScaleLimit: number | null = null

let userInteracted = false
let roamTimer: ReturnType<typeof setTimeout> | null = null
let roaming = false
let peekIntentGeneration = 0
let screenEdgeHome: { side: 'left' | 'right'; yRatio: number } | null = null
let windowTracker: ReturnType<typeof setInterval> | null = null
let windowSnapshotPending = false
let expressionExitToken = 0
let expressionPeekReturn: { peek: SpatialPeek | PeekPreparation; packId: number } | null = null

interface PendingWindowPeek extends WindowPeekBinding {
  action: 'peek_left' | 'peek_right'
  generation: number
}

let pendingWindowPeek: PendingWindowPeek | null = null

// 探身流程的舞台前提：未锁屏且桌面精灵舞台展示中（未被完整入口收起、精灵窗未隐藏或最小化）。
function isStageActive(): boolean {
  return !$screenLocked.get() && isSpriteStageShown()
}

function getHomePosition(): { x: number; y: number } {
  const c = contentBox($defaultScale.get())

  return {
    x: Math.max(REST_MARGIN, window.innerWidth - stageInsets.right - c.right - REST_MARGIN),
    y: Math.max(-c.top, window.innerHeight - stageInsets.bottom - c.bottom)
  }
}

// 普通落位约束缩放后的内容像素，透明留白可越界；探身另按露出范围落位。
function contentBox(scale = $spatialScale.get()): { left: number; top: number; right: number; bottom: number } {
  const r = contentBounds()
  const { width, height } = baseSpriteSize(window.innerHeight)

  return {
    left: r.left * width * scale,
    top: r.top * height * scale,
    right: r.right * width * scale,
    bottom: r.bottom * height * scale
  }
}

function contentBounds(): SpriteRect {
  const content = $spriteContentRect.get() ?? { left: 0, top: 0, right: 1, bottom: 1 }

  if ($surfaceRole.get() !== 'desktop-companion') {
    return content
  }

  // 预留情绪放大与退出过渡的范围，避免空间比例先恢复时 CSS 放大尚未结束。
  const center = (content.left + content.right) / 2

  return {
    left: center + (content.left - center) * EXPRESSION_BOOST_SCALE,
    right: center + (content.right - center) * EXPRESSION_BOOST_SCALE,
    top: content.bottom + (content.top - content.bottom) * EXPRESSION_BOOST_SCALE,
    bottom: content.bottom
  }
}

function clampPosToViewport(pos: { x: number; y: number }, scale = $spatialScale.get()): { x: number; y: number } {
  const c = contentBox(scale)
  const vw = window.innerWidth
  const vh = window.innerHeight
  const maxY = vh - stageInsets.bottom - c.bottom

  return {
    x: clamp(pos.x, stageInsets.left - c.left, Math.max(stageInsets.left - c.left, vw - stageInsets.right - c.right)),
    y: clamp(pos.y, stageInsets.top - c.top, Math.max(stageInsets.top - c.top, maxY))
  }
}

export interface PerchPlacement {
  pos: { x: number; y: number }
  scale: number
}

/** 普通栖息选可容纳比例较大的一侧，同等空间优先右侧；低于 MIN_SCALE 时放弃。 */
export function computePerchPlacement(geom: DesktopScreenRect, maxScale: number): PerchPlacement | null {
  const margin = 8
  const { width: spriteW, height: spriteH0 } = baseSpriteSize(window.innerHeight)
  const content = contentBounds()
  const contentLeft = content.left
  const contentTop = content.top
  const contentRight = content.right
  const contentBottom = content.bottom
  const contentW = Math.max(1, (contentRight - contentLeft) * spriteW)

  const rightAvail = Math.max(
    0,
    window.innerWidth - Math.max(REST_MARGIN, stageInsets.right) - (geom.x + geom.w) - margin
  )

  const leftAvail = Math.max(0, geom.x - margin - Math.max(REST_MARGIN, stageInsets.left))
  const contentH = Math.max(1, (contentBottom - contentTop) * spriteH0)

  const heightLimit =
    $surfaceRole.get() === 'desktop-companion'
      ? Math.max(0, window.innerHeight - stageInsets.top - Math.max(REST_MARGIN, stageInsets.bottom)) / contentH
      : maxScale

  const rightScale = Math.min(maxScale, rightAvail / contentW, heightLimit)
  const leftScale = Math.min(maxScale, leftAvail / contentW, heightLimit)

  let side: 'left' | 'right'
  let scale: number

  if (rightScale >= MIN_SCALE && rightScale >= leftScale) {
    side = 'right'
    scale = rightScale
  } else if (leftScale >= MIN_SCALE) {
    side = 'left'
    scale = leftScale
  } else {
    return null
  }

  const contentTopPx = contentTop * spriteH0 * scale
  const contentBottomPx = contentBottom * spriteH0 * scale

  const x =
    side === 'right'
      ? geom.x + geom.w + margin - contentLeft * spriteW * scale
      : geom.x - margin - contentRight * spriteW * scale

  const y = Math.max(
    stageInsets.top - contentTopPx,
    Math.min(
      geom.y + geom.h - margin - contentBottomPx,
      window.innerHeight - Math.max(REST_MARGIN, stageInsets.bottom) - contentBottomPx
    )
  )

  return { pos: { x, y }, scale }
}

// 弹层按实际露出范围定位；右侧放不下时翻到左侧，并限制在视口内。
export function computeOverlayAnchorBesideSprite(opts: {
  pos: { x: number; y: number }
  scale: number
  anchorRect: SpriteRect | null
  peek: SpatialPeek | null
  gap: number
  overlayW: number
  overlayH?: number
  vw: number
  vh: number
  verticalRatio?: number
}): { left: number; top: number; side: 'left' | 'right' } {
  const { pos, scale, anchorRect, peek, gap, overlayW, overlayH = 0, vw, vh, verticalRatio = 0 } = opts
  const base = baseSpriteSize(vh)
  const width = base.width * scale
  const height = base.height * scale
  let visibleLeft = pos.x + (anchorRect?.left ?? 0) * width
  let visibleRight = pos.x + (anchorRect?.right ?? 1) * width
  let visibleTop = pos.y + (anchorRect?.top ?? 0) * height
  let visibleBottom = pos.y + (anchorRect?.bottom ?? 1) * height

  if (peek) {
    const [left, top, right, bottom] = peek.focusRect
    visibleLeft = Math.max(visibleLeft, pos.x + left * width)
    visibleRight = Math.min(visibleRight, pos.x + right * width)
    visibleTop = Math.max(visibleTop, pos.y + top * height)
    visibleBottom = Math.min(visibleBottom, pos.y + bottom * height)

    if (visibleLeft >= visibleRight || visibleTop >= visibleBottom) {
      visibleLeft = pos.x + left * width
      visibleRight = pos.x + right * width
      visibleTop = pos.y + top * height
      visibleBottom = pos.y + bottom * height
    }
  }

  if (peek?.mode === 'screen') {
    const cut = pos.x + peek.cutX * width

    if (peek.side === 'left') {
      visibleRight = Math.min(visibleRight, cut)
    } else {
      visibleLeft = Math.max(visibleLeft, cut)
    }
  }

  const rightSpace = vw - visibleRight - gap
  const leftSpace = visibleLeft - gap
  const side = rightSpace >= overlayW || rightSpace >= leftSpace ? 'right' : 'left'

  const left = clamp(
    side === 'right' ? visibleRight + gap : visibleLeft - gap - overlayW,
    0,
    Math.max(0, vw - overlayW)
  )

  const top = clamp(visibleTop + (visibleBottom - visibleTop) * verticalRatio, 0, Math.max(0, vh - overlayH))

  return { left, top, side }
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

function tick(now: number): void {
  if (!moveStart || !moveTarget || $surfaceOpen.get() === 'living') {
    cancelMovement()

    return
  }

  const t = Math.min(1, (now - moveStartTime) / moveDuration)
  const eased = easeInOut(t)

  const position = {
    x: moveStart.x + (moveTarget.x - moveStart.x) * eased,
    y: moveStart.y + (moveTarget.y - moveStart.y) * eased
  }

  $spatialPos.set(
    (t === 1 || $surfaceRole.get() === 'desktop-companion') && !$spatialPeek.get()
      ? clampPosToViewport(position)
      : position
  )

  if (t < 1) {
    rafId = requestAnimationFrame(tick)
  } else {
    cancelMovement(true)
  }
}

export function moveDurationMs(dist: number, locomotion: 'walk' | 'fly'): number {
  const speed = locomotion === 'walk' ? WALK_SPEED : FLY_SPEED

  return Math.max((dist / speed) * 1000, 200)
}

export function locomotionForDistance(dist: number): 'walk' | 'fly' {
  return dist > WALK_RANGE_PX ? 'fly' : 'walk'
}

function moveTo(target: { x: number; y: number }, locomotion: 'walk' | 'fly', onArrive?: () => void): void {
  cancelMovement()

  if ($surfaceRole.get() === 'desktop-companion' && !$spatialPeek.get()) {
    target = clampPosToViewport(target)
  }

  const current = $spatialPos.get()
  const dist = Math.hypot(target.x - current.x, target.y - current.y)

  if (dist < 2) {
    onArrive?.()

    return
  }

  moveStart = { ...current }
  moveTarget = target
  moveStartTime = performance.now()
  moveDuration = moveDurationMs(dist, locomotion)
  moveOnArrive = onArrive ?? null
  $spatialLocomotion.set(locomotion)
  rafId = requestAnimationFrame(tick)
}

export function cancelMovement(notifyArrive = false): void {
  if (rafId !== null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }

  const cb = moveOnArrive
  moveStart = null
  moveTarget = null
  moveOnArrive = null

  if ($spatialLocomotion.get() !== 'drag') {
    $spatialLocomotion.set('still')
  }

  if (notifyArrive) {
    cb?.()
  }
}

function tickScale(now: number): void {
  const t = Math.min(1, (now - scaleStartTime) / SCALE_TRANSITION_MS)
  const eased = easeInOut(t)

  $spatialScale.set(scaleStartVal + (scaleTargetVal - scaleStartVal) * eased)

  if (t < 1) {
    scaleRafId = requestAnimationFrame(tickScale)
  } else {
    scaleRafId = null
  }
}

function setScaleTarget(scale: number, instant = false): void {
  const clamped = clamp(scale, MIN_SCALE, MAX_SCALE)

  if (instant || Math.abs(clamped - $spatialScale.get()) < 0.01) {
    if (scaleRafId !== null) {
      cancelAnimationFrame(scaleRafId)
      scaleRafId = null
    }

    $spatialScale.set(clamped)

    return
  }

  scaleStartVal = $spatialScale.get()
  scaleTargetVal = clamped
  scaleStartTime = performance.now()

  if (scaleRafId === null) {
    scaleRafId = requestAnimationFrame(tickScale)
  }
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
  const requested = cap !== null ? Math.min(base, cap) : base

  if ($surfaceRole.get() !== 'desktop-companion') {
    return requested
  }

  const content = contentBox(1)
  const width = Math.max(1, content.right - content.left)
  const height = Math.max(1, content.bottom - content.top)

  return Math.min(
    requested,
    Math.max(0, window.innerWidth - stageInsets.left - stageInsets.right) / width,
    Math.max(0, window.innerHeight - stageInsets.top - stageInsets.bottom) / height
  )
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

  window.spiritagent.sprite.setDefaultScale({ scale: clamped })
}

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
    { width: window.innerWidth, height: window.innerHeight },
    { width: getBaseSpriteWidth(), height: getBaseSpriteHeight() },
    $spatialScale.get()
  )

  cancelMovement()
  $spatialPeek.set(peek)
  $spatialLocale.set('screen_peek')
  $homePosition.set(position)

  const savePosition = (savedPosition: { x: number; y: number } = position): void => {
    void saveRestPosition({
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

async function activateScreenPeek(target: { side: 'left' | 'right'; yRatio: number }, animate = false): Promise<void> {
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
  return window.spiritagent.sprite.getWindowScene().catch(error => {
    log.warn('spatial', 'Could not read window scene', error)

    return null
  })
}

function findBoundWindow(
  scene: DesktopWindowSceneSnapshot | null,
  binding: WindowPeekBinding
): DesktopWindowSceneSnapshot['windows'][number] | undefined {
  return scene?.runnerInstanceId === binding.runnerInstanceId
    ? scene.windows.find(item => item.visible && item.id === binding.windowId && item.pid === binding.windowPid)
    : undefined
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

export function cancelPeekPreparation(action: 'peek_left' | 'peek_right', generation?: number): void {
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

/** 首帧就绪后同步提交探身位置、遮挡与播放器。 */
export async function commitPeekPreparation(
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
    await window.spiritagent.sprite.moveToDisplay({ x: target.x + target.w / 2, y: target.y + target.h / 2 })
    scene = await readWindowScene()
    target = findBoundWindow(scene, preparation)

    if (!isCurrentPeekPreparation(preparation) || !scene || !target || target.displayId !== scene.viewport.displayId) {
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

  stopWindowPeekTracker()
  perchScaleLimit = layout.scale
  setScaleTarget(layout.scale, true)
  $spatialPeek.set(layout.peek)
  setSpatialLocale('window_peek', { instant: true, position: layout.position, scaleLimit: layout.scale })
  $peekPreparation.set(null)
  windowTracker = setInterval(() => void updateWindowPeek(), 100)
  showFirstFrame()

  return true
}

function stopWindowPeekTracker(): void {
  if (windowTracker !== null) {
    clearInterval(windowTracker)
    windowTracker = null
  }
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
  stopWindowPeekTracker()
  $spatialPeek.set(null)

  return peek
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

function abandonPeekMode(): void {
  const peek = clearPeekState()
  const wasPeeking = peek !== null || $spatialLocale.get() === 'screen_peek' || $spatialLocale.get() === 'window_peek'

  if (!wasPeeking) {
    return
  }

  cancelMovement()

  const position = settleHome(peek?.mode === 'window')

  void saveRestPosition({
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

async function updateWindowPeek(): Promise<void> {
  if (windowSnapshotPending || $spatialPeek.get()?.mode !== 'window') {
    return
  }

  windowSnapshotPending = true
  const generation = peekIntentGeneration

  try {
    const peek = $spatialPeek.get()

    if (!peek || peek.mode !== 'window') {
      return
    }

    if (!isStageActive()) {
      leaveWindowPeek()

      return
    }

    const scene = await readWindowScene()

    if (generation !== peekIntentGeneration || $spatialPeek.get() !== peek) {
      return
    }

    const target = findBoundWindow(scene, peek)

    if (!scene || !target) {
      leaveWindowPeek()

      return
    }

    if (target.displayId !== scene.viewport.displayId) {
      await window.spiritagent.sprite.moveToDisplay({ x: target.x + target.w / 2, y: target.y + target.h / 2 })

      return
    }

    const layout = buildWindowPeekLayout(scene, target, peek.action)

    if (!layout) {
      leaveWindowPeek()

      return
    }

    perchScaleLimit = layout.scale
    setScaleTarget(layout.scale, true)
    $spatialPeek.set(layout.peek)
    $spatialPos.set(layout.position)
  } catch (error) {
    log.warn('spatial', 'Could not follow window peek', error)

    if (generation === peekIntentGeneration) {
      leaveWindowPeek()
    }
  } finally {
    windowSnapshotPending = false
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
    stopWindowPeekTracker()

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

export interface WindowPeekIntent extends WindowPeekBinding {
  packId: number
  generation: number
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
export async function captureWindowPeekIntent(): Promise<WindowPeekIntent | null> {
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

  return { windowId: focused.id, windowPid: focused.pid, runnerInstanceId: scene.runnerInstanceId, packId, generation }
}

export async function enterWindowPeek(captured?: WindowPeekIntent): Promise<boolean> {
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

  setSpatialLocale('perch', { position: perch?.pos ?? clampPosToViewport($spatialPos.get()), scaleLimit: perch?.scale })

  return true
}

export function leavePeekForExpression(): boolean {
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
  stopWindowPeekTracker()

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
    x: clamp(raw.x, -(canvas?.left ?? 0) * width, window.innerWidth - (canvas?.right ?? 1) * width),
    y: clamp(raw.y, -(canvas?.top ?? 0) * height, window.innerHeight - (canvas?.bottom ?? 1) * height)
  }

  moveTo(destination, 'fly', () => {
    if (token === expressionExitToken && expressionPeekReturn) {
      $spatialPeek.set(null)
      $spatialLocale.set('home')
    }
  })

  return true
}

export async function restorePeekAfterExpression(): Promise<void> {
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

export function setSpatialLocale(
  locale: SpatialLocale,
  opts?: {
    position?: { x: number; y: number }
    locomotion?: 'walk' | 'fly'
    instant?: boolean
    /** 普通与窗口栖息的缩放上限；缺省时不限。 */
    scaleLimit?: number
    onArrive?: () => void
  }
): void {
  if (locale === 'home' && screenEdgeHome) {
    if ($spatialLocale.get() === 'screen_peek' || $peekPreparation.get()?.mode === 'screen') {
      return
    }

    abandonPeekMode()
    stopRoam()
    $spatialLocale.set('home')
    clearPerchScaleLimit()
    const home = clampPosToViewport($homePosition.get(), computeTargetScale())

    const returnToEdge = (): void => {
      opts?.onArrive?.()

      if (screenEdgeHome) {
        void activateScreenPeek(screenEdgeHome, !opts?.instant)
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
      ? clearPeekState(locale === 'perch' && pendingWindowPeek !== null)
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

  const target =
    locale === 'home' ||
    ($surfaceRole.get() === 'desktop-companion' && locale !== 'screen_peek' && locale !== 'window_peek')
      ? clampPosToViewport(rawTarget)
      : rawTarget

  const locomotion = opts?.locomotion ?? (locale === 'target' ? 'fly' : 'walk')

  if (opts?.instant) {
    cancelMovement()
    $spatialPos.set(target)
    $spatialLocomotion.set('still')
    opts?.onArrive?.()
  } else {
    moveTo(target, locomotion, opts?.onArrive)
  }
}

export function updateSpatialDecision(): void {
  if ($spatialLocomotion.get() === 'drag' || !isSpriteStageShown()) {
    return
  }

  if (expressionPeekReturn || $activePlayInstance.get()) {
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

    if (pendingWindowPeek) {
      peekIntentGeneration += 1
      pendingWindowPeek = null
    }

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
  const vw = window.innerWidth
  const vh = window.innerHeight
  const w = getBaseSpriteWidth() * $spatialScale.get()
  const h = getBaseSpriteHeight() * $spatialScale.get()

  return {
    x: REST_MARGIN + Math.random() * Math.max(0, vw - w - 2 * REST_MARGIN),
    y: Math.max(REST_MARGIN, vh * 0.5 + Math.random() * Math.max(0, vh * 0.4 - h))
  }
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
  if ($surfaceRole.get() === 'desktop-companion') {
    $spatialPos.set(clampPosToViewport(pos))

    return
  }

  const c = contentBox()
  const w = getBaseSpriteWidth() * $spatialScale.get()
  $spatialPos.set({
    x: clamp(pos.x, -w * 0.8, window.innerWidth - w * 0.2),
    y: clamp(pos.y, -c.top, window.innerHeight - c.bottom)
  })
}

export function endDragAt(pos: { x: number; y: number }, cancelled = false): void {
  const safe = clampPosToViewport(pos)

  const c = contentBox()
  const fullWidth = Math.max(1, c.right - c.left)
  const leftClipped = Math.max(0, -(pos.x + c.left))
  const rightClipped = Math.max(0, pos.x + c.right - window.innerWidth)

  const side =
    $surfaceRole.get() === 'desktop-companion'
      ? null
      : !cancelled && rightClipped / fullWidth >= 0.25
        ? 'right'
        : !cancelled && leftClipped / fullWidth >= 0.25
          ? 'left'
          : null

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
    screenEdgeHome = { side, yRatio }
    void saveRestPosition({ ...safe, screenEdge: { side, yRatio } })
    void activateScreenPeek(screenEdgeHome, true)

    return
  }

  if (!cancelled) {
    screenEdgeHome = null
  } else if (screenEdgeHome) {
    void activateScreenPeek(screenEdgeHome)

    return
  }

  void saveRestPosition(safe)
}

export function resetToHomePosition(): void {
  userInteracted = false
  clearPeekState()
  screenEdgeHome = null
  stopRoam()

  const home = getHomePosition()
  $homePosition.set(home)
  $spatialLocale.set('home')
  $spatialLocomotion.set('still')

  $spatialPos.set(home)
  void saveRestPosition(home)
}

export function initSpatial(): () => void {
  let disposed = false
  const offs: Array<() => void> = []

  offs.push(window.spiritagent.sprite.onDefaultScaleChanged(syncDefaultScale))

  offs.push(
    registerStorageClearHandler(() => {
      abandonPeekMode()
      screenEdgeHome = null
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
      void saveRestPosition({
        ...next,
        ...(saved.screenEdge ? { screenEdge: saved.screenEdge } : {})
      })
    }

    if (saved.screenEdge) {
      screenEdgeHome = saved.screenEdge
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

  void loadRestPosition()
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

  // 桌面舞台收起、精灵窗隐藏或最小化时暂停走位、漫游、探身与仪式提示；重新展示后收回栖身、恢复贴边并重新裁决。
  const pauseDesktopStage = (): void => {
    peekIntentGeneration += 1
    clearSpriteGesture()

    if ($spatialPeek.get() || pendingWindowPeek || $peekPreparation.get()) {
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

    if (screenEdgeHome) {
      void activateScreenPeek(screenEdgeHome)
    }

    if ($spatialLocale.get() === 'perch' || $spatialLocale.get() === 'workbench') {
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
      if (open === 'living' || open === 'workbench') {
        pauseDesktopStage()

        if (open === 'workbench') {
          $spatialLocale.set('workbench')
        }
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
        if (screenEdgeHome) {
          void activateScreenPeek(screenEdgeHome)
        }

        updateSpatialDecision()
      }
    })
  )

  // 拖拽逐帧限制位置；移动完成时按最终比例重新落位。
  offs.push(
    $spatialScale.listen(() => {
      if ($spatialLocomotion.get() === 'drag' || rafId !== null) {
        return
      }

      const locale = $spatialLocale.get()

      if (locale === 'screen_peek' && screenEdgeHome) {
        const action = screenEdgeHome.side === 'right' ? 'peek_left' : 'peek_right'
        const geometry = $actionCatalog.get()?.clipsBySlot.get(action)?.peek_geometry

        if (geometry) {
          applyScreenPeek(screenEdgeHome, geometry, false)
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
      if ($surfaceRole.get() === 'desktop-companion') {
        setSpatialInsets(stageInsets)
      }

      if (!screenEdgeHome) {
        $homePosition.set(clampPosToViewport($homePosition.get()))
      }

      if (!$spatialPeek.get() && $spatialLocomotion.get() === 'still' && rafId === null) {
        $spatialPos.set(clampPosToViewport($spatialPos.get()))
      }
    })
  )

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

  offs.push(
    $spriteCanvasRect.listen(() => {
      const peek = $spatialPeek.get()

      if (peek?.mode === 'screen' && screenEdgeHome) {
        const geometry = $actionCatalog.get()?.clipsBySlot.get(peek.action)?.peek_geometry

        if (geometry) {
          applyScreenPeek(screenEdgeHome, geometry, false)
        }
      } else if (peek?.mode === 'window') {
        void updateWindowPeek()
      } else {
        restoreScreenDock()
      }
    })
  )

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

  let previousCatalog = $actionCatalog.get()

  offs.push(
    $actionCatalog.listen(catalog => {
      const previous = previousCatalog
      previousCatalog = catalog

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
    })
  )

  const onResize = () => {
    $viewport.set({ width: window.innerWidth, height: window.innerHeight })

    if ($surfaceRole.get() === 'desktop-companion') {
      setSpatialInsets(stageInsets)
    }

    // 跨屏拖拽由指针路径重映射位置，resize 不接管。
    if ($spatialLocomotion.get() === 'drag') {
      return
    }

    const home = $homePosition.get()
    const c = contentBox()

    const clamped =
      $surfaceRole.get() === 'desktop-companion'
        ? clampPosToViewport(home)
        : {
            x: clamp(home.x, REST_MARGIN, window.innerWidth - c.right - REST_MARGIN),
            y: clamp(home.y, -c.top, window.innerHeight - c.bottom)
          }

    $homePosition.set(clamped)

    // 跟随目标跨屏也会触发 resize；保留已冻结的窗口和首帧准备代次。
    if ($peekPreparation.get()?.mode === 'window' || pendingWindowPeek) {
      $spatialPos.set(clampPosToViewport($spatialPos.get()))

      return
    }

    const locale = $spatialLocale.get()

    if (locale === 'home') {
      setSpatialLocale('home', { instant: true })
    } else if (locale === 'screen_peek' && screenEdgeHome) {
      const action = screenEdgeHome.side === 'right' ? 'peek_left' : 'peek_right'
      const geometry = $actionCatalog.get()?.clipsBySlot.get(action)?.peek_geometry

      if (geometry) {
        applyScreenPeek(screenEdgeHome, geometry, true)
      } else {
        void activateScreenPeek(screenEdgeHome)
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

    if (scaleRafId !== null) {
      cancelAnimationFrame(scaleRafId)
      scaleRafId = null
    }
  }
}
