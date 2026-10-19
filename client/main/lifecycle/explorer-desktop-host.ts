import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

import type { SpiritAgentUiPalette } from '@ipc/contracts'

import type { RunningApplicationsState, RunningApplicationWindow } from '../shared/desktop-applications'
import type { DesktopJumpListTask } from '../shared/desktop-jump-list'
import { createSerialQueue } from '../shared/utils'

function isRunningWindow(raw: unknown): raw is RunningApplicationWindow {
  if (!raw || typeof raw !== 'object') {
    return false
  }

  const item = raw as Partial<RunningApplicationWindow>

  return (
    typeof item.id === 'string' &&
    item.id.length > 0 &&
    item.id.length <= 256 &&
    typeof item.appId === 'string' &&
    item.appId.length > 0 &&
    (item.target === null || typeof item.target === 'string') &&
    typeof item.name === 'string' &&
    typeof item.title === 'string' &&
    typeof item.minimized === 'boolean' &&
    typeof item.onDesktopDisplay === 'boolean' &&
    Number.isSafeInteger(item.lastActive) &&
    typeof item.lastActive === 'number' &&
    item.lastActive >= 0
  )
}

export interface ExplorerDesktopWindow {
  handle: Buffer
  role: 'background' | 'overlay'
  /** 使用 screen.dipToScreenRect 转换完整显示器边界；helper 校正最多两像素的舍入。 */
  bounds: { x: number; y: number; width: number; height: number }
}

export interface ExplorerDesktopSystemTray {
  bounds: ExplorerDesktopWindow['bounds']
  visible: boolean
}

export interface ExplorerDesktopTrayLayout {
  bounds: ExplorerDesktopWindow['bounds']
  clip: ExplorerDesktopWindow['bounds']
}

interface HostOptions {
  helperPath: string
  journalPath: string
  log: (message: string) => void
  onFailure: (reason: string) => void
  onForegroundChanged?: (state: { active: boolean; stageAvailable: boolean; fullscreen: boolean }) => void
  onWarning?: (reason: string) => void
  onApplicationsChanged?: (state: RunningApplicationsState) => void
  onWorkAreaChanged?: (bounds: ExplorerDesktopWindow['bounds']) => void
  onSystemTrayChanged?: (tray: ExplorerDesktopSystemTray | null) => void
}

export interface ExplorerDesktopHost {
  start: (options: {
    windows: ExplorerDesktopWindow[]
    parentPid: number
    takeover: boolean
    palette: SpiritAgentUiPalette
    workArea: ExplorerDesktopWindow['bounds']
  }) => Promise<void>
  setPalette: (palette: SpiritAgentUiPalette) => Promise<void>
  setTrayLayout: (layout: ExplorerDesktopTrayLayout | null, eligible?: () => boolean) => Promise<void>
  focus: (handle: Buffer, eligible?: () => boolean) => Promise<boolean>
  refreshApplications: (eligible: () => boolean) => Promise<void>
  activateExternal: (
    windowId: string,
    allowRemote: boolean,
    eligible: () => boolean,
    continuation?: () => boolean
  ) => Promise<void>
  cancelExternalInteraction: () => Promise<void>
  launchExternal: (target: string, appId: string, eligible: () => boolean) => Promise<void>
  closeExternal: (windowIds: string[], eligible: () => boolean) => Promise<void>
  getJumpListTasks: (target: string, eligible: () => boolean) => Promise<DesktopJumpListTask[]>
  activateJumpListTask: (target: string, taskId: string, eligible: () => boolean) => Promise<void>
  stop: () => Promise<void>
  recover: () => Promise<boolean>
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

interface ExternalActivation {
  windowId: string
  continuation: () => boolean
  generation: number
  host: ChildProcessWithoutNullStreams
  nativeOperationId: string | null
  authorizationRevision: number
  bufferedFocus: ExternalFocusRequest | null
  pendingFocus: ExternalFocusRequest | null
  latestFocus: ExternalFocusRequest | null
  authorizing: boolean
  cancelGrant: (() => void) | null
}

interface ExternalFocusRequest {
  operationId: string
  activationId: string
  revision: number
}

type ExternalFocusAuthorizationStatus = 'accepted' | 'deferred' | 'stale' | 'completed'
type ForegroundGrantResult = 'allowed' | 'deferred' | 'cancelled'

function parseExternalActivation(value: unknown): string {
  const activationId =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as { activation_id?: unknown }).activation_id
      : undefined

  if (typeof activationId !== 'string' || !activationId || activationId.length > 256) {
    throw new Error('Desktop helper returned an invalid external activation identity')
  }

  return activationId
}

function parseExternalFocusAuthorization(value: unknown): ExternalFocusAuthorizationStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned an invalid foreground authorization status')
  }

  const status = (value as { status?: unknown }).status

  if (status !== 'accepted' && status !== 'deferred' && status !== 'stale' && status !== 'completed') {
    throw new Error('Desktop helper returned an invalid foreground authorization status')
  }

  return status
}

function parseMessage(line: string): Record<string, unknown> {
  const value: unknown = JSON.parse(line)

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned an invalid message')
  }

  return value as Record<string, unknown>
}

function parseJumpListTasks(value: unknown): DesktopJumpListTask[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned invalid Jump List tasks')
  }

  const tasks = (value as { tasks?: unknown }).tasks

  if (!Array.isArray(tasks) || tasks.length > 64) {
    throw new Error('Desktop helper returned invalid Jump List task count')
  }

  return tasks.map(raw => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('Desktop helper returned invalid Jump List task')
    }

    const task = raw as Partial<DesktopJumpListTask>

    if (
      typeof task.id !== 'string' ||
      task.id.length < 1 ||
      task.id.length > 512 ||
      typeof task.title !== 'string' ||
      task.title.length < 1 ||
      task.title.length > 255 ||
      typeof task.description !== 'string' ||
      task.description.length > 2048 ||
      typeof task.iconPath !== 'string' ||
      task.iconPath.length > 32_768 ||
      typeof task.iconIndex !== 'number' ||
      !Number.isInteger(task.iconIndex) ||
      task.iconIndex < -2_147_483_648 ||
      task.iconIndex > 2_147_483_647
    ) {
      throw new Error('Desktop helper returned invalid Jump List task fields')
    }

    return {
      id: task.id,
      title: task.title,
      description: task.description,
      iconPath: task.iconPath,
      iconIndex: task.iconIndex
    }
  })
}

function encodeHandle(buffer: Buffer): string {
  if (buffer.length !== 4 && buffer.length !== 8) {
    throw new Error('Desktop native window handle has an unsupported size')
  }

  const handle = buffer.length === 8 ? buffer.readBigUInt64LE() : BigInt(buffer.readUInt32LE())

  if (handle === 0n) {
    throw new Error('Desktop native window handle is invalid')
  }

  return handle.toString(16)
}

function encodeWindow(window: ExplorerDesktopWindow): {
  handle: string
  role: ExplorerDesktopWindow['role']
  bounds: ExplorerDesktopWindow['bounds']
} {
  const handle = encodeHandle(window.handle)
  const bounds = window.bounds

  if (!isPhysicalRect(bounds)) {
    throw new Error('Desktop native window handle or physical bounds are invalid')
  }

  return { bounds: { ...bounds }, handle, role: window.role }
}

function isPhysicalRect(value: unknown): value is ExplorerDesktopWindow['bounds'] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }

  const bounds = value as Record<string, unknown>

  return (
    ['x', 'y', 'width', 'height'].every(key => typeof bounds[key] === 'number' && Number.isSafeInteger(bounds[key])) &&
    Number(bounds.width) > 0 &&
    Number(bounds.height) > 0 &&
    Number(bounds.width) <= 65_536 &&
    Number(bounds.height) <= 65_536 &&
    Math.abs(Number(bounds.x)) <= 1_000_000 &&
    Math.abs(Number(bounds.y)) <= 1_000_000
  )
}

export function createExplorerDesktopHost(options: HostOptions): ExplorerDesktopHost {
  if (!path.isAbsolute(options.helperPath) || !path.isAbsolute(options.journalPath)) {
    throw new Error('Desktop helper and journal paths must be absolute')
  }

  let state: 'idle' | 'starting' | 'running' | 'stopping' = 'idle'
  let child: ChildProcessWithoutNullStreams | null = null
  let recoveryProcess: ChildProcessWithoutNullStreams | null = null
  let nextId = 1
  let generation = 0
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  const serialize = createSerialQueue()
  let failureReported = false
  let externalActivation: ExternalActivation | null = null
  let foregroundGrantProcess: ChildProcessWithoutNullStreams | null = null

  let applicationBatch: {
    revision: number
    parts: number
    nextPart: number
    bytes: number
    state: RunningApplicationsState
  } | null = null

  const pending = new Map<number, PendingRequest>()

  function applicationsMessage(message: Record<string, unknown>): void {
    const { revision, parts, part, windows, status, error } = message

    if (
      typeof revision !== 'number' ||
      !Number.isSafeInteger(revision) ||
      revision < 1 ||
      typeof parts !== 'number' ||
      !Number.isSafeInteger(parts) ||
      parts < 1 ||
      parts > 128 ||
      typeof part !== 'number' ||
      !Number.isSafeInteger(part) ||
      part < 0 ||
      part >= parts ||
      (status !== 'ready' && status !== 'unavailable') ||
      (error !== null && typeof error !== 'string') ||
      !Array.isArray(windows) ||
      !windows.every(isRunningWindow)
    ) {
      throw new Error('Desktop helper returned an invalid application snapshot')
    }

    if (part === 0) {
      applicationBatch = { revision, parts, nextPart: 0, bytes: 0, state: { status, error, windows: [] } }
    }

    const batch = applicationBatch

    if (
      !batch ||
      batch.revision !== revision ||
      batch.parts !== parts ||
      batch.nextPart !== part ||
      batch.state.status !== status ||
      batch.state.error !== error
    ) {
      throw new Error('Desktop helper application snapshot is incomplete')
    }

    batch.bytes += Buffer.byteLength(JSON.stringify(message))

    if (batch.bytes > 2 * 1024 * 1024) {
      throw new Error('Desktop helper application snapshot exceeds 2 MiB')
    }

    batch.state.windows.push(...windows)
    batch.nextPart += 1

    if (batch.nextPart === parts) {
      applicationBatch = null
      options.onApplicationsChanged?.(batch.state)
    }
  }

  function clearHeartbeat(): void {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function rejectPending(error: Error): void {
    for (const request of pending.values()) {
      clearTimeout(request.timer)
      request.reject(error)
    }

    pending.clear()
  }

  function reportFailure(reason: string): void {
    clearExternalActivation()
    clearHeartbeat()
    rejectPending(new Error(reason))

    if (!failureReported && state === 'running') {
      failureReported = true
      options.log(`Explorer desktop host failed: ${reason}`)
      options.onFailure(reason)
    }
  }

  function request<T = void>(command: Record<string, unknown>, timeoutMs: number): Promise<T> {
    const process = child

    if (!process || exited(process) || process.killed) {
      return Promise.reject(new Error('Desktop helper is not running'))
    }

    const id = nextId++

    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id)
        const reason = `Desktop helper ${String(command.command)} timed out`

        reject(new Error(reason))
      }, timeoutMs)

      timer.unref()
      pending.set(id, { reject, resolve: value => resolve(value as T), timer })
      process.stdin.write(`${JSON.stringify({ ...command, id })}\n`, error => {
        if (!error) {
          return
        }

        const waiting = pending.get(id)

        if (waiting) {
          pending.delete(id)
          clearTimeout(waiting.timer)
          waiting.reject(error)
        }
      })
    })
  }

  function clearExternalActivation(): void {
    const previous = externalActivation
    externalActivation = null
    previous?.cancelGrant?.()
  }

  function currentExternalActivation(activation: ExternalActivation, focus?: ExternalFocusRequest): boolean {
    return (
      externalActivation === activation &&
      state === 'running' &&
      generation === activation.generation &&
      child === activation.host &&
      !exited(activation.host) &&
      !activation.host.killed &&
      activation.continuation() &&
      (focus === undefined || (activation.latestFocus === focus && activation.nativeOperationId === focus.operationId))
    )
  }

  async function waitForForegroundGrant(activation: ExternalActivation, focus: ExternalFocusRequest): Promise<boolean> {
    while (currentExternalActivation(activation, focus)) {
      const previous = foregroundGrantProcess

      if (!previous || exited(previous)) {
        return true
      }

      await new Promise<void>(resolve => {
        const finished = (): void => {
          previous.removeListener('exit', finished)
          previous.removeListener('close', finished)

          if (activation.cancelGrant === finished) {
            activation.cancelGrant = null
          }

          resolve()
        }

        activation.cancelGrant = finished
        previous.once('exit', finished)
        previous.once('close', finished)

        if (exited(previous) || !currentExternalActivation(activation, focus)) {
          finished()
        }
      })
    }

    return false
  }

  function runForegroundGrant(activation: ExternalActivation): Promise<ForegroundGrantResult> {
    if (foregroundGrantProcess && !exited(foregroundGrantProcess)) {
      return Promise.reject(new Error('上次前台授权辅助进程尚未退出。'))
    }

    return new Promise<ForegroundGrantResult>((resolve, reject) => {
      const grant = spawn(options.helperPath, ['allow-foreground', String(process.pid), String(activation.host.pid)], {
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true
      })

      foregroundGrantProcess = grant

      let stdout = ''
      let stderr = ''
      let settled = false

      const finishFailure = (reason: string): void => {
        if (settled) {
          return
        }

        settled = true
        activation.cancelGrant = null

        if (!grant.killed && !exited(grant)) {
          try {
            grant.kill()
          } catch (error) {
            options.log(`[desktop] foreground grant helper termination: ${String(error)}`)
          }
        }

        void waitForExit(grant, 2_000).then(stopped =>
          reject(new Error(stopped ? reason : `${reason}; foreground grant helper could not be stopped`))
        )
      }

      activation.cancelGrant = () => finishFailure('前台授权请求已失效。')
      grant.stdout.setEncoding('utf8')
      grant.stderr.setEncoding('utf8')
      grant.stdout.on('data', (chunk: string) => {
        if (!settled) {
          stdout += chunk

          if (Buffer.byteLength(stdout) > 8192) {
            finishFailure('前台授权辅助进程输出过长。')
          }
        }
      })
      grant.stderr.on('data', (chunk: string) => {
        if (!settled) {
          stderr += chunk

          if (Buffer.byteLength(stderr) > 4096) {
            finishFailure('前台授权辅助进程诊断输出过长。')
          }
        }
      })
      grant.stdin.on('error', error => finishFailure(`前台授权命令通道失败：${error.message}`))
      grant.once('error', error => finishFailure(`前台授权辅助进程无法启动：${error.message}`))
      grant.once('close', code => {
        if (foregroundGrantProcess === grant) {
          foregroundGrantProcess = null
        }

        if (settled) {
          return
        }

        try {
          const event = code === 0 ? parseMessage(stdout.trim()).event : undefined

          if (
            code !== 0 ||
            (event !== 'foreground_allowed' && event !== 'foreground_deferred' && event !== 'foreground_cancelled')
          ) {
            throw new Error(`前台授权辅助进程未确认成功 (${String(code)})${stderr ? `: ${stderr.trim()}` : ''}`)
          }

          settled = true
          activation.cancelGrant = null
          resolve(
            event === 'foreground_allowed' ? 'allowed' : event === 'foreground_deferred' ? 'deferred' : 'cancelled'
          )
        } catch (error) {
          finishFailure(error instanceof Error ? error.message : String(error))
        }
      })
      grant.stdin.end()
    })
  }

  function cancelExternalAuthorization(activation: ExternalActivation, focus: ExternalFocusRequest): void {
    if (!currentExternalActivation(activation, focus)) {
      return
    }

    clearExternalActivation()
    void request({ command: 'cancel_external_interaction' }, 3_000).catch(failure =>
      options.log(`[desktop] cancelling foreground authorization: ${String(failure)}`)
    )
  }

  function failExternalAuthorization(
    activation: ExternalActivation,
    focus: ExternalFocusRequest,
    error: unknown
  ): void {
    options.log(`[desktop] foreground authorization failed: ${error instanceof Error ? error.message : String(error)}`)

    if (!currentExternalActivation(activation, focus)) {
      return
    }

    options.onWarning?.('窗口前台授权未确认，请重新点击程序。')
    cancelExternalAuthorization(activation, focus)
  }

  async function authorizeExternalFocus(activation: ExternalActivation): Promise<void> {
    if (activation.authorizing) {
      return
    }

    activation.authorizing = true

    try {
      while (currentExternalActivation(activation) && activation.pendingFocus) {
        const focus = activation.pendingFocus
        activation.pendingFocus = null

        if (!(await waitForForegroundGrant(activation, focus)) || !currentExternalActivation(activation, focus)) {
          continue
        }

        let grant: ForegroundGrantResult

        try {
          grant = await runForegroundGrant(activation)
        } catch (error) {
          if (currentExternalActivation(activation, focus)) {
            failExternalAuthorization(activation, focus, error)
          }

          continue
        }

        if (!currentExternalActivation(activation, focus)) {
          continue
        }

        if (grant === 'cancelled') {
          cancelExternalAuthorization(activation, focus)

          continue
        }

        try {
          const result = await request<unknown>(
            { command: 'authorize_external_focus', activation_id: focus.activationId, granted: grant === 'allowed' },
            3_000
          )

          const status = parseExternalFocusAuthorization(result)

          if (!currentExternalActivation(activation, focus)) {
            continue
          }

          if (status === 'completed') {
            clearExternalActivation()
          } else if (status === 'deferred' || status === 'stale') {
            // 本轮授权已消费，后续重规划必须由新令牌重新取得权限。
            activation.latestFocus = null
          }
        } catch (error) {
          failExternalAuthorization(activation, focus, error)
        }
      }
    } finally {
      activation.authorizing = false

      if (externalActivation === activation && !currentExternalActivation(activation)) {
        clearExternalActivation()
      }
    }
  }

  function externalFocusMessage(message: Record<string, unknown>): void {
    const activation = externalActivation
    const token = message.activation_id
    const operationId = message.operation_id
    const revision = message.authorization_revision

    if (
      !activation ||
      !currentExternalActivation(activation) ||
      message.window_id !== activation.windowId ||
      typeof token !== 'string' ||
      !token ||
      token.length > 256 ||
      typeof operationId !== 'string' ||
      !operationId ||
      operationId.length > 256 ||
      typeof revision !== 'number' ||
      !Number.isSafeInteger(revision) ||
      revision < 1
    ) {
      return
    }

    const focus: ExternalFocusRequest = { operationId, activationId: token, revision }

    if (activation.nativeOperationId === null) {
      if (
        !activation.bufferedFocus ||
        activation.bufferedFocus.operationId !== operationId ||
        activation.bufferedFocus.revision < revision
      ) {
        activation.bufferedFocus = focus
      }

      return
    }

    queueExternalFocus(activation, focus)
  }

  function queueExternalFocus(activation: ExternalActivation, focus: ExternalFocusRequest): void {
    if (
      !currentExternalActivation(activation) ||
      activation.nativeOperationId !== focus.operationId ||
      focus.revision <= activation.authorizationRevision
    ) {
      return
    }

    activation.authorizationRevision = focus.revision
    activation.pendingFocus = focus
    activation.latestFocus = focus
    activation.cancelGrant?.()
    void authorizeExternalFocus(activation).catch(error =>
      options.log(`[desktop] foreground authorization failed: ${String(error)}`)
    )
  }

  function launchHelper(
    mode: 'host' | 'recover',
    handlers: {
      message: (message: Record<string, unknown>) => void
      failure: (reason: string) => void
      close: (code: number | null, signal: NodeJS.Signals | null, stderr: string) => void
    }
  ): ChildProcessWithoutNullStreams {
    const process = spawn(options.helperPath, [mode, '--journal', options.journalPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true
    })

    let buffer = ''
    let stderr = ''
    let failed = false

    const fail = (reason: string): void => {
      if (failed) {
        return
      }

      failed = true
      process.kill()
      handlers.failure(reason)
    }

    process.stdout.setEncoding('utf8')
    process.stderr.setEncoding('utf8')
    process.stderr.on('data', (chunk: string) => {
      stderr = (stderr + chunk).slice(-4096)
    })
    process.stdin.on('error', error => fail(`Desktop command channel failed: ${error.message}`))
    process.on('error', error => fail(`Desktop helper could not start: ${error.message}`))
    process.stdout.on('data', (chunk: string) => {
      if (failed) {
        return
      }

      buffer += chunk

      try {
        for (;;) {
          const newline = buffer.indexOf('\n')

          if (newline < 0) {
            break
          }

          const line = buffer.slice(0, newline)
          buffer = buffer.slice(newline + 1)

          if (Buffer.byteLength(line) > 65_536) {
            throw new Error('Desktop helper output line exceeds 64 KiB')
          }

          handlers.message(parseMessage(line))
        }

        if (Buffer.byteLength(buffer) > 65_536) {
          throw new Error('Desktop helper output line exceeds 64 KiB')
        }
      } catch (error) {
        fail(error instanceof Error ? error.message : String(error))
      }
    })
    process.once('close', (code, signal) => {
      if (!failed && buffer.length > 0) {
        fail('Desktop helper output is incomplete')
      }

      if (!failed) {
        handlers.close(code, signal, stderr.trim())
      }
    })

    return process
  }

  async function spawnHost(): Promise<void> {
    const currentGeneration = ++generation
    await new Promise<void>((resolve, reject) => {
      let ready = false

      const fail = (reason: string): void => {
        clearTimeout(timer)
        reject(new Error(reason))

        if (currentGeneration === generation) {
          reportFailure(reason)
        }
      }

      const timer = setTimeout(() => {
        fail('Desktop helper readiness timed out')

        if (currentGeneration === generation) {
          child?.kill()
        }
      }, 5_000)

      timer.unref()
      child = launchHelper('host', {
        failure: fail,
        close: (code, signal, stderr) =>
          fail(`Desktop helper exited (${code ?? signal ?? 'unknown'})${stderr ? `: ${stderr}` : ''}`),
        message: message => {
          if (currentGeneration !== generation) {
            return
          }

          if (message.event === 'host_ready' && !ready) {
            ready = true
            clearTimeout(timer)
            resolve()
          } else if (message.event === 'failure' && typeof message.reason === 'string') {
            fail(message.reason)
          } else if (
            message.event === 'foreground' &&
            typeof message.active === 'boolean' &&
            typeof message.stage_available === 'boolean' &&
            typeof message.fullscreen === 'boolean'
          ) {
            options.onForegroundChanged?.({
              active: message.active,
              stageAvailable: message.stage_available,
              fullscreen: message.fullscreen
            })
          } else if (message.event === 'warning' && typeof message.reason === 'string') {
            options.log(`[desktop] ${message.reason}`)

            if (message.notify !== false) {
              options.onWarning?.(message.reason)
            }
          } else if (message.event === 'applications') {
            applicationsMessage(message)
          } else if (message.event === 'external_focus') {
            externalFocusMessage(message)
          } else if (message.event === 'work_area') {
            if (!isPhysicalRect(message.work_area)) {
              throw new Error('Desktop helper returned an invalid work area')
            }

            options.onWorkAreaChanged?.({ ...message.work_area })
          } else if (message.event === 'system_tray') {
            const tray = message.tray

            if (tray === null) {
              options.onSystemTrayChanged?.(null)
            } else {
              if (
                !tray ||
                typeof tray !== 'object' ||
                Array.isArray(tray) ||
                !('bounds' in tray) ||
                !isPhysicalRect(tray.bounds) ||
                !('visible' in tray) ||
                typeof tray.visible !== 'boolean'
              ) {
                throw new Error('Desktop helper returned an invalid system tray')
              }

              options.onSystemTrayChanged?.({ bounds: { ...tray.bounds }, visible: tray.visible })
            }
          } else if (
            Number.isInteger(message.id) &&
            typeof message.id === 'number' &&
            message.id > 0 &&
            typeof message.ok === 'boolean'
          ) {
            const waiting = pending.get(message.id)

            if (!waiting) {
              return
            }

            pending.delete(message.id)
            clearTimeout(waiting.timer)

            if (message.ok) {
              waiting.resolve(message.result)
            } else {
              waiting.reject(
                new Error(typeof message.reason === 'string' ? message.reason : 'Desktop helper command failed')
              )
            }
          } else {
            throw new Error('Desktop helper returned an unsupported message')
          }
        }
      })
    })
  }

  function recoverJournal(): Promise<boolean> {
    if (process.platform !== 'win32') {
      return Promise.resolve(false)
    }

    if (recoveryProcess && !exited(recoveryProcess)) {
      return Promise.reject(new Error('Previous desktop recovery helper has not stopped'))
    }

    return new Promise<boolean>((resolve, reject) => {
      let restored: boolean | undefined
      let failing = false

      const fail = (reason: string): void => {
        if (failing) {
          return
        }

        failing = true
        clearTimeout(timer)

        if (!recovery.killed && !exited(recovery)) {
          recovery.kill()
        }

        void waitForExit(recovery, 2_000).then(stopped => {
          if (stopped) {
            recoveryProcess = null
          }

          reject(new Error(stopped ? reason : `${reason}; recovery helper could not be stopped`))
        })
      }

      const timer = setTimeout(() => {
        fail('Desktop shell recovery timed out')
      }, 8_000)

      timer.unref()

      const recovery = launchHelper('recover', {
        failure: fail,
        message: message => {
          if (message.event === 'failure' && typeof message.reason === 'string') {
            throw new Error(message.reason)
          }

          if (message.event !== 'recovered' || typeof message.restored !== 'boolean' || restored !== undefined) {
            throw new Error('Desktop helper returned an invalid recovery result')
          }

          restored = message.restored
        },
        close: (code, _signal, stderr) => {
          if (failing) {
            return
          }

          clearTimeout(timer)
          recoveryProcess = null

          if (code === 0 && restored !== undefined) {
            resolve(restored)
          } else {
            reject(new Error(`Desktop shell recovery failed (${code})${stderr ? `: ${stderr}` : ''}`))
          }
        }
      })

      recoveryProcess = recovery
      recovery.stdin.end()
    })
  }

  function exited(process: ChildProcessWithoutNullStreams): boolean {
    return process.exitCode !== null || process.signalCode !== null || process.pid === undefined
  }

  function waitForExit(process: ChildProcessWithoutNullStreams, budgetMs: number): Promise<boolean> {
    if (exited(process)) {
      return Promise.resolve(true)
    }

    return new Promise<boolean>(resolve => {
      const complete = (done: boolean): void => {
        clearTimeout(timer)
        process.removeListener('exit', onExit)
        resolve(done)
      }

      const onExit = (): void => complete(true)
      const timer = setTimeout(() => complete(false), budgetMs)
      timer.unref()
      process.once('exit', onExit)
    })
  }

  async function stopHost(): Promise<void> {
    clearExternalActivation()
    clearHeartbeat()
    state = 'stopping'
    const process = child
    let stopError: unknown

    if (process && !exited(process)) {
      if (!process.killed) {
        try {
          await request({ command: 'stop' }, 7_000)
        } catch (error) {
          stopError = error
        }
      }

      if (!(await waitForExit(process, 1_000))) {
        process.kill()

        if (!(await waitForExit(process, 2_000))) {
          throw new Error('Desktop helper could not be stopped')
        }
      }
    }

    ++generation
    applicationBatch = null
    child = null
    rejectPending(new Error('Desktop helper stopped'))
    state = 'idle'

    if (stopError || existsSync(options.journalPath)) {
      try {
        await recoverJournal()
      } catch (recoveryError) {
        if (!stopError) {
          throw recoveryError
        }

        const reason = stopError instanceof Error ? stopError.message : String(stopError)
        const recovery = recoveryError instanceof Error ? recoveryError.message : String(recoveryError)
        throw new Error(`${reason}; restoration: ${recovery}`)
      }
    }
  }

  return {
    setTrayLayout: (layout, eligible = () => true) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          return
        }

        if (layout !== null && (!isPhysicalRect(layout.bounds) || !isPhysicalRect(layout.clip))) {
          throw new Error('无效系统托盘布局。')
        }

        await request({ command: 'set_tray_layout', layout }, 4_000)
      })
    },
    setPalette: palette => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration) {
          return
        }

        await request({ command: 'set_palette', palette }, 4_000)
      })
    },
    focus: (handle, eligible = () => true) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration) {
          throw new Error('Desktop focus requires the current running host')
        }

        if (!eligible()) {
          return false
        }

        // helper 单线程命令循环里启动期首轮应用扫描可达秒级；超时只放弃本次激活，存活由心跳与 lease 兜底。
        await request({ command: 'focus', handle: encodeHandle(handle) }, 3_000)

        return true
      })
    },
    refreshApplications: eligible => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择程序。')
        }

        await request({ command: 'refresh_applications' }, 4_000)
      })
    },
    activateExternal: (windowId, allowRemote, eligible, continuation = eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择窗口。')
        }

        clearExternalActivation()
        const host = child

        if (!host || exited(host) || host.killed) {
          throw new Error('Desktop helper is not running')
        }

        const activation: ExternalActivation = {
          windowId,
          continuation,
          generation: currentGeneration,
          host,
          nativeOperationId: null,
          authorizationRevision: 0,
          bufferedFocus: null,
          pendingFocus: null,
          latestFocus: null,
          authorizing: false,
          cancelGrant: null
        }

        externalActivation = activation

        try {
          const result = await request<unknown>(
            { command: 'activate_external', window_id: windowId, allow_remote: allowRemote },
            3_000
          )

          if (!currentExternalActivation(activation)) {
            if (externalActivation === activation) {
              clearExternalActivation()
            }

            return
          }

          activation.nativeOperationId = parseExternalActivation(result)
          const buffered = activation.bufferedFocus
          activation.bufferedFocus = null

          if (buffered) {
            queueExternalFocus(activation, buffered)
          }
        } catch (error) {
          if (externalActivation === activation) {
            clearExternalActivation()
          }

          throw error
        }
      })
    },
    cancelExternalInteraction: () => {
      clearExternalActivation()

      if (state !== 'running' || !child || exited(child) || child.killed) {
        return Promise.resolve()
      }

      return request({ command: 'cancel_external_interaction' }, 3_000)
    },
    launchExternal: (target, appId, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新打开程序。')
        }

        if (
          typeof target !== 'string' ||
          target.length < 1 ||
          target.length > 32_768 ||
          typeof appId !== 'string' ||
          appId.length < 1 ||
          appId.length > 32_768
        ) {
          throw new Error('无效程序目标。')
        }

        await request({ command: 'launch_external', target, app_id: appId }, 4_000)
      })
    },
    closeExternal: (windowIds, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择窗口。')
        }

        await request({ command: 'close_external', window_ids: windowIds }, 3_000)
      })
    },
    getJumpListTasks: async (target, eligible) => {
      const currentGeneration = generation

      if (state !== 'running' || generation !== currentGeneration || !eligible()) {
        throw new Error('桌面已改变，请重新读取程序任务。')
      }

      if (typeof target !== 'string' || target.length < 1 || target.length > 32_768) {
        throw new Error('无效程序目标。')
      }

      const result = await request<unknown>({ command: 'get_jump_list_tasks', target }, 5_000)

      if (state !== 'running' || generation !== currentGeneration || !eligible()) {
        throw new Error('桌面已改变，请重新读取程序任务。')
      }

      return parseJumpListTasks(result)
    },
    activateJumpListTask: (target, taskId, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新执行程序任务。')
        }

        if (
          typeof target !== 'string' ||
          target.length < 1 ||
          target.length > 32_768 ||
          typeof taskId !== 'string' ||
          taskId.length < 1 ||
          taskId.length > 512
        ) {
          throw new Error('无效程序任务。')
        }

        await request(
          { command: 'activate_jump_list_task', target, task_id: taskId, expires_at: Date.now() + 5_000 },
          5_000
        )
      })
    },
    recover: () =>
      serialize(async () => {
        if (state !== 'idle') {
          throw new Error('Desktop recovery requires an idle host')
        }

        return recoverJournal()
      }),
    start: input =>
      serialize(async () => {
        if (process.platform !== 'win32') {
          throw new Error('Explorer desktop hosting is available on Windows only')
        }

        if (state !== 'idle') {
          throw new Error('Desktop host has already started')
        }

        if (recoveryProcess && !exited(recoveryProcess)) {
          throw new Error('Previous desktop recovery helper has not stopped')
        }

        if (input.parentPid !== process.pid || input.windows.length < 1 || input.windows.length > 32) {
          throw new Error('Desktop host requires the current Electron process and 1–32 windows')
        }

        const windows = input.windows.map(encodeWindow)
        state = 'starting'
        failureReported = false
        applicationBatch = null

        try {
          await spawnHost()
          await request(
            {
              command: 'start',
              parent_pid: input.parentPid,
              takeover: input.takeover,
              palette: input.palette,
              windows,
              work_area: input.workArea
            },
            15_000
          )
          state = 'running'
          heartbeatTimer = setInterval(() => {
            void request({ command: 'heartbeat' }, 4_000).catch(error => {
              reportFailure(error instanceof Error ? error.message : String(error))
            })
          }, 2_000)
          heartbeatTimer.unref()
        } catch (error) {
          try {
            await stopHost()
          } catch (cleanupError) {
            const reason = error instanceof Error ? error.message : String(error)
            const cleanup = cleanupError instanceof Error ? cleanupError.message : String(cleanupError)
            throw new Error(`${reason}; restoration: ${cleanup}`)
          }

          throw error
        }
      }),
    stop: () => serialize(stopHost)
  }
}
