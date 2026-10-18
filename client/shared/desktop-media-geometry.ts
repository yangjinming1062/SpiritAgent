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

export interface DesktopMediaLayout {
  objectFit: 'contain' | 'cover'
  left: number
  top: number
  width: number
  height: number
}

export function desktopMediaLayout(
  width: number,
  height: number,
  viewportWidth: number,
  viewportHeight: number
): DesktopMediaLayout {
  const frame: DesktopMediaLayout = {
    objectFit: 'contain',
    left: 0,
    top: 0,
    width: Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 0,
    height: Number.isFinite(viewportHeight) && viewportHeight > 0 ? viewportHeight : 0
  }

  if (![width, height, viewportWidth, viewportHeight].every(value => Number.isFinite(value) && value > 0)) {
    return frame
  }

  const targetAspect = viewportWidth / viewportHeight
  const cropWidth = width - height * targetAspect
  const cropHeight = height - width / targetAspect

  // 显示只按比例限制裁切，浮点容差不随封面或视频分辨率改变判断。
  const maxCropRatio = 0.1 + 1e-9

  if (cropWidth <= width * maxCropRatio && cropHeight <= height * maxCropRatio) {
    return { ...frame, objectFit: 'cover' }
  }

  const scale = Math.min(viewportWidth / width, viewportHeight / height)
  const mediaWidth = width * scale
  const mediaHeight = height * scale

  return {
    objectFit: 'contain',
    left: (viewportWidth - mediaWidth) / 2,
    top: (viewportHeight - mediaHeight) / 2,
    width: mediaWidth,
    height: mediaHeight
  }
}
