import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

import type { RunningApplicationsState, RunningApplicationWindow } from '../shared/desktop-applications'
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
    Number.isSafeInteger(item.lastActive) &&
    typeof item.lastActive === 'number' &&
    item.lastActive >= 0
  )
}

export interface ExplorerDesktopWindow {
  handle: Buffer
  role: 'background' | 'overlay' | 'companion'
  /** 使用 screen.dipToScreenRect 转换完整显示器边界；helper 校正最多两像素的舍入。 */
  bounds: { x: number; y: number; width: number; height: number }
}

interface HostOptions {
  helperPath: string
  journalPath: string
  log: (message: string) => void
  onFailure: (reason: string) => void
  onForegroundChanged?: (state: { active: boolean; stageAvailable: boolean; fullscreen: boolean }) => void
  onWarning?: (reason: string) => void
  onApplicationsChanged?: (state: RunningApplicationsState) => void
}

export interface ExplorerDesktopHost {
  start: (options: {
    windows: ExplorerDesktopWindow[]
    parentPid: number
    takeover: boolean
    workArea: ExplorerDesktopWindow['bounds']
    companionAlwaysOnTop: boolean
  }) => Promise<void>
  setCompanionAlwaysOnTop: (enabled: boolean) => Promise<void>
  focus: (handle: Buffer, eligible?: () => boolean) => Promise<boolean>
  refreshApplications: (eligible: () => boolean) => Promise<void>
  activateExternal: (windowId: string, eligible: () => boolean) => Promise<void>
  closeExternal: (windowIds: string[], eligible: () => boolean) => Promise<void>
  stop: () => Promise<void>
  recover: () => Promise<boolean>
}

interface PendingRequest {
  resolve: () => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

function parseMessage(line: string): Record<string, unknown> {
  const value: unknown = JSON.parse(line)

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned an invalid message')
  }

  return value as Record<string, unknown>
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

  if (
    !Object.values(bounds).every(Number.isSafeInteger) ||
    bounds.width <= 0 ||
    bounds.height <= 0 ||
    bounds.width > 65_536 ||
    bounds.height > 65_536 ||
    Math.abs(bounds.x) > 1_000_000 ||
    Math.abs(bounds.y) > 1_000_000
  ) {
    throw new Error('Desktop native window handle or physical bounds are invalid')
  }

  return { bounds: { ...bounds }, handle, role: window.role }
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
    clearHeartbeat()
    rejectPending(new Error(reason))

    if (!failureReported && state === 'running') {
      failureReported = true
      options.log(`Explorer desktop host failed: ${reason}`)
      options.onFailure(reason)
    }
  }

  function request(command: Record<string, unknown>, timeoutMs: number, fatalTimeout = false): Promise<void> {
    const process = child

    if (!process || exited(process) || process.killed) {
      return Promise.reject(new Error('Desktop helper is not running'))
    }

    const id = nextId++

    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id)
        const reason = `Desktop helper ${String(command.command)} timed out`

        if (fatalTimeout) {
          process.kill()
          reportFailure(reason)
        }

        reject(new Error(reason))
      }, timeoutMs)

      timer.unref()
      pending.set(id, { reject, resolve, timer })
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
            options.onWarning?.(message.reason)
          } else if (message.event === 'applications') {
            applicationsMessage(message)
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
              waiting.resolve()
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
    setCompanionAlwaysOnTop: enabled =>
      serialize(async () => {
        if (state !== 'running') {
          throw new Error('Desktop host is not running')
        }

        await request({ command: 'companion_layer', always_on_top: enabled }, 4_000, true)
      }),
    focus: (handle, eligible = () => true) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration) {
          throw new Error('Desktop focus requires the current running host')
        }

        if (!eligible()) {
          return false
        }

        await request({ command: 'focus', handle: encodeHandle(handle) }, 1_000, true)

        return true
      })
    },
    refreshApplications: eligible => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择程序。')
        }

        await request({ command: 'refresh_applications' }, 2_000, true)
      })
    },
    activateExternal: (windowId, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择窗口。')
        }

        await request({ command: 'activate_external', window_id: windowId }, 1_000, true)
      })
    },
    closeExternal: (windowIds, eligible) => {
      const currentGeneration = generation

      return serialize(async () => {
        if (state !== 'running' || generation !== currentGeneration || !eligible()) {
          throw new Error('桌面已改变，请重新选择窗口。')
        }

        await request({ command: 'close_external', window_ids: windowIds }, 1_000, true)
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
              windows,
              work_area: input.workArea,
              companion_always_on_top: input.companionAlwaysOnTop
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
