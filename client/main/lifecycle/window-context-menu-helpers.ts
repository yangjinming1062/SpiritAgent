import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { BrowserWindow, Net } from 'electron'
import { clipboard, dialog, nativeImage } from 'electron'

import { DEFAULT_FETCH_TIMEOUT_MS } from '../security/hardening'
import { createImageOperations } from '../shared/lib/image-operations'
import { extensionForMimeType, mimeTypeForPath, parseDataUrl } from '../shared/mime'

// 解析 `data:` / `file:` / http(s) URL 为字节流 + mime；右键菜单里复制/保存图片依赖这条公共路径。
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

  const res = await electronNet.fetch(rawUrl, { signal: AbortSignal.timeout(DEFAULT_FETCH_TIMEOUT_MS) })

  if (!res.ok) {
    // 只报路径：图片地址可能携带签名参数。
    throw new Error(`Failed to fetch ${new URL(rawUrl).pathname}: ${res.status}`)
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
  electronNet: Net
}

export function createContextMenuHelpers({ electronNet }: ContextMenuHelpersOptions) {
  const { copyImage, saveImage } = createImageOperations({ clipboard, dialog, nativeImage })

  async function copyImageFromUrl(rawUrl: string): Promise<void> {
    const { buffer, mimeType } = await resourceBufferFromUrl(rawUrl, electronNet)

    copyImage(buffer, mimeType || 'unknown')
  }

  async function saveImageFromUrl(rawUrl: string, parentWindow: BrowserWindow): Promise<boolean> {
    const { buffer, mimeType } = await resourceBufferFromUrl(rawUrl, electronNet)
    const fallbackName = filenameFromUrl(rawUrl, `image${extensionForMimeType(mimeType) || '.png'}`)

    return saveImage(buffer, fallbackName, parentWindow)
  }

  return { copyImageFromUrl, saveImageFromUrl }
}

export type ContextMenuHelpers = ReturnType<typeof createContextMenuHelpers>
