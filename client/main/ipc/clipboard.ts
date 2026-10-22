import type { BrowserWindow, Clipboard, Dialog, IpcMain, NativeImage } from 'electron'

import { IPC } from '@ipc/contracts'

import { assertUserSelectedPath } from '../security/user-selected-paths'
import { createImageOperations } from '../shared/lib/image-operations'
import { dataOrFileBufferFromUrl, extensionForMimeType } from '../shared/mime'

interface ClipboardIpcDeps {
  electron: {
    clipboard: Clipboard
    dialog: Dialog
    getMainWindow: () => BrowserWindow | null
    nativeImage: { createFromBuffer: (buffer: Buffer) => NativeImage }
  }
  ipcMain: IpcMain
}

// copyImage/saveImage 共用解析路径：仅接受 data: 图片或经对话框/拖拽注册过的 file: 路径（与 readFileDataUrl 同一白名单，防 XSS/IPC 任意读盘外传）；不接受任意 http(s)。
async function imageBufferFromUrl(rawUrl: string): Promise<{ buffer: Buffer; mimeType: string }> {
  const parsed = await dataOrFileBufferFromUrl(rawUrl, {
    assertPath: filePath => assertUserSelectedPath(filePath, 'Image clipboard')
  })

  if (!parsed) {
    throw new Error('Unsupported image URL scheme')
  }

  if (parsed.kind === 'data' && !parsed.mimeType.startsWith('image/')) {
    throw new Error(`Not an image data URL (${parsed.mimeType})`)
  }

  return { buffer: parsed.buffer, mimeType: parsed.mimeType }
}

function defaultFileName(rawUrl: string, mimeType: string): string {
  if (rawUrl.startsWith('data:')) {
    const ext = extensionForMimeType(mimeType) || '.png'

    return `reference${ext}`
  }

  try {
    const parsed = new URL(rawUrl)

    const base =
      decodeURIComponent(parsed.pathname || '')
        .split(/[\\/]/)
        .pop() || ''

    return base && base.includes('.') ? base : `image${extensionForMimeType(mimeType) || '.png'}`
  } catch {
    return `image${extensionForMimeType(mimeType) || '.png'}`
  }
}

function ensureImageExtension(name: string, mimeType: string): string {
  const safeName = name.replace(/[\\/]/g, '_').trim() || 'image'

  if (safeName.includes('.')) {
    return safeName
  }

  return `${safeName}${extensionForMimeType(mimeType) || '.png'}`
}

export function registerClipboardIpc({ electron, ipcMain }: ClipboardIpcDeps): void {
  const { clipboard, getMainWindow } = electron
  const { copyImage, saveImage } = createImageOperations(electron)

  ipcMain.handle(IPC.invoke.writeClipboard, (_event, text) => {
    clipboard.writeText(String(text || ''))

    return true
  })

  ipcMain.handle(IPC.invoke.copyImage, async (_event, payload?: { url?: string }) => {
    const { buffer, mimeType } = await imageBufferFromUrl(String(payload?.url || ''))

    copyImage(buffer, mimeType)

    return true
  })

  ipcMain.handle(IPC.invoke.saveImage, async (_event, payload?: { defaultName?: string; url?: string }) => {
    const rawUrl = String(payload?.url || '')
    const { buffer, mimeType } = await imageBufferFromUrl(rawUrl)
    const parent = getMainWindow()

    const defaultPath = payload?.defaultName?.trim()
      ? ensureImageExtension(payload.defaultName.trim(), mimeType)
      : defaultFileName(rawUrl, mimeType)

    return saveImage(buffer, defaultPath, parent)
  })
}
