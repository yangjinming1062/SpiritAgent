import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import type { IpcEventChannel, IpcEventContract, SurfacePlaybackClaim } from '@ipc/contracts'
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

  sendToSender(mainWindow.webContents, channel, ...payload)
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

// 先写 .tmp 再重命名，崩溃时旧文件保持完整；失败或 shouldCommit 返回 false 时清理残留 tmp，返回是否已落盘。
export async function atomicWriteFile(
  targetPath: string,
  content: Buffer | string | Uint8Array,
  shouldCommit: () => boolean = () => true
): Promise<boolean> {
  await fs.promises.mkdir(path.dirname(targetPath), { recursive: true })
  const tmpPath = `${targetPath}.${process.pid}.${crypto.randomUUID()}.tmp`
  let committed = false

  try {
    await fs.promises.writeFile(tmpPath, content)

    if (shouldCommit()) {
      await fs.promises.rename(tmpPath, targetPath)
      committed = true
    }
  } finally {
    if (!committed) {
      await fs.promises.unlink(tmpPath).catch(() => {})
    }
  }

  return committed
}

/** 账户 ID 是 `backend/session.ts` 生成的 SHA-256 十六进制摘要，缓存目录名只接受这一形态。 */
export function isAccountId(value: string): boolean {
  return /^[a-f0-9]{64}$/.test(value)
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** `Number.isFinite` 不做类型转换，非 number 一律为 false；这里补上类型收窄。 */
export function isFiniteNumber(value: unknown): value is number {
  return Number.isFinite(value)
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

/** 非 2xx 响应转为 HttpError：正文只用于诊断文案，读取失败时回落状态文本；pathname 不得带签名参数。 */
export async function httpErrorFromResponse(res: Response, pathname: string): Promise<HttpError> {
  const detail = await res.text().catch(() => '')

  return new HttpError(res.status, `${res.status} ${pathname}: ${detail || res.statusText}`)
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

/** 串行队列：任务按入队顺序逐个执行，前一任务失败不阻断后续；结果与错误只交给各自的调用方。 */
export function createSerialQueue(): <T>(task: () => Promise<T> | T) => Promise<T> {
  let tail: Promise<unknown> = Promise.resolve()

  return task => {
    const next = tail.then(task)
    tail = next.catch(() => {})

    return next
  }
}

/** SurfacePlaybackClaim 认领表：解析、过期剪枝、去重与写入单点定义；窗口/舞台等资格门槛由调用方先行判定。 */
export function createPlaybackClaims(): { claim: (raw: unknown) => boolean; reset: () => void } {
  const claims = new Map<string, number>()

  return {
    claim: raw => {
      const claim = raw as Partial<SurfacePlaybackClaim> | null
      const playId = claim?.playId

      const expiresAt =
        claim?.expiresAt === null ? Infinity : typeof claim?.expiresAt === 'string' ? Date.parse(claim.expiresAt) : NaN

      const now = Date.now()

      for (const [id, deadline] of claims) {
        if (deadline < now) {
          claims.delete(id)
        }
      }

      if (
        typeof playId !== 'string' ||
        !/^[a-f0-9]{32}$/i.test(playId) ||
        Number.isNaN(expiresAt) ||
        expiresAt < now ||
        claims.has(playId)
      ) {
        return false
      }

      claims.set(playId, expiresAt)

      return true
    },
    reset: () => {
      claims.clear()
    }
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

/** 渲染层按命中检测切换鼠标穿透；ignore 为真时默认转发 mousemove，以便渲染层继续收到 mouseleave 等事件。 */
export function setWindowIgnoreMouseEvents(
  win: BrowserWindow | null | undefined,
  payload?: { forward?: boolean; ignore: boolean }
): void {
  if (!win || win.isDestroyed()) {
    return
  }

  const ignore = Boolean(payload?.ignore)
  win.setIgnoreMouseEvents(ignore, { forward: ignore && payload?.forward !== false })
}

/** 窗口存活、可见且未最小化。 */
export function isWindowShown(win: BrowserWindow | null | undefined): boolean {
  return !!win && !win.isDestroyed() && win.isVisible() && !win.isMinimized()
}
