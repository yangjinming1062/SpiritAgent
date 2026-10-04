// 渲染层入口面 store：当前哪个 surface 开着。主进程是状态权威（持有 BrowserWindow 引用与互斥锁），本 store 只是镜像。启动期 hydrateSurfaces 主动拉一次 getState，之后订阅 onChanged 维持一致；requestOpenSurface 先更新本地意图再交给主进程，本地意图用于 UI 立即反馈。
import type {
  DesktopSurfaceChangedEvent,
  DesktopSurfaceOpenPayload,
  SurfaceCompanionPreference,
  SurfaceCompanionState,
  SurfaceId
} from '@ipc/contracts'
import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

import { $presentation } from './presentation'

export const $surfaceOpen = atom<null | SurfaceId>(null)
export const $surfaceOpenVisible = atom(false)
export const $surfaceScreenLocked = atom(false)
// 桌面精灵窗的实际显隐（托盘、快捷键、右键隐藏或最小化）；收到主进程快照前按可见处理。
export const $surfaceSpriteVisible = atom(true)
let appliedRevision = -1
export const $surfaceCompanions = atom<Record<SurfaceId, SurfaceCompanionState>>({
  living: {
    hiddenReason: 'window-hidden',
    preference: { enabled: true, side: 'right' },
    slotWidth: 0,
    outerWidth: 0,
    visible: false
  },
  workbench: {
    hiddenReason: 'window-hidden',
    preference: { enabled: true, side: 'left' },
    slotWidth: 0,
    outerWidth: 0,
    visible: false
  }
})

export type SurfaceRole = 'living' | 'workbench' | 'sprite' | 'desktop' | 'desktop-companion'
export const $surfaceRole = atom<SurfaceRole | null>(null)

export function setSurfaceRole(role: SurfaceRole): void {
  $surfaceRole.set(role)
}

// 桌面精灵舞台展示中：精灵窗未隐藏或最小化，且未被完整入口收起。
export function isSpriteStageShown(): boolean {
  const presentation = $presentation.get()

  if ($surfaceRole.get() === 'desktop-companion') {
    return (
      presentation.stageOwner === 'desktop' &&
      presentation.status === 'active' &&
      presentation.stageAvailable &&
      presentation.stageVisible
    )
  }

  return (
    presentation.stageOwner === 'sprite' &&
    !['starting', 'recovering'].includes(presentation.status) &&
    $surfaceOpen.get() === null &&
    $surfaceSpriteVisible.get()
  )
}

export function isCompanionStageVisible(): boolean {
  if ($surfaceScreenLocked.get() || document.visibilityState === 'hidden') {
    return false
  }

  const role = $surfaceRole.get()

  if (role === 'desktop') {
    return false
  }

  if (role === 'sprite' || role === 'desktop-companion') {
    return isSpriteStageShown()
  }

  return role !== null && $surfaceOpen.get() === role && $surfaceCompanions.get()[role].visible
}

function applySurfaceState(state: DesktopSurfaceChangedEvent): void {
  if (state.revision < appliedRevision) {
    return
  }

  appliedRevision = state.revision
  $surfaceScreenLocked.set(state.screenLocked)
  $surfaceSpriteVisible.set(state.spriteVisible)
  $surfaceOpen.set(state.open)
  $surfaceOpenVisible.set(state.openVisible)
  $surfaceCompanions.set(state.companions)
}

export async function setSurfaceCompanion(preference: SurfaceCompanionPreference): Promise<void> {
  applySurfaceState(await window.spiritagent.surface.setCompanion(preference))
}

export type OpenSurfaceOptions = Omit<DesktopSurfaceOpenPayload, 'surface'>

export async function requestOpenSurface(surface: SurfaceId, options: OpenSurfaceOptions = {}): Promise<void> {
  const payload: DesktopSurfaceOpenPayload = {
    sessionId: options.sessionId,
    surface,
    view: options.view
  }

  // 乐观回灌：把当前意图同步进 store，让按钮立即按下、UI 跟随。
  $surfaceOpen.set(surface)

  await window.spiritagent?.surface?.open?.(payload)
}

export async function requestCloseSurface(): Promise<void> {
  await window.spiritagent?.surface?.close?.()
}

export function hydrateSurfaces(): () => void {
  if (!window.spiritagent?.surface) {
    return () => {}
  }

  let disposed = false
  const stop = window.spiritagent.surface.onChanged(applySurfaceState)

  void window.spiritagent.surface
    .getState()
    .then(state => {
      if (!disposed && state) {
        applySurfaceState(state)
      }
    })
    .catch(error => log.warn('surfaces', 'getState failed', error))

  return () => {
    disposed = true
    stop()
  }
}
