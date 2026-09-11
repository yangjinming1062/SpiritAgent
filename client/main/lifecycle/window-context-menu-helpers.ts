import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { App, BrowserWindow, Net } from 'electron'
import { clipboard, dialog, nativeImage } from 'electron'

import { extensionForMimeType, mimeTypeForPath, parseDataUrl } from '../shared/mime'

// 解析 `data:` / `file:` / http(s) URL 为字节流 + mime；右键菜单里
// 复制/保存图片与写 composer 缩略图都依赖这条公共路径。
async function resourceBufferFromUrl(rawUrl: string, electronNet: Net): Promise<{ buffer: Buffer; mimeType: string }> {
  if (!rawUrl) {
    throw new Error('Missing URL')
  }

  if (rawUrl.startsWith('data:')) {
    const { data: buffer, mime: mimeType } = parseDataUrl(rawUrl)

    return { buffer, mimeType }
  }

  if (rawUrl.startsWith('file:')) {
    const filePath = fileURLToPath(rawUrl)
    const buffer = await fs.promises.readFile(filePath)

    return { buffer, mimeType: mimeTypeForPath(filePath) }
  }

  const res = await electronNet.fetch(rawUrl)

  if (!res.ok) {
    throw new Error(`Failed to fetch ${rawUrl}: ${res.status}`)
  }

  const arrayBuf = await res.arrayBuffer()

  return {
    buffer: Buffer.from(arrayBuf),
    mimeType: res.headers.get('content-type') || 'application/octet-stream'
  }
}

function filenameFromUrl(rawUrl: string, fallback = 'image'): string {
  try {
    const parsed = new URL(rawUrl)
    const base = path.basename(decodeURIComponent(parsed.pathname || ''))

    return base && base.includes('.') ? base : fallback
  } catch {
    return fallback
  }
}

interface ContextMenuHelpersOptions {
  app: Pick<App, 'getPath'>
  electronNet: Net
}

export function createContextMenuHelpers({ app, electronNet }: ContextMenuHelpersOptions) {
  async function copyImageFromUrl(rawUrl: string): Promise<void> {
    const { buffer } = await resourceBufferFromUrl(rawUrl, electronNet)
    const image = nativeImage.createFromBuffer(buffer)

    if (image.isEmpty()) {
      throw new Error('Could not read image')
    }

    clipboard.writeImage(image)
  }

  async function saveImageFromUrl(rawUrl: string, parentWindow: BrowserWindow): Promise<boolean> {
    const { buffer, mimeType } = await resourceBufferFromUrl(rawUrl, electronNet)
    const fallbackName = filenameFromUrl(rawUrl, `image${extensionForMimeType(mimeType) || '.png'}`)

    const result = await dialog.showSaveDialog(parentWindow, {
      defaultPath: fallbackName,
      title: 'Save Image'
    })

    if (result.canceled || !result.filePath) {
      return false
    }

    await fs.promises.writeFile(result.filePath, buffer)

    return true
  }

  async function writeComposerImage(buffer: Buffer, ext = '.png'): Promise<string> {
    const rawExt = String(ext || '.png')
      .trim()
      .toLowerCase()

    const normalizedExt = rawExt.startsWith('.') ? rawExt : `.${rawExt}`
    const safeExt = /^\.[a-z0-9]{1,5}$/.test(normalizedExt) ? normalizedExt : '.png'
    const dir = path.join(app.getPath('userData'), 'composer-images')
    await fs.promises.mkdir(dir, { recursive: true })
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', '')
    const random = crypto.randomBytes(3).toString('hex')
    const filePath = path.join(dir, `composer_${stamp}_${random}${safeExt}`)
    await fs.promises.writeFile(filePath, buffer)

    return filePath
  }

  return { copyImageFromUrl, saveImageFromUrl, writeComposerImage }
}

export type ContextMenuHelpers = ReturnType<typeof createContextMenuHelpers>
