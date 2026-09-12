import type { ChildProcess } from 'node:child_process'
import childProcess from 'node:child_process'
import { EventEmitter } from 'node:events'
import path from 'node:path'

import { errorMessage } from '../shared/utils'

import { resolveVenvPython } from './venv'

const DEFAULT_GRACE_MS = 4_000
const DEFAULT_STOP_TIMEOUT_MS = 8_000
const DEFAULT_HEALTH_TIMEOUT_MS = 8_000

export interface RunnerProcessState {
  args: null | string[]
  command: null | string
  exitCode: null | number
  exitSignal: NodeJS.Signals | null | string
  kind: null | string
  lastError: null | string
  pid: null | number
  running: boolean
  startedAt: null | number
}

type RunnerProcessEvent =
  | { args: string[]; command: string; pid: null | number | undefined; type: 'start' }
  | { data: string; type: 'stderr' | 'stdout' }
  | { code: null | number; signal: null | string; type: 'exit' }
  | { error: Error; type: 'error' }
  | { type: 'ready' }

export interface CreateRunnerProcessOptions {
  spiritagentHome?: null | string
  devPython?: null | string
  env?: Record<string, string | undefined>
  executable?: null | string
  fileExists?: (p: string) => boolean
  log?: (chunk: string) => void
  repoRoot?: null | string
  spawn?: typeof childProcess.spawn
  stopGraceMs?: number
  stopTimeoutMs?: number
}

export interface RunnerProcessStartArgs {
  authToken?: string
  endpointPath?: string
  executable?: string
  extraArgs?: string[]
}

export interface RunnerProcess {
  getStatus: () => RunnerProcessState
  onEvent: (callback: (event: RunnerProcessEvent) => void) => () => void
  restart: (args: RunnerProcessStartArgs) => Promise<RunnerProcessState>
  signalReady: () => void
  start: (args: RunnerProcessStartArgs) => Promise<RunnerProcessState>
  stop: (options?: {
    reason?: string
  }) => Promise<{ code?: null | number; noop?: boolean; ok: boolean; signal?: null | string }>
  waitForReady: (options?: { timeoutMs?: number }) => Promise<RunnerProcessState>
}

function resolveRunnerExecutable(options: {
  spiritagentHome?: null | string
  devPython?: null | string
  executable?: null | string
  fileExists: (p: string) => boolean
  repoRoot?: null | string
}): null | { args: string[]; command: string; kind: string } {
  if (options.executable) {
    return { args: [], command: options.executable, kind: 'binary' }
  }

  const venvResult = resolveVenvPython({
    spiritagentHome: options.spiritagentHome,
    fileExists: options.fileExists,
    platform: process.platform
  })

  if (venvResult) {
    return venvResult
  }

  if (options.devPython && options.repoRoot && options.fileExists(options.devPython)) {
    return {
      args: [path.join(options.repoRoot, 'runner', 'server.py')],
      command: options.devPython,
      kind: 'dev-python'
    }
  }

  return null
}

export function createRunnerProcess(options: CreateRunnerProcessOptions = {}): RunnerProcess {
  const emitter = new EventEmitter()
  const log = options.log || (() => {})
  const spawnFn = options.spawn || childProcess.spawn
  const fileExists = options.fileExists || (() => false)

  const stopGraceMs =
    typeof options.stopGraceMs === 'number' && Number.isFinite(options.stopGraceMs)
      ? options.stopGraceMs
      : DEFAULT_GRACE_MS

  const stopTimeoutMs =
    typeof options.stopTimeoutMs === 'number' && Number.isFinite(options.stopTimeoutMs)
      ? options.stopTimeoutMs
      : DEFAULT_STOP_TIMEOUT_MS

  let state: RunnerProcessState = {
    args: null,
    command: null,
    exitCode: null,
    exitSignal: null,
    kind: null,
    lastError: null,
    pid: null,
    running: false,
    startedAt: null
  }

  let child: ChildProcess | null = null

  function emit(event: RunnerProcessEvent): void {
    emitter.emit('event', event)
  }

  function setState(patch: Partial<RunnerProcessState>): void {
    state = { ...state, ...patch }
  }

  function getStatus(): RunnerProcessState {
    return { ...state }
  }

  // 平台分支：Windows 走 taskkill /T /F 强杀进程树，失败回退到 SIGTERM；
  // POSIX 直接 SIGTERM。SIGKILL 兜底留给 stop() 的 grace 超时分支。
  function requestGracefulKill(target: ChildProcess, graceMs: number): void {
    if (process.platform === 'win32') {
      childProcess.execFile(
        'taskkill',
        ['/PID', String(target.pid), '/T', '/F'],
        { timeout: Math.max(100, graceMs - 100), windowsHide: true },
        err => {
          if (err) {
            log(`[runner] taskkill failed for pid=${target.pid}: ${err.message}; falling back to SIGTERM`)

            try {
              target.kill('SIGTERM')
            } catch (error: unknown) {
              const msg = errorMessage(error)
              log(`[runner] SIGTERM fallback failed: ${msg}`)
            }
          }
        }
      )
    } else {
      try {
        target.kill('SIGTERM')
      } catch (error: unknown) {
        const msg = errorMessage(error)
        log(`[runner] SIGTERM failed: ${msg}`)
      }
    }
  }

  function buildArgs({ endpointPath, extraArgs }: RunnerProcessStartArgs): string[] {
    // token 经环境变量下发，不进 argv——同机进程列表可读命令行。
    const args = ['--desktop-endpoint', endpointPath || '']

    if (Array.isArray(extraArgs)) {
      args.push(...extraArgs)
    }

    return args
  }

  async function start(args: RunnerProcessStartArgs = {}): Promise<RunnerProcessState> {
    if (state.running) {
      throw new Error('Runner is already running.')
    }

    if (!args.endpointPath) {
      throw new Error('start() requires an endpointPath (named pipe or socket path).')
    }

    if (!args.authToken) {
      throw new Error('start() requires an authToken.')
    }

    const resolved = resolveRunnerExecutable({
      spiritagentHome: options.spiritagentHome,
      devPython: options.devPython,
      executable: args.executable,
      fileExists,
      repoRoot: options.repoRoot
    })

    if (!resolved) {
      const err = new Error(
        'Could not resolve a Runner executable. Pass options.executable, install the Runner venv under $SPIRITAGENT_HOME, or set SPIRITAGENT_DESKTOP_PYTHON for dev mode.'
      )

      setState({ lastError: err.message })
      throw err
    }

    const argv = [...resolved.args, ...buildArgs(args)]

    const env: NodeJS.ProcessEnv = {
      ...process.env,
      ...(options.spiritagentHome ? { SPIRITAGENT_HOME: options.spiritagentHome } : {}),
      SPIRITAGENT_DESKTOP_TOKEN: args.authToken || '',
      ...(options.env || {})
    }

    log(`[runner] spawn ${resolved.kind} ${resolved.command} ${argv.join(' ')}`)

    let handle: ChildProcess

    try {
      handle = spawnFn(resolved.command, argv, {
        env,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true
      })
    } catch (error: unknown) {
      const err = error instanceof Error ? error : new Error(String(error))
      setState({ lastError: err.message })
      log(`[runner] spawn failed: ${err.message}`)
      throw err
    }

    child = handle
    setState({
      args: argv,
      command: resolved.command,
      exitCode: null,
      exitSignal: null,
      kind: resolved.kind,
      lastError: null,
      pid: handle.pid ?? null,
      running: true,
      startedAt: Date.now()
    })

    handle.stdout?.on('data', chunk => emit({ data: chunk.toString(), type: 'stdout' }))
    handle.stderr?.on('data', chunk => {
      const text = chunk.toString()
      log(`[runner] stderr: ${text.trim()}`)
      emit({ data: text, type: 'stderr' })
    })
    handle.on('error', (error: Error) => {
      log(`[runner] error event: ${error.message}`)
      setState({ lastError: error.message })
      emit({ error, type: 'error' })

      // Node 不保证 error 后再发 exit；spawn 级失败须解除 running 门闩，否则 start 永久卡死。
      if (handle.exitCode == null && handle.signalCode == null) {
        setState({ exitCode: null, exitSignal: null, running: false })
        child = null
      }
    })
    handle.on('exit', (code, signal) => {
      log(`[runner] exit code=${code} signal=${signal}`)
      setState({ exitCode: code, exitSignal: signal, running: false })
      child = null
      emit({ code, signal, type: 'exit' })
    })

    emit({ args: argv, command: resolved.command, pid: handle.pid, type: 'start' })

    return getStatus()
  }

  function stop({ reason }: { reason?: string } = {}): Promise<{
    code?: null | number
    noop?: boolean
    ok: boolean
    signal?: null | string
  }> {
    if (!state.running || !child) {
      return Promise.resolve({ noop: true, ok: true })
    }

    log(`[runner] stop reason=${reason || 'unspecified'}`)

    return new Promise(resolve => {
      const target = child!
      let settled = false
      let forceKillTimer: ReturnType<typeof setTimeout> | null = null
      let forceSettleTimer: ReturnType<typeof setTimeout> | null = null
      let stopTimeoutTimer: ReturnType<typeof setTimeout> | null = null

      const finalize = (code: null | number, signal: null | string, fromExit: boolean) => {
        if (settled) {
          return
        }

        settled = true

        if (forceKillTimer) {
          clearTimeout(forceKillTimer)
          forceKillTimer = null
        }

        if (forceSettleTimer) {
          clearTimeout(forceSettleTimer)
          forceSettleTimer = null
        }

        if (stopTimeoutTimer) {
          clearTimeout(stopTimeoutTimer)
          stopTimeoutTimer = null
        }

        // 超时兜底也允许再次 start：门闩与 stop 终态对齐，避免 restart 卡死。
        if (state.running && target.exitCode == null && target.signalCode == null) {
          setState({ running: false })

          if (child === target) {
            child = null
          }
        }

        target.removeListener('exit', onExit)
        const ok = fromExit ? code === 0 || signal !== null : false
        resolve({ code, ok, signal })
      }

      const onExit = (code: null | number, signal: null | string) => finalize(code, signal, true)

      target.once('exit', onExit)

      const settleIfExited = (fallbackSignal: null | string) => {
        if (target.exitCode != null || target.signalCode != null) {
          finalize(target.exitCode, target.signalCode, true)
        } else {
          finalize(null, fallbackSignal, false)
        }
      }

      requestGracefulKill(target, stopGraceMs)

      forceKillTimer = setTimeout(() => {
        if (settled) {
          return
        }

        log(`[runner] grace expired; force-killing pid=${target.pid}`)

        try {
          target.kill('SIGKILL')
        } catch (error: unknown) {
          const msg = errorMessage(error)
          log(`[runner] SIGKILL failed: ${msg}`)
        }

        forceSettleTimer = setTimeout(() => {
          if (target.exitCode == null && target.signalCode == null) {
            log(`[runner] pid=${target.pid} still alive after SIGKILL; reporting failure`)
          }

          settleIfExited('SIGKILL')
        }, 500)

        if (typeof forceSettleTimer.unref === 'function') {
          forceSettleTimer.unref()
        }
      }, stopGraceMs)

      stopTimeoutTimer = setTimeout(() => settleIfExited(null), stopTimeoutMs)

      if (typeof stopTimeoutTimer.unref === 'function') {
        stopTimeoutTimer.unref()
      }
    })
  }

  async function restart(args: RunnerProcessStartArgs): Promise<RunnerProcessState> {
    await stop({ reason: 'restart' })

    return start(args)
  }

  function onEvent(callback: (event: RunnerProcessEvent) => void): () => void {
    emitter.on('event', callback)

    return () => emitter.off('event', callback)
  }

  function waitForReady({
    timeoutMs = DEFAULT_HEALTH_TIMEOUT_MS
  }: { timeoutMs?: number } = {}): Promise<RunnerProcessState> {
    return new Promise((resolve, reject) => {
      if (!state.running) {
        reject(new Error('Runner is not running.'))

        return
      }

      const timer = setTimeout(() => {
        detach()
        reject(new Error(`Runner failed to become ready within ${timeoutMs}ms`))
      }, timeoutMs)

      const onEventHandler = (ev: RunnerProcessEvent) => {
        if (ev.type === 'ready') {
          detach()
          clearTimeout(timer)
          resolve(getStatus())
        } else if (ev.type === 'exit') {
          detach()
          clearTimeout(timer)
          reject(new Error(`Runner exited before becoming ready (code=${ev.code}, signal=${ev.signal})`))
        }
      }

      function detach() {
        emitter.off('event', onEventHandler)
      }

      emitter.on('event', onEventHandler)
    })
  }

  function signalReady(): void {
    emit({ type: 'ready' })
  }

  return {
    getStatus,
    onEvent,
    restart,
    signalReady,
    start,
    stop,
    waitForReady
  }
}
