import { log } from '@/shared/lib/log'
import { backendApi, fileApi } from '@/shared/platform/desktop'
import { getStrings } from '@/shared/strings'

export interface PickedImage {
  base64: string
  contentType: string
  previewUrl: string
}

// 与后端 AvatarFromImageRequest 上限对齐——更大的请求会被拒为 422，用户无从操作，所以这里先给提示直接拒收。
const MAX_IMAGE_BASE64 = 8 * 1024 * 1024

/** 解析 `data:<mime>;base64,<载荷>`；形式不符或载荷为空返回 null，缺省类型按 image/png。 */
export function parseImageDataUrl(dataUrl: string): PickedImage | null {
  const match = /^data:([^;,]+)?;base64,([\s\S]+)$/.exec(dataUrl)

  if (!match?.[2]) {
    return null
  }

  return { base64: match[2], contentType: match[1] || 'image/png', previewUrl: dataUrl }
}

/** `null` when the user cancels or the file is unreadable; `error` is user-facing copy. */
export async function pickAvatarImage(title: string): Promise<{ image: PickedImage } | { error: string } | null> {
  try {
    const [path] = await fileApi().selectPaths({
      title,
      filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }]
    })

    if (!path) {
      return null
    }

    const image = parseImageDataUrl(await fileApi().readFileDataUrl(path))

    if (!image) {
      return null
    }

    return image.base64.length > MAX_IMAGE_BASE64 ? { error: getStrings().common.imagePick.tooLarge } : { image }
  } catch (error) {
    log.warn('avatar-image', 'Could not read picked image', error)

    return { error: getStrings().common.imagePick.readFailed }
  }
}

/** `null` on failure — the raw URL is the thing the renderer can't reach, so returning it would render a broken image. */
export async function resolvePortraitUrl(
  assetUrl: string | null | undefined,
  options?: { cacheOnly?: boolean; preferCache?: boolean }
): Promise<string | null> {
  if (!assetUrl) {
    return null
  }

  try {
    return await backendApi().apiAsset({
      cacheOnly: options?.cacheOnly,
      preferCache: options?.preferCache,
      url: assetUrl
    })
  } catch (error) {
    log.warn('avatar-image', 'Could not resolve portrait URL', error)

    return null
  }
}
