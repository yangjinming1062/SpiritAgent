import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import type { IpcEventChannel, IpcEventContract } from '@ipc/contracts'
import { BrowserWindow, type WebContents } from 'electron'

export function fileExists(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile()
  } catch {
    return false
  }
}

export function directoryExists(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isDirectory()
  } catch {
    return false
  }
}

// 守护 webContents.send：关闭/重载期间窗口可能已销毁。channel 只用 `IpcEventChannel`（主→渲单向），不联合 `IpcSendChannel`（渲染→主）。
export function sendToWindow<C extends IpcEventChannel>(
  mainWindow: BrowserWindow | null | undefined,
  channel: C,
  ...payload: IpcEventContract[C]
): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  const { webContents } = mainWindow

  if (!webContents || webContents.isDestroyed()) {
    return
  }

  webContents.send(channel, ...payload)
}

export function broadcastToAllWindows<C extends IpcEventChannel>(channel: C, ...payload: IpcEventContract[C]): void {
  for (const win of BrowserWindow.getAllWindows()) {
    sendToWindow(win, channel, ...payload)
  }
}

// event.sender 直接是 WebContents；同样要避免销毁后 send 抛错。
export function sendToSender<C extends IpcEventChannel>(
  sender: WebContents | null | undefined,
  channel: C,
  ...payload: IpcEventContract[C]
): void {
  if (!sender || sender.isDestroyed()) {
    return
  }

  sender.send(channel, ...payload)
}

// 先写 .tmp 再重命名，崩溃时旧文件保持完整；失败时清理残留 tmp。
export async function atomicWriteFile(targetPath: string, content: Buffer | string | Uint8Array): Promise<void> {
  await fs.promises.mkdir(path.dirname(targetPath), { recursive: true })
  const tmpPath = `${targetPath}.${process.pid}.${crypto.randomUUID()}.tmp`

  try {
    await fs.promises.writeFile(tmpPath, content)
    await fs.promises.rename(tmpPath, targetPath)
  } catch (error) {
    await fs.promises.unlink(tmpPath).catch(() => {})
    throw error
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** 后端 HTTP 失败的结构化错误：携带 status，禁止用文案前缀推断状态码。 */
export class HttpError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'HttpError'
    this.status = status
  }
}

/** Runner 未连接、请求未发出。 */
export class RunnerNotConnectedError extends Error {}

/** Runner 以 JSON-RPC error 回复的请求；`disposition` 取自 `error.data.disposition`（PROTOCOL「调用日志与未知结果」）。 */
export class RunnerRpcError extends Error {
  readonly code: null | number
  readonly disposition: null | string

  constructor(message: string, code: null | number, disposition: null | string) {
    super(message)
    this.code = code
    this.disposition = disposition
  }
}

function isHttpStatus(error: unknown, status: number): boolean {
  return error instanceof HttpError && error.status === status
}

export function isUnauthorized(error: unknown): boolean {
  return isHttpStatus(error, 401)
}

// ENOENT 或格式损坏时返回 null，由调用方走默认分支。
export function safeReadJson<T = unknown>(filePath: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T
  } catch {
    return null
  }
}

// 精灵是无边框置顶浮层，hide 后必须从 Windows 任务栏摘掉，否则会多出一个按钮。
export function hideAndSkipTaskbar(win: BrowserWindow | null | undefined): void {
  if (!win || win.isDestroyed()) {
    return
  }

  win.hide()

  if (process.platform === 'win32') {
    win.setSkipTaskbar(true)
  }
}
