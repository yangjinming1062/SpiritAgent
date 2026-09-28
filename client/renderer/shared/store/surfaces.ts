// 渲染层入口面 store：当前哪个 surface 开着。
//
// 主进程是状态权威（surfaces.ts 持有 BrowserWindow 引用与互斥锁），本 store 只是镜像。
// 启动期 `hydrateSurfaces` 主动拉一次 `getState`，之后订阅 `onChanged` 维持一致；
// 任何 `requestOpenSurface` 调用先更新本地意图再交给主进程；本地意图用于 UI 立即反馈。
import type {
  DesktopSurfaceChangedEvent,
  DesktopSurfaceOpenPayload,
  SurfaceCompanionPreference,
  SurfaceCompanionState,
  SurfaceId
} from '@ipc/contracts'
import { atom } from 'nanostores'

export const $surfaceOpen = atom<null | SurfaceId>(null)
export const $surfaceScreenLocked = atom(false)
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

export type SurfaceRole = 'living' | 'workbench' | 'sprite'
export const $surfaceRole = atom<SurfaceRole | null>(null)

export function setSurfaceRole(role: SurfaceRole): void {
  $surfaceRole.set(role)
}

export function isCompanionStageVisible(): boolean {
  if ($surfaceScreenLocked.get() || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) {
    return false
  }

  const role = $surfaceRole.get()

  if (role === 'sprite') {
    return $surfaceOpen.get() === null
  }

  return role !== null && $surfaceOpen.get() === role && $surfaceCompanions.get()[role].visible
}

function applySurfaceState(state: DesktopSurfaceChangedEvent): void {
  if (state.revision < appliedRevision) {
    return
  }

  appliedRevision = state.revision
  $surfaceScreenLocked.set(state.screenLocked)
  $surfaceOpen.set(state.open)
  $surfaceCompanions.set(state.companions)
}

export async function setSurfaceCompanion(preference: SurfaceCompanionPreference): Promise<void> {
  applySurfaceState(await window.spiritagent.surface.setCompanion(preference))
}

export function isLivingProxyWindow(): boolean {
  if ($surfaceRole.get() === 'living') {
    return true
  }

  if (typeof window !== 'undefined' && window.location.pathname.includes('living.html')) {
    return true
  }

  return false
}

export interface OpenSurfaceOptions {
  sessionId?: string
  view?: string
}

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
    .catch(() => {
      // 启动早期 main 端 IPC 尚未就绪：保留默认 store（null），等下一次 onChanged 跟上。
    })

  return () => {
    disposed = true
    stop()
  }
}
