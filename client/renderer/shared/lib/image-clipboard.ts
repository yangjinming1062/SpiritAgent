/** 主进程 nativeImage 通常只稳解 PNG/JPEG；WebP/GIF 等 data URL 先在渲染进程转 PNG。 */
export async function imageUrlForNativeClipboard(url: string): Promise<string> {
  if (!url.startsWith('data:image/') || /^data:image\/(png|jpe?g)/i.test(url)) {
    return url
  }

  const img = new Image()

  await new Promise<void>((resolve, reject) => {
    img.onload = (): void => resolve()
    img.onerror = (): void => reject(new Error('image decode failed'))
    img.src = url
  })

  const canvas = document.createElement('canvas')

  canvas.width = img.naturalWidth
  canvas.height = img.naturalHeight

  const ctx = canvas.getContext('2d')

  if (!ctx) {
    throw new Error('canvas unavailable')
  }

  ctx.drawImage(img, 0, 0)

  return canvas.toDataURL('image/png')
}
