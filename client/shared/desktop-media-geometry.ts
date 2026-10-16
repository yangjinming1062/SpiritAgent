export interface DesktopResolution {
  width: number
  height: number
}

export const MAX_DESKTOP_VIDEO_DIMENSION = 65536

export function isDesktopResolution(value: unknown): value is DesktopResolution {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }

  const resolution = value as Record<string, unknown>

  return [resolution.width, resolution.height].every(
    dimension =>
      typeof dimension === 'number' &&
      Number.isSafeInteger(dimension) &&
      dimension > 0 &&
      dimension <= MAX_DESKTOP_VIDEO_DIMENSION
  )
}

export function desktopMediaObjectFit(
  width: number,
  height: number,
  viewportWidth: number,
  viewportHeight: number,
  geometryMode: 'target' | 'keep'
): 'contain' | 'cover' {
  if (
    geometryMode === 'keep' ||
    ![width, height, viewportWidth, viewportHeight].every(value => Number.isFinite(value) && value > 0)
  ) {
    return 'contain'
  }

  const targetAspect = viewportWidth / viewportHeight
  const cropWidth = width - height * targetAspect
  const cropHeight = height - width / targetAspect

  // 与媒体处理共用 10% 裁切上限；2 像素用于比例取整及编码对齐。
  return cropWidth <= width * 0.1 + 2 && cropHeight <= height * 0.1 + 2 ? 'cover' : 'contain'
}
