// 渲染层入口面 store：当前哪个 surface 开着 + 用户最近一次打开的是哪个。
//
// 主进程是状态权威（surfaces.ts 持有 BrowserWindow 引用与互斥锁），本 store 只是镜像。
// 启动期 `hydrateSurfaces` 主动拉一次 `getState`，之后订阅 `onChanged` 维持一致；
// 任何 `requestOpenSurface` 调用先更新本地意图再交给主进程；本地意图用于 UI 立即反馈。
import type { DesktopSurfaceBounds, DesktopSurfaceOpenPayload, SurfaceId } from '@ipc/contracts'
import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

export const $surfaceOpen = atom<null | SurfaceId>(null)
export const $lastSurface = atom<SurfaceId>('living')
// 工作台窗口外侧栖息坐标，由主进程合帧广播
export const $surfaceBounds = atom<DesktopSurfaceBounds | null>(null)

export type SurfaceRole = 'living' | 'workbench' | 'sprite'
export const $surfaceRole = atom<SurfaceRole | null>(null)

export function setSurfaceRole(role: SurfaceRole): void {
  $surfaceRole.set(role)
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

registerStorageClearHandler(() => {
  $surfaceOpen.set(null)
  $lastSurface.set('living')
  $surfaceBounds.set(null)
  $surfaceRole.set(null)
})

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
  $lastSurface.set(surface)

  await window.spiritagent?.surface?.open?.(payload)
}

export async function requestCloseSurface(): Promise<void> {
  await window.spiritagent?.surface?.close?.()
}

export async function requestToggleSurface(surface: SurfaceId, options: OpenSurfaceOptions = {}): Promise<void> {
  const payload: DesktopSurfaceOpenPayload = {
    sessionId: options.sessionId,
    surface,
    view: options.view
  }

  await window.spiritagent?.surface?.toggle?.(payload)
}

export function hydrateSurfaces(): () => void {
  if (!window.spiritagent?.surface) {
    return () => {}
  }

  void window.spiritagent.surface
    .getState()
    .then(state => {
      if (state) {
        $surfaceOpen.set(state.open)
        $lastSurface.set(state.lastSurface)
        $surfaceBounds.set(state.bounds ?? null)
      }
    })
    .catch(() => {
      // 启动早期 main 端 IPC 尚未就绪：保留默认 store（null / 'living'），等下一次 onChanged 跟上。
    })

  return window.spiritagent.surface.onChanged(payload => {
    $surfaceOpen.set(payload?.open ?? null)
    $lastSurface.set(payload?.lastSurface ?? 'living')
    $surfaceBounds.set(payload?.bounds ?? null)
  })
}
