export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  const focused = document.activeElement
  const input = document.createElement('textarea')
  input.value = text
  input.readOnly = true
  input.style.position = 'fixed'
  input.style.left = '-9999px'
  document.body.append(input)
  try {
    input.select()
    input.setSelectionRange(0, text.length)
    if (!document.execCommand('copy')) {
      throw new Error('复制失败，请长按说明文字手动复制')
    }
  } finally {
    input.remove()
    if (focused instanceof HTMLElement) {
      focused.focus({ preventScroll: true })
    }
  }
}

export async function readImage(
  file: File
): Promise<{ base64: string; type: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'; url: string }> {
  const type = (['image/png', 'image/jpeg', 'image/webp', 'image/gif'] as const).find(value => value === file.type)
  if (!type) {
    throw new Error('请选择 PNG、JPEG、WebP 或 GIF 图片')
  }

  if (file.size > 6 * 1024 * 1024) {
    throw new Error('图片超过 6 MB，请缩小后上传')
  }

  const url = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () =>
      typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('图片无法读取'))
    reader.onerror = () => reject(new Error('图片无法读取'))
    reader.readAsDataURL(file)
  })

  return { base64: url.slice(url.indexOf(',') + 1), type, url }
}
