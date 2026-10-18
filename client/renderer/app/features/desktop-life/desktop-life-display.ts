import { desktopDisplayResolution } from '@/modules/desktop-videos'
import type { DesktopResolution } from '@client-shared/desktop-media-geometry'
import type { DesktopLifeUiSnapshot, PresentationDisplay, PresentationState } from '@ipc/contracts'

type Creation = DesktopLifeUiSnapshot['create']
type Displays = Pick<PresentationState, 'displayId' | 'displays'>

export function resolveDesktopLifeRecommendationDisplay(presentation: Displays): PresentationDisplay | undefined {
  return (
    presentation.displays.find(display => display.id === presentation.displayId) ??
    presentation.displays.find(display => display.isPrimary) ??
    presentation.displays[0]
  )
}

export function reconcileDesktopLifeDisplay(
  creation: Creation,
  presentation: Displays,
  displayId = creation.targetDisplayId ?? resolveDesktopLifeRecommendationDisplay(presentation)?.id ?? null
): Creation {
  const changedDisplay = creation.targetDisplayId !== displayId
  const liveResolution = desktopDisplayResolution(presentation.displays.find(display => display.id === displayId))
  // 原生尺寸刷新期间保留已提交请求的身份；提交时仍须复核实时尺寸。
  const targetResolution = liveResolution ?? (changedDisplay ? null : creation.targetResolution)

  if (
    !changedDisplay &&
    creation.targetResolution?.width === targetResolution?.width &&
    creation.targetResolution?.height === targetResolution?.height
  ) {
    return creation
  }

  return { ...creation, targetDisplayId: displayId, targetResolution, requestId: crypto.randomUUID() }
}

export function readyDesktopLifeResolution(creation: Creation, presentation: Displays): DesktopResolution | null {
  const resolution = desktopDisplayResolution(
    presentation.displays.find(display => display.id === creation.targetDisplayId)
  )

  return resolution &&
    resolution.width === creation.targetResolution?.width &&
    resolution.height === creation.targetResolution?.height
    ? resolution
    : null
}
