import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

export interface ExplorerDesktopWindow {
  handle: Buffer
  /** 使用 Electron screen.dipToScreenRect 转换的屏幕物理像素。 */
  bounds: { x: number; y: number; width: number; height: number }
}

interface HostOptions {
  helperPath: string
  journalPath: string
  log: (message: string) => void
  onFailure: (reason: string) => void
  onForegroundChanged?: (active: boolean) => void
}

export interface ExplorerDesktopHost {
  start: (options: { windows: ExplorerDesktopWindow[]; parentPid: number; takeover: boolean }) => Promise<void>
  heartbeat: () => Promise<void>
  stop: () => Promise<void>
  recover: () => Promise<boolean>
  status: () => 'idle' | 'starting' | 'running' | 'stopping'
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

function encodeWindow(window: ExplorerDesktopWindow): { handle: string; bounds: ExplorerDesktopWindow['bounds'] } {
  if (window.handle.length !== 4 && window.handle.length !== 8) {
    throw new Error('Desktop native window handle has an unsupported size')
  }

  const handle = window.handle.length === 8 ? window.handle.readBigUInt64LE() : BigInt(window.handle.readUInt32LE())
  const bounds = window.bounds

  if (
    handle === 0n ||
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

  return { bounds: { ...bounds }, handle: handle.toString(16) }
}

export function createExplorerDesktopHost(options: HostOptions): ExplorerDesktopHost {
  if (!path.isAbsolute(options.helperPath) || !path.isAbsolute(options.journalPath)) {
    throw new Error('Desktop helper and journal paths must be absolute')
  }

  let state: ReturnType<ExplorerDesktopHost['status']> = 'idle'
  let child: ChildProcessWithoutNullStreams | null = null
  let recoveryProcess: ChildProcessWithoutNullStreams | null = null
  let nextId = 1
  let generation = 0
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let operation: Promise<unknown> = Promise.resolve()
  let failureReported = false
  const pending = new Map<number, PendingRequest>()

  function serialize<T>(work: () => Promise<T>): Promise<T> {
    const current = operation.then(work, work)
    operation = current.catch(() => undefined)

    return current
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

  function request(command: Record<string, unknown>, timeoutMs: number): Promise<void> {
    const process = child

    if (!process || exited(process) || process.killed) {
      return Promise.reject(new Error('Desktop helper is not running'))
    }

    const id = nextId++

    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id)
        reject(new Error(`Desktop helper ${String(command.command)} timed out`))
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

      if (Buffer.byteLength(buffer) > 65_536) {
        fail('Desktop helper output exceeds 64 KiB')

        return
      }

      try {
        for (;;) {
          const newline = buffer.indexOf('\n')

          if (newline < 0) {
            break
          }

          const line = buffer.slice(0, newline)
          buffer = buffer.slice(newline + 1)
          handlers.message(parseMessage(line))
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
          } else if (message.event === 'foreground' && typeof message.active === 'boolean') {
            options.onForegroundChanged?.(message.active)
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
    heartbeat: async () => {
      if (state !== 'running') {
        return
      }

      await request({ command: 'heartbeat' }, 4_000)
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

        try {
          await spawnHost()
          await request({ command: 'start', parent_pid: input.parentPid, takeover: input.takeover, windows }, 15_000)
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
    status: () => state,
    stop: () => serialize(stopHost)
  }
}
