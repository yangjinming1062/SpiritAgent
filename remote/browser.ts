export function newRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

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
