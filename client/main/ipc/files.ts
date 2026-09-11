import fs from 'node:fs'
import path from 'node:path'

import { IPC, type SpiritAgentSelectPathsOptions } from '@ipc/contracts'
import { nativeImage } from 'electron'
import type { BrowserWindow, Dialog, IpcMain } from 'electron'

import { assertUserSelectedPath, registerUserSelectedPaths } from '../security/user-selected-paths'
import { dataUrlFromBuffer } from '../shared/mime'

interface FilesIpcDeps {
  electron: {
    dialog: Dialog
    getMainWindow: () => BrowserWindow | null | undefined
  }
  hardening: {
    DATA_URL_READ_MAX_BYTES: number
    resolveReadableFileForIpc: (
      filePath: string,
      options?: { maxBytes?: number; purpose?: string }
    ) => Promise<{ resolvedPath: string; stat: fs.Stats }>
  }
  ipcMain: IpcMain
  mimeTypeForPath: (filePath: string) => string
}

// 聊天图片附件的体量护栏：data URL 附件要走 WS 单帧 + 视觉模型请求体，
// 超过边长/字节任一上限时降采样并重编码 JPEG，保证发送不被体量截断。
const IMAGE_ATTACH_MAX_EDGE = 2048
const IMAGE_ATTACH_TARGET_BYTES = 6 * 1024 * 1024
const IMAGE_ATTACH_JPEG_QUALITY = 0.85

export function registerFilesIpc({ electron, hardening, ipcMain, mimeTypeForPath }: FilesIpcDeps): void {
  const { dialog, getMainWindow } = electron

  ipcMain.handle(IPC.invoke.readFileDataUrl, async (_event, filePath: string) => {
    assertUserSelectedPath(filePath, 'File preview')

    const { resolvedPath } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.DATA_URL_READ_MAX_BYTES,
      purpose: 'File preview'
    })

    const data = await fs.promises.readFile(resolvedPath)

    return dataUrlFromBuffer(data, mimeTypeForPath(resolvedPath))
  })

  ipcMain.handle(IPC.invoke.readImageForAttach, async (_event, filePath: string) => {
    assertUserSelectedPath(filePath, 'Image attach')

    const { resolvedPath } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.DATA_URL_READ_MAX_BYTES,
      purpose: 'Image attach'
    })

    const data = await fs.promises.readFile(resolvedPath)
    let buffer: Buffer = data
    let mime = mimeTypeForPath(resolvedPath)

    // 解码失败（HEIC 等原生不支持的格式）时尺寸为 0，原样透传字节，交由供应商侧判定。
    const decoded = nativeImage.createFromBuffer(data)
    const { width, height } = decoded.getSize()

    if (width > 0 && height > 0) {
      const scale = Math.min(1, IMAGE_ATTACH_MAX_EDGE / Math.max(width, height))
      const oversized = scale < 1 || data.length > IMAGE_ATTACH_TARGET_BYTES

      if (oversized) {
        const resized = scale < 1 ? decoded.resize({ width: Math.round(width * scale) }) : decoded
        const jpeg = resized.toJPEG(IMAGE_ATTACH_JPEG_QUALITY)

        // 仅在重编码确实变小时替换，避免把紧凑原图越压越大。
        if (jpeg.length < data.length) {
          buffer = jpeg
          mime = 'image/jpeg'
        }
      }
    }

    return dataUrlFromBuffer(buffer, mime)
  })

  ipcMain.handle(IPC.invoke.selectPaths, async (_event, options: SpiritAgentSelectPathsOptions = {}) => {
    const properties: Array<'multiSelections' | 'openDirectory' | 'openFile'> = options?.directories
      ? ['openDirectory']
      : ['openFile']

    if (options?.multiple !== false) {
      properties.push('multiSelections')
    }

    let resolvedDefaultPath: string | undefined

    if (options?.defaultPath) {
      try {
        resolvedDefaultPath = path.resolve(String(options.defaultPath))
      } catch {
        resolvedDefaultPath = undefined
      }
    }

    const mainWin = getMainWindow()

    const openOptions = {
      defaultPath: resolvedDefaultPath,
      filters: Array.isArray(options?.filters) ? options.filters : undefined,
      properties,
      title: options?.title || 'Add context'
    }

    const result = mainWin
      ? await dialog.showOpenDialog(mainWin, openOptions)
      : await dialog.showOpenDialog(openOptions)

    if (result.canceled) {
      return []
    }

    // 用户显式选择：写入白名单，后续 readFileDataUrl / readImageForAttach / 视频上传才可读。
    registerUserSelectedPaths(result.filePaths)

    return result.filePaths
  })

  ipcMain.handle(IPC.invoke.registerUserSelectedPaths, async (_event, paths: string[]) => {
    if (!Array.isArray(paths) || paths.length === 0) {
      return
    }

    registerUserSelectedPaths(paths.map(p => String(p)))
  })
}
