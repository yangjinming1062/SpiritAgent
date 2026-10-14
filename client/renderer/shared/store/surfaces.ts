import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'
import { shellApi } from '@/shared/platform/desktop'
// 主窗口与伙伴状态的主进程镜像；版本快照裁决实际显隐。
import type {
  DesktopSurfaceChangedEvent,
  DesktopSurfaceOpenPayload,
  SurfaceCompanionPreference,
  SurfaceCompanionState
} from '@ipc/contracts'

import { $presentation } from './presentation'

export const $surfaceOpen = atom(false)
export const $surfaceOpenVisible = atom(false)
export const $surfaceScreenLocked = atom(false)
// 桌面精灵窗的实际显隐（托盘、快捷键、右键隐藏或最小化）；收到主进程快照前按可见处理。
export const $surfaceSpriteVisible = atom(true)
let appliedRevision = -1
export const $surfaceCompanion = atom<SurfaceCompanionState>({
  hiddenReason: 'window-hidden',
  preference: { enabled: true, side: 'right' },
  slotWidth: 0,
  outerWidth: 0,
  visible: false
})

export type SurfaceRole = 'main' | 'sprite' | 'desktop'
export const $surfaceRole = atom<SurfaceRole | null>(null)

export function setSurfaceRole(role: SurfaceRole): void {
  $surfaceRole.set(role)
}

// 桌面精灵舞台展示中：精灵窗未隐藏或最小化，且未被完整入口收起。
export function isSpriteStageShown(): boolean {
  const presentation = $presentation.get()

  return (
    presentation.stageOwner === 'sprite' &&
    !['starting', 'recovering'].includes(presentation.status) &&
    !$surfaceOpen.get() &&
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

  if (role === 'sprite') {
    return isSpriteStageShown()
  }

  return (
    role === 'main' &&
    $presentation.get().effectiveMode === 'window' &&
    $surfaceOpen.get() &&
    $surfaceCompanion.get().visible
  )
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
  $surfaceCompanion.set(state.companion)
}

export async function setSurfaceCompanion(preference: SurfaceCompanionPreference): Promise<void> {
  applySurfaceState(await shellApi().surface.setCompanion(preference))
}

export async function requestOpenSurface(target: DesktopSurfaceOpenPayload = {}): Promise<void> {
  await shellApi().surface.open(target)
}

export async function requestCloseSurface(): Promise<void> {
  await shellApi()?.surface?.close?.()
}

export function hydrateSurfaces(): () => void {
  if (!shellApi()?.surface) {
    return () => {}
  }

  let disposed = false
  const stop = shellApi().surface.onChanged(applySurfaceState)

  void shellApi()
    .surface.getState()
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
