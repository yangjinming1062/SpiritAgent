export function installClipboardShim(): void {
  const ipc = window.spiritagent?.writeClipboard

  if (!ipc || !navigator.clipboard) {
    return
  }

  const native = navigator.clipboard.writeText?.bind(navigator.clipboard)

  const writeText = async (text: string): Promise<void> => {
    try {
      await ipc(text)
    } catch {
      await native?.(text)
    }
  }

  try {
    Object.defineProperty(navigator.clipboard, 'writeText', { configurable: true, value: writeText, writable: true })
  } catch {
    /* 浏览器拒绝覆盖 */
  }
}
