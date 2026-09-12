import crypto from 'node:crypto'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import type { RunnerCapabilities, RunnerCapabilitiesHealth } from '@ipc/contracts'

import type { BackendSessionLike } from '../shared/backend-port'
import { atomicWriteFile, errorMessage } from '../shared/utils'

import type { RunnerProcess, RunnerProcessStartArgs, RunnerProcessState } from './process'
import type { ReverseRpcOptions } from './reverse-rpc'
import type { CreateRunnerWsServerOptions, RunnerWsEvent, RunnerWsServer, RunnerWsStatus } from './rpc-ws'

// macOS 的 sun_path 上限为 104 字节；留出余量以确保不超限。
const MAC_SOCK_PATH_BYTE_LIMIT = 100

function computeDesktopEndpoint(spiritagentHome?: null | string): { path: string; transport: string } {
  if (process.platform === 'win32') {
    return { path: `\\\\.\\pipe\\spiritagent-runner-${process.pid}`, transport: 'pipe' }
  }

  const home = spiritagentHome || os.tmpdir()
  const primary = path.join(home, `runner-${process.pid}.sock`)

  if (Buffer.byteLength(primary) <= MAC_SOCK_PATH_BYTE_LIMIT) {
    return { path: primary, transport: 'unix' }
  }

  const digest = crypto.createHash('sha256').update(`${home}|${process.pid}`).digest('hex').slice(0, 8)
  const uid = typeof process.getuid === 'function' ? process.getuid() : 0

  return { path: path.join(os.tmpdir(), `spiritagent-${uid}-${digest}.sock`), transport: 'unix' }
}

function sweepLegacySockets(spiritagentHome: string): void {
  try {
    for (const name of fs.readdirSync(spiritagentHome)) {
      if (/^runner-\d+\.sock$/.test(name)) {
        try {
          fs.unlinkSync(path.join(spiritagentHome, name))
        } catch {
          /* 已被竞争移除 */
        }
      }
    }
  } catch {
    /* spiritagentHome 缺失或不可读——没有可清理的内容 */
  }
}

export interface RunnerBridgeStartOptions extends RunnerProcessStartArgs {
  backendSession?: BackendSessionLike | null
  readyTimeoutMs?: number
}

export interface RunnerBridgeOptions {
  spiritagentHome?: null | string
  log?: (chunk: string) => void
  processFactory?: null | ((args?: RunnerBridgeStartOptions) => RunnerProcess)
  pushConfig?: null | (() => Promise<unknown> | void)
  reverseRpcFactory?: null | ((options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>)
  wsServerFactory?: null | ((options: CreateRunnerWsServerOptions) => RunnerWsServer)
}

interface RunnerBridgeState {
  capabilities: null | RunnerCapabilities
  capabilitiesHealth: null | RunnerCapabilitiesHealth
  lastError: null | string
  phase: 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'
  probeFailed: boolean | null
  runnerVersion: null | string
  startedAt: null | number
  stoppedAt: null | number
}

export interface RunnerBridgeStatus extends RunnerBridgeState {
  runner: null | RunnerProcessState
  wsServer: null | RunnerWsStatus
}

export type RunnerBridgeEvent =
  | {
      capabilities: null | RunnerCapabilities
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      probeFailed: boolean | null
      runnerVersion: null | string
      tools: Record<string, unknown>[] | null
      type: 'runner_ready' | 'running'
    }
  | { error: Error; phase: string; type: 'error' }
  | { errors?: string[]; reason?: string; type: 'stopped' }

export interface RunnerBridge {
  dispatch: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    opts?: { id?: number | string; timeoutMs?: number }
  ) => Promise<T>
  getStatus: () => RunnerBridgeStatus
  getTools: () => Record<string, unknown>[]
  invoke: <T = unknown>(
    name: string,
    args?: Record<string, unknown>,
    opts?: { id?: number | string; timeoutMs?: number }
  ) => Promise<T>
  onEvent: (callback: (event: RunnerBridgeEvent) => void) => () => void
  start: (args?: RunnerBridgeStartOptions) => Promise<RunnerBridgeStatus>
  stop: (options?: { reason?: string }) => Promise<{ errors?: string[]; noop?: boolean; ok: boolean }>
}

export function createRunnerBridge(options: RunnerBridgeOptions = {}): RunnerBridge {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const emit = new EventEmitter()

  const onEvent = (callback: (event: RunnerBridgeEvent) => void) => {
    emit.on('event', callback)

    return () => emit.off('event', callback)
  }

  const processFactory = options.processFactory || null
  const wsServerFactory = options.wsServerFactory || null
  const reverseRpcFactory = options.reverseRpcFactory || null
  const pushConfig = typeof options.pushConfig === 'function' ? options.pushConfig : null

  let runnerProcess: null | RunnerProcess = null
  let wsServer: null | RunnerWsServer = null
  let cachedTools: Record<string, unknown>[] | null = null
  let subUnsubFns: Array<() => void> = []
  let endpointFilePath: null | string = null

  let state: RunnerBridgeState = {
    capabilities: null,
    capabilitiesHealth: null,
    lastError: null,
    phase: 'idle',
    probeFailed: null,
    runnerVersion: null,
    startedAt: null,
    stoppedAt: null
  }

  // start/stop 操作代数：stop 递增后，被打断的 start 在 await 恢复时自废。
  let opGeneration = 0

  function setState(patch: Partial<RunnerBridgeState>): void {
    state = { ...state, ...patch }
  }

  function getStatus(): RunnerBridgeStatus {
    return {
      ...state,
      runner: runnerProcess?.getStatus?.() ?? null,
      wsServer: wsServer?.getStatus?.() ?? null
    }
  }

  function fail(phase: 'error' | 'stopped', error: unknown): Error {
    const err = error instanceof Error ? error : new Error(String(error))
    setState({ lastError: err.message, phase })
    emit.emit('event', { error: err, phase, type: 'error' })

    if (phase === 'error') {
      emit.emit('event', { errors: [err.message], reason: err.message, type: 'stopped' })
    }

    return err
  }

  function detachSubs(): void {
    for (const off of subUnsubFns) {
      try {
        off()
      } catch {
        /* 早已解绑 */
      }
    }

    subUnsubFns = []
  }

  async function writeEndpointFile(endpoint: { path: string; token: string; transport: string }): Promise<void> {
    const spiritagentHome = options.spiritagentHome

    if (!spiritagentHome) {
      return
    }

    endpointFilePath = path.join(spiritagentHome, 'desktop-endpoint.json')

    const payload = JSON.stringify({
      path: endpoint.path,
      pid: process.pid,
      timestamp: Date.now(),
      token: endpoint.token,
      transport: endpoint.transport
    })

    try {
      await atomicWriteFile(endpointFilePath, payload)

      if (process.platform !== 'win32') {
        try {
          fs.chmodSync(endpointFilePath, 0o600)
        } catch {
          // 尽力而为；某些文件系统不支持 chmod
        }
      }

      log(
        `[runner-bridge] wrote endpoint file: transport=${endpoint.transport} path=${endpoint.path} pid=${process.pid}`
      )
    } catch (error: unknown) {
      const msg = errorMessage(error)
      log(`[runner-bridge] failed to write endpoint file: ${msg}`)
    }
  }

  function cleanupEndpointFile(): void {
    if (!endpointFilePath) {
      return
    }

    try {
      fs.unlinkSync(endpointFilePath)
      log('[runner-bridge] cleaned up endpoint file')
    } catch (error: unknown) {
      const err = error as { code?: string; message?: string }

      if (err?.code !== 'ENOENT') {
        log(`[runner-bridge] failed to cleanup endpoint file: ${err.message || String(error)}`)
      }
    }

    endpointFilePath = null
  }

  async function rollback(reason: string): Promise<void> {
    detachSubs()
    const tasks: Promise<unknown>[] = []

    if (wsServer) {
      tasks.push(wsServer.stop().catch(e => e))
    }

    if (runnerProcess) {
      tasks.push(runnerProcess.stop({ reason }).catch(e => e))
    }

    await Promise.all(tasks)
    cleanupEndpointFile()
    wsServer = null
    runnerProcess = null
    cachedTools = null
  }

  async function start(args: RunnerBridgeStartOptions = {}): Promise<RunnerBridgeStatus> {
    detachSubs()

    if (state.phase === 'starting' || state.phase === 'running' || state.phase === 'stopping') {
      throw new Error('Runner bridge is already running.')
    }

    const gen = ++opGeneration

    setState({
      lastError: null,
      phase: 'starting',
      startedAt: Date.now(),
      stoppedAt: null
    })

    const processInstance = processFactory ? processFactory(args) : null

    if (!processInstance) {
      throw fail('error', new Error('No runner process factory wired.'))
    }

    runnerProcess = processInstance

    const offProcess = runnerProcess.onEvent?.(ev => {
      if (ev.type === 'exit') {
        if (state.phase === 'running') {
          fail('stopped', new Error(`Runner exited (code=${ev.code}, signal=${ev.signal})`))
        } else if (state.phase === 'starting') {
          // 主动 stop 走 stopping，不在此记 error；仅启动期异常退出算 error。
          fail('error', new Error(`Runner exited during ${state.phase} (code=${ev.code}, signal=${ev.signal})`))
        }
      }
    })

    if (typeof offProcess === 'function') {
      subUnsubFns.push(offProcess)
    }

    const authToken = crypto.randomBytes(32).toString('hex')
    const endpoint = computeDesktopEndpoint(options.spiritagentHome)

    if (process.platform !== 'win32' && options.spiritagentHome) {
      sweepLegacySockets(options.spiritagentHome)
    }

    const wsInstance = wsServerFactory
      ? wsServerFactory({
          authToken,
          log: options.log,
          onReverseRpc: reverseRpcFactory
            ? reverseRpcFactory({ backendSession: args.backendSession ?? null, log })
            : null
        })
      : null

    wsServer = wsInstance

    if (!wsInstance) {
      await rollback('ws-server-init')
      throw fail('error', new Error('No WS server factory wired.'))
    }

    const offWs = wsInstance.onEvent?.((ev: RunnerWsEvent) => {
      if (ev.type === 'runner_ready') {
        void handleRunnerReady(ev)
      } else if (ev.type === 'disconnected') {
        if (state.phase === 'running') {
          fail('stopped', new Error('Runner disconnected from WS server.'))
        }
      } else if (ev.type === 'error') {
        const errObj = ev.error as { message?: string }
        log(`[runner-bridge] ws server error: ${errObj?.message || String(ev.error)}`)
      } else if (ev.type === 'connected') {
        // connected 事件，无需处理
      } else {
        const detail =
          (ev as { type: string; method?: string }).type === 'notification'
            ? `notification ${(ev as { method: string }).method}`
            : 'unknown'

        log(`[runner-bridge] unhandled ws server event: ${detail}`)
      }
    })

    if (typeof offWs === 'function') {
      subUnsubFns.push(offWs)
    }

    try {
      const started = await wsInstance.start({ path: endpoint.path })
      log(
        `[runner-bridge] WS server listening on ${started?.transport || endpoint.transport} ${started?.path || endpoint.path}`
      )
      await writeEndpointFile({ ...endpoint, token: authToken })
    } catch (error) {
      await rollback('ws-server-start')

      if (gen === opGeneration) {
        throw fail('error', error)
      }

      throw error
    }

    if (gen !== opGeneration) {
      await rollback('start-superseded')
      throw new Error('Runner bridge start was superseded by stop.')
    }

    try {
      await processInstance.start({ authToken, endpointPath: endpoint.path, executable: args.executable })
    } catch (error) {
      await rollback('process-start')

      if (gen === opGeneration) {
        throw fail('error', error)
      }

      throw error
    }

    if (gen !== opGeneration) {
      await rollback('start-superseded')
      throw new Error('Runner bridge start was superseded by stop.')
    }

    try {
      await processInstance.waitForReady({ timeoutMs: args.readyTimeoutMs ?? 8_000 })
    } catch (error) {
      await rollback('ready-timeout')

      if (gen === opGeneration) {
        throw fail('error', error)
      }

      throw error
    }

    if (gen !== opGeneration) {
      await rollback('start-superseded')
      throw new Error('Runner bridge start was superseded by stop.')
    }

    return getStatus()
  }

  async function handleRunnerReady(payload: {
    capabilities?: null | RunnerCapabilities
    capabilities_health?: null | RunnerCapabilitiesHealth
    probe_failed?: boolean | null
    version?: null | string
  }): Promise<void> {
    log('[runner-bridge] runner_ready received')

    if (runnerProcess?.signalReady) {
      runnerProcess.signalReady()
    }

    if (payload && typeof payload === 'object') {
      setState({
        capabilities: payload.capabilities ?? null,
        capabilitiesHealth: payload.capabilities_health ?? null,
        probeFailed: payload.probe_failed ?? null,
        runnerVersion: payload.version ?? null
      })
    }

    if (state.phase !== 'starting') {
      if (pushConfig) {
        Promise.resolve(pushConfig()).catch(err => {
          const msg = errorMessage(err)
          log(`[runner-bridge] config push on reconnect failed: ${msg}`)
        })
      }

      emit.emit('event', {
        capabilities: state.capabilities,
        capabilitiesHealth: state.capabilitiesHealth,
        probeFailed: state.probeFailed,
        runnerVersion: state.runnerVersion,
        tools: cachedTools,
        type: 'runner_ready'
      })

      return
    }

    if (pushConfig) {
      try {
        await pushConfig()
      } catch (err: unknown) {
        const msg = errorMessage(err)
        log(`[runner-bridge] initial config push failed: ${msg}`)
      }
    }

    await _fetchAndCacheTools()

    if (state.phase !== 'starting') {
      return
    }

    setState({ phase: 'running' })
    emit.emit('event', {
      capabilities: state.capabilities,
      capabilitiesHealth: state.capabilitiesHealth,
      probeFailed: state.probeFailed,
      runnerVersion: state.runnerVersion,
      tools: cachedTools,
      type: 'running'
    })
  }

  async function _fetchAndCacheTools(): Promise<void> {
    try {
      const result = await wsServer!.call<{ tools?: Record<string, unknown>[] }>('get_tools', {}, { timeoutMs: 10_000 })
      cachedTools = (result?.tools as Record<string, unknown>[]) || []
      log(`[runner-bridge] got ${cachedTools.length} tools from runner`)

      if (cachedTools.length > 0) {
        const names = cachedTools
          .map(t => {
            const func = t?.function as { name?: string } | undefined

            return func?.name || (t?.name as string | undefined)
          })
          .filter(Boolean)

        log(`[runner-bridge] tool names: ${names.join(', ') || '(unparseable schemas)'}`)
      }
    } catch (error: unknown) {
      const msg = errorMessage(error)
      log(`[runner-bridge] get_tools failed: ${msg}`)
      cachedTools = []
    }
  }

  async function stop({ reason }: { reason?: string } = {}): Promise<{
    errors?: string[]
    noop?: boolean
    ok: boolean
  }> {
    if (state.phase === 'idle' || state.phase === 'stopped') {
      return { noop: true, ok: true }
    }

    // 已有 stop 在跑时复用同一次收尾，避免双 kill / 双 stopped。
    if (state.phase === 'stopping') {
      return new Promise(resolve => {
        const off = onEvent(ev => {
          if (ev.type === 'stopped') {
            off()
            resolve({ errors: ev.errors, ok: !(ev.errors && ev.errors.length > 0) })
          }
        })
      })
    }

    opGeneration++
    setState({ phase: 'stopping' })
    log(`[runner-bridge] stop reason=${reason || 'unspecified'}`)

    const errors: unknown[] = []
    const tasks: Promise<unknown>[] = []

    if (wsServer) {
      tasks.push(
        wsServer.stop().catch(e => {
          errors.push(e)
        })
      )
    }

    if (runnerProcess) {
      tasks.push(
        runnerProcess.stop({ reason: reason || 'desktop-stop' }).catch(e => {
          errors.push(e)
        })
      )
    }

    await Promise.all(tasks)

    cleanupEndpointFile()
    detachSubs()
    wsServer = null
    runnerProcess = null
    cachedTools = null
    setState({ phase: 'stopped', stoppedAt: Date.now() })
    const errorStrings = errors.map(e => (e instanceof Error ? e.message : String(e)))
    emit.emit('event', { errors: errorStrings, reason, type: 'stopped' })

    return { errors: errorStrings, ok: errors.length === 0 }
  }

  async function _rpc<T = unknown>(
    method: string,
    params: Record<string, unknown>,
    opts: { id?: number | string; timeoutMs?: number } = {}
  ): Promise<T> {
    if (!wsServer || !wsServer.getStatus()?.connected) {
      throw new Error('Runner is not connected.')
    }

    return wsServer.call<T>(method, params || {}, opts)
  }

  const invoke = <T = unknown>(
    name: string,
    args?: Record<string, unknown>,
    opts?: { id?: number | string; timeoutMs?: number }
  ): Promise<T> => _rpc<T>('execute_tool', { args: args || {}, name }, opts)

  const dispatch = <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    opts?: { id?: number | string; timeoutMs?: number }
  ): Promise<T> => _rpc<T>(method, params || {}, opts)

  function getTools(): Record<string, unknown>[] {
    return cachedTools || []
  }

  return {
    dispatch,
    getStatus,
    getTools,
    invoke,
    onEvent,
    start,
    stop
  }
}
