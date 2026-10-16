import { type DesktopResolution, isDesktopResolution } from '@client-shared/desktop-media-geometry'
import type { PresentationDisplay } from '@ipc/contracts'

import type { DesktopVideoSet } from './types'

export function desktopDisplayResolution(display: PresentationDisplay | undefined): DesktopResolution | null {
  const resolution = { width: display?.pixelWidth, height: display?.pixelHeight }

  return isDesktopResolution(resolution) ? resolution : null
}

export function desktopVideoMatchesDisplay(
  set: Pick<DesktopVideoSet, 'target_width' | 'target_height'>,
  display: PresentationDisplay | undefined
): boolean | null {
  const resolution = desktopDisplayResolution(display)

  if (!resolution) {
    return null
  }

  const expected = set.target_height * resolution.width

  // 交叉相乘避免刚好落在 1% 边界时受浮点除法影响。
  return Math.abs(set.target_width * resolution.height - expected) * 100 <= expected
}
