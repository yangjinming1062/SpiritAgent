import path from 'node:path'

import { nativeImage } from 'electron'
import type { BrowserWindow, Dialog, IpcMain, WebContents } from 'electron'

import { IPC, type SpiritAgentSelectPathsOptions } from '@ipc/contracts'

import { DATA_URL_READ_MAX_BYTES, readUserSelectedFile } from '../security/hardening'
import { registerUserSelectedPaths } from '../security/user-selected-paths'
import { dataUrlFromBuffer, mimeTypeForPath } from '../shared/mime'
import { broadcastToAllWindows } from '../shared/utils'

interface FilesIpcDeps {
  electron: {
    dialog: Dialog
    getMainWindow: () => BrowserWindow | null | undefined
  }
  ipcMain: IpcMain
  authIdentity: () => string | null
  isFeedSender: (sender: WebContents) => boolean
  isFeedReceiver: (sender: WebContents) => boolean
}

// 聊天图片附件体量护栏：data URL 走 WS 单帧 + 视觉模型请求体，超过边长/字节任一上限时降采样并重编码 JPEG。
const IMAGE_ATTACH_MAX_EDGE = 2048
const IMAGE_ATTACH_TARGET_BYTES = 6 * 1024 * 1024
const IMAGE_ATTACH_JPEG_QUALITY = 0.85

// 同鉴权会话的投喂信箱，读取不授予文件访问权限。
const pendingFeedPaths: string[] = []
let pendingFeedIdentity: string | null = null

export function clearPendingFeed(): void {
  pendingFeedPaths.length = 0
  pendingFeedIdentity = null
}

export function registerFilesIpc({
  electron,
  ipcMain,
  authIdentity,
  isFeedSender,
  isFeedReceiver
}: FilesIpcDeps): void {
  const { dialog, getMainWindow } = electron

  ipcMain.handle(IPC.invoke.readFileDataUrl, async (_event, filePath: string) => {
    const { data, resolvedPath } = await readUserSelectedFile(filePath, {
      maxBytes: DATA_URL_READ_MAX_BYTES,
      purpose: 'File preview'
    })

    return dataUrlFromBuffer(data, mimeTypeForPath(resolvedPath))
  })

  ipcMain.handle(IPC.invoke.readImageForAttach, async (_event, filePath: string) => {
    const { data, resolvedPath } = await readUserSelectedFile(filePath, {
      maxBytes: DATA_URL_READ_MAX_BYTES,
      purpose: 'Image attach'
    })

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

    const mainWin = getMainWindow()

    const openOptions = {
      defaultPath: options?.defaultPath ? path.resolve(String(options.defaultPath)) : undefined,
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

  ipcMain.handle(IPC.invoke.chatSetPendingFeed, (event, paths: string[], authSessionId: string) => {
    if (!isFeedSender(event.sender) || !authSessionId || authIdentity() !== authSessionId) {
      throw new Error('文件投喂账户已变化。')
    }

    const cleaned = Array.isArray(paths) ? paths.map(p => String(p)).filter(Boolean) : []

    if (cleaned.length === 0) {
      return
    }

    // 路径须已由 preload getPathForFile 或 selectPaths 注册；此处不自授白名单。
    if (pendingFeedIdentity !== authSessionId) {
      clearPendingFeed()
    }

    pendingFeedIdentity = authSessionId
    pendingFeedPaths.push(...cleaned)
    // 已打开的主对话收到信号，新窗口在取得资格后补取。
    broadcastToAllWindows(IPC.event.chatPendingFeed, cleaned)
  })

  ipcMain.handle(IPC.invoke.chatTakePendingFeed, (event, authSessionId: string) => {
    if (!isFeedReceiver(event.sender) || !authSessionId || authIdentity() !== authSessionId) {
      throw new Error('文件投喂账户已变化。')
    }

    if (pendingFeedIdentity !== authSessionId) {
      clearPendingFeed()

      return []
    }

    return pendingFeedPaths.splice(0)
  })
}
