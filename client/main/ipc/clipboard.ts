import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

import { IPC } from '@ipc/contracts'
import type { BrowserWindow, Clipboard, Dialog, IpcMain, NativeImage } from 'electron'

import { assertUserSelectedPath } from '../security/user-selected-paths'
import { extensionForMimeType, mimeTypeForPath, parseDataUrl } from '../shared/mime'

interface ClipboardIpcDeps {
  electron: {
    clipboard: Clipboard
    dialog: Dialog
    getMainWindow: () => BrowserWindow | null
    nativeImage: { createFromBuffer: (buffer: Buffer) => NativeImage }
  }
  ipcMain: IpcMain
}

// 渲染层 copyImage/saveImage 共用的解析路径。仅接受 data: 图片，或经对话框/拖拽
// 注册过的 file: 路径（与 readFileDataUrl 同一白名单，防 XSS/IPC 任意读盘外传）。
// 不接受任意 http(s)：参考图与立绘经 apiAsset 已是 data URL，右键菜单另有主进程路径。
async function imageBufferFromUrl(rawUrl: string): Promise<{ buffer: Buffer; mimeType: string }> {
  if (!rawUrl) {
    throw new Error('Missing URL')
  }

  if (rawUrl.startsWith('data:')) {
    const { data: buffer, mime: mimeType } = parseDataUrl(rawUrl)

    if (!mimeType.startsWith('image/')) {
      throw new Error(`Not an image data URL (${mimeType})`)
    }

    return { buffer, mimeType }
  }

  if (rawUrl.startsWith('file:')) {
    const filePath = fileURLToPath(rawUrl)

    assertUserSelectedPath(filePath, 'Image clipboard')

    const buffer = await fs.promises.readFile(filePath)

    return { buffer, mimeType: mimeTypeForPath(filePath) }
  }

  throw new Error('Unsupported image URL scheme')
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
  const { clipboard, dialog, getMainWindow, nativeImage } = electron

  ipcMain.handle(IPC.invoke.writeClipboard, (_event, text) => {
    clipboard.writeText(String(text || ''))

    return true
  })

  ipcMain.handle(IPC.invoke.copyImage, async (_event, payload?: { url?: string }) => {
    const { buffer, mimeType } = await imageBufferFromUrl(String(payload?.url || ''))
    // 非 PNG/JPEG 可能读空；渲染层应先经 image-clipboard 转 PNG
    const image = nativeImage.createFromBuffer(buffer)

    if (image.isEmpty()) {
      throw new Error(`Could not read image (${mimeType})`)
    }

    clipboard.writeImage(image)

    return true
  })

  ipcMain.handle(IPC.invoke.saveImage, async (_event, payload?: { defaultName?: string; url?: string }) => {
    const rawUrl = String(payload?.url || '')
    const { buffer, mimeType } = await imageBufferFromUrl(rawUrl)
    const parent = getMainWindow()

    const defaultPath = payload?.defaultName?.trim()
      ? ensureImageExtension(payload.defaultName.trim(), mimeType)
      : defaultFileName(rawUrl, mimeType)

    const options = { defaultPath, title: 'Save Image' }

    const result = parent ? await dialog.showSaveDialog(parent, options) : await dialog.showSaveDialog(options)

    if (result.canceled || !result.filePath) {
      return false
    }

    await fs.promises.writeFile(result.filePath, buffer)

    return true
  })
}
