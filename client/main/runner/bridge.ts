import crypto from 'node:crypto'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { sleep } from '@runtime'

import type { BackendSessionLike } from '../shared/backend-port'
import { atomicWriteFile, createSerialQueue, errorMessage, RunnerNotConnectedError } from '../shared/utils'

import type { RunnerProcess, RunnerProcessState } from './process'
import type { ReverseRpcOptions } from './reverse-rpc'
import type {
  CreateRunnerWsServerOptions,
  RunnerCapabilities,
  RunnerCapabilitiesHealth,
  RunnerWsEvent,
  RunnerWsServer,
  RunnerWsStatus
} from './rpc-ws'

// macOS 的 sun_path 上限为 104 字节；留出余量以确保不超限。
const MAC_SOCK_PATH_BYTE_LIMIT = 100
// 三次完整握手最多 45 秒 RPC + 750 ms 退避；外层另留进程启动与调度余量。
const READY_TIMEOUT_MS = 60_000
const HANDSHAKE_ATTEMPTS = 3

function computeDesktopEndpoint(spiritagentHome?: null | string): { path: string; transport: string } {
  if (process.platform === 'win32') {
    return { path: `\\\\.\\pipe\\spiritagent-runner-${process.pid}`, transport: 'pipe' }
  }

  const home = spiritagentHome || os.tmpdir()
  const primary = path.join(home, `runner-${process.pid}.sock`)

  if (Buffer.byteLength(primary) <= MAC_SOCK_PATH_BYTE_LIMIT) {
    return { path: primary, transport: 'unix' }
  }

  const digest = crypto.hash('sha256', `${home}|${process.pid}`).slice(0, 8)
  const uid = typeof process.getuid === 'function' ? process.getuid() : 0

  return { path: path.join(os.tmpdir(), `spiritagent-${uid}-${digest}.sock`), transport: 'unix' }
}

export interface RunnerBridgeStartOptions {
  /** 缺失时 start 直接拒绝：反向 RPC 必须持有会话。 */
  backendSession?: BackendSessionLike | null
  readyTimeoutMs?: number
}

export interface RunnerBridgeOptions {
  spiritagentHome?: null | string
  log?: (chunk: string) => void
  processFactory: () => RunnerProcess
  pushConfig: (server: RunnerWsServer, force?: boolean) => Promise<boolean>
  reverseRpcFactory: (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>
  wsServerFactory: (options: CreateRunnerWsServerOptions) => RunnerWsServer
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
  | { errors?: string[]; reason?: string; type: 'stopped' | 'stopping' }

export interface RunnerBridge {
  dispatch: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    opts?: { id?: number | string; timeoutMs?: number }
  ) => Promise<T>
  getStatus: () => RunnerBridgeStatus
  getTools: () => Record<string, unknown>[]
  onEvent: (callback: (event: RunnerBridgeEvent) => void) => () => void
  start: (args?: RunnerBridgeStartOptions) => Promise<RunnerBridgeStatus>
  stop: (options?: { reason?: string }) => Promise<{ errors?: string[]; noop?: boolean; ok: boolean }>
  syncConfig: () => Promise<void>
}

export function createRunnerBridge(options: RunnerBridgeOptions): RunnerBridge {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const emitter = new EventEmitter()

  const publish = (event: RunnerBridgeEvent): void => {
    emitter.emit('event', event)
  }

  const onEvent = (callback: (event: RunnerBridgeEvent) => void) => {
    emitter.on('event', callback)

    return () => emitter.off('event', callback)
  }

  const { processFactory, pushConfig, reverseRpcFactory, wsServerFactory } = options

  let runnerProcess: null | RunnerProcess = null
  let wsServer: null | RunnerWsServer = null
  let cachedTools: Record<string, unknown>[] | null = null
  let toolsGeneration = 0
  let toolsRefreshPending = false
  let handshakePending = false
  let hasBeenReady = false
  const enqueueConfig = createSerialQueue()
  let subUnsubFns: Array<() => void> = []
  let endpointFilePath: null | string = null
  let starting: Promise<RunnerBridgeStatus> | null = null

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
      runner: runnerProcess?.getStatus() ?? null,
      wsServer: wsServer?.getStatus() ?? null
    }
  }

  function fail(phase: 'error' | 'stopped', error: unknown): Error {
    const err = error instanceof Error ? error : new Error(String(error))
    toolsGeneration++
    cachedTools = null
    setState({ lastError: err.message, phase })
    publish({ error: err, phase, type: 'error' })

    if (phase === 'error') {
      publish({ errors: [err.message], reason: err.message, type: 'stopped' })
    }

    return err
  }

  // 就绪事件：握手完成与运行中清单变化共用，宿主据此重新同步工具。
  function publishReady(type: 'running' | 'runner_ready'): void {
    publish({
      capabilities: state.capabilities,
      capabilitiesHealth: state.capabilitiesHealth,
      probeFailed: state.probeFailed,
      runnerVersion: state.runnerVersion,
      tools: cachedTools,
      type
    })
  }

  // 断连、停止或下一次握手使旧查询失效，旧连接不能重新发布执行资格。
  function isStale(server: RunnerWsServer, generation: number): boolean {
    return generation !== toolsGeneration || server !== wsServer || !server.getStatus().connected
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
        log(`[runner-bridge] failed to cleanup endpoint file: ${errorMessage(error)}`)
      }
    }

    endpointFilePath = null
  }

  async function rollback(reason: string): Promise<void> {
    toolsGeneration++
    handshakePending = false
    detachSubs()
    await Promise.allSettled([wsServer?.stop(), runnerProcess?.stop({ reason })])
    cleanupEndpointFile()
    wsServer = null
    runnerProcess = null
    cachedTools = null
  }

  function start(args: RunnerBridgeStartOptions = {}): Promise<RunnerBridgeStatus> {
    if (starting || state.phase === 'starting' || state.phase === 'running' || state.phase === 'stopping') {
      return Promise.reject(new Error('Runner bridge is already running.'))
    }

    const generation = opGeneration

    const task =
      handshakePending && wsServer?.getStatus().connected
        ? syncConfig().then(() => {
            const status = getStatus()

            if (generation !== opGeneration) {
              throw new Error('Runner bridge recovery was superseded by stop or restart.')
            }

            if (status.phase !== 'running') {
              throw new Error(status.lastError || 'Runner bridge recovery was superseded.')
            }

            return status
          })
        : startRuntime(args)

    starting = task

    return task.finally(() => {
      if (starting === task) {
        starting = null
      }
    })
  }

  async function startRuntime(args: RunnerBridgeStartOptions): Promise<RunnerBridgeStatus> {
    const backendSession = args.backendSession

    if (!backendSession) {
      throw new Error('Runner bridge start requires a backend session.')
    }

    const gen = ++opGeneration
    hasBeenReady = false

    setState({
      lastError: null,
      phase: 'starting',
      startedAt: Date.now(),
      stoppedAt: null
    })

    // stopped / error 终态可能仍持有上一轮的 WS 服务、子进程与端点文件（断连后 Runner 会重连）；重建前先收尾，避免新旧服务争用同一管道路径。
    await rollback('restart')

    if (gen !== opGeneration) {
      throw new Error('Runner bridge start was superseded by stop.')
    }

    const processInstance = processFactory()
    runnerProcess = processInstance

    const offProcess = processInstance.onEvent(ev => {
      if (ev.type === 'error' && state.phase !== 'stopping') {
        handshakePending = false
        fail('error', ev.error)
      } else if (ev.type === 'exit') {
        handshakePending = false

        if (state.phase === 'running' || state.phase === 'stopped') {
          fail('stopped', new Error(`Runner exited (code=${ev.code}, signal=${ev.signal})`))
        } else if (state.phase === 'starting') {
          // 主动 stop 走 stopping，不在此记 error；仅启动期异常退出算 error。
          fail('error', new Error(`Runner exited during ${state.phase} (code=${ev.code}, signal=${ev.signal})`))
        }
      }
    })

    subUnsubFns.push(offProcess)

    const authToken = crypto.randomBytes(32).toString('hex')
    const endpoint = computeDesktopEndpoint(options.spiritagentHome)

    const wsInstance = wsServerFactory({
      authToken,
      log: options.log,
      onReverseRpc: reverseRpcFactory({ backendSession, log })
    })

    wsServer = wsInstance

    const offWs = wsInstance.onEvent((ev: RunnerWsEvent) => {
      if (ev.type === 'runner_ready') {
        void handleRunnerReady(ev).catch(error => {
          log(`[runner-bridge] handshake failed: ${errorMessage(error)}`)
        })
      } else if (ev.type === 'connected') {
        if (state.phase === 'stopping') {
          return
        }

        toolsGeneration++
        cachedTools = null
        handshakePending = false

        const wasRunning = state.phase === 'running'
        // 初次启动有就绪等待；重连尚未报告 ready 时可由 autoStart 重建。
        const awaitingStartup = starting !== null && state.phase === 'starting'
        setState({ phase: awaitingStartup ? 'starting' : 'stopped' })

        if (wasRunning) {
          publish({ reason: 'Runner connection awaits handshake.', type: 'stopped' })
        }
      } else if (ev.type === 'disconnected') {
        toolsGeneration++
        cachedTools = null
        handshakePending = false

        if (state.phase !== 'stopping' && state.phase !== 'error') {
          fail('stopped', new Error('Runner disconnected from WS server.'))
        }
      } else if (ev.type === 'error') {
        log(`[runner-bridge] ws server error: ${errorMessage(ev.error)}`)
      } else if (ev.type === 'notification' && ev.method !== 'runner_capabilities_changed') {
        // Client 不消费运行期能力通知（PROTOCOL「能力与进程代次」），该通知属预期事件。
        log(`[runner-bridge] unhandled ws server event: notification ${ev.method}`)
      }
    })

    subUnsubFns.push(offWs)

    let rollbackReason = 'ws-server-start'

    const assertCurrentStart = (): void => {
      if (gen !== opGeneration) {
        rollbackReason = 'start-superseded'
        throw new Error('Runner bridge start was superseded by stop.')
      }
    }

    let cancelReadyWait: (() => void) | undefined

    try {
      await wsInstance.start({ path: endpoint.path })
      log(`[runner-bridge] WS server listening on ${endpoint.transport} ${endpoint.path}`)
      await writeEndpointFile({ ...endpoint, token: authToken })
      assertCurrentStart()

      rollbackReason = 'process-start'
      const readyWait = waitUntilReady(args.readyTimeoutMs ?? READY_TIMEOUT_MS)
      cancelReadyWait = readyWait.cancel
      // spawn 失败可能先于 await 就绪，仍须消费等待期间的失败。
      void readyWait.promise.catch(() => {})
      await processInstance.start({ authToken, endpointPath: endpoint.path })
      assertCurrentStart()

      rollbackReason = 'ready-timeout'
      await readyWait.promise
      assertCurrentStart()
    } catch (error) {
      if (gen !== opGeneration) {
        throw error
      }

      // 握手耗尽时保留连接，配置保存、再次 ready 或 autoStart 可原位重试。
      if (
        handshakePending &&
        wsInstance.getStatus().connected &&
        processInstance.getStatus().running &&
        state.phase === 'stopped'
      ) {
        throw error
      }

      await rollback(rollbackReason)

      if (gen === opGeneration) {
        if (state.phase === 'error' && state.lastError === errorMessage(error)) {
          throw error
        }

        throw fail('error', error)
      }

      throw error
    } finally {
      cancelReadyWait?.()
    }

    return getStatus()
  }

  function waitUntilReady(timeoutMs: number): { cancel: () => void; promise: Promise<void> } {
    let cancel = () => {}

    const promise = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        cancel()
        reject(new Error(`Runner failed to become ready within ${timeoutMs}ms`))
      }, timeoutMs)

      const off = onEvent(event => {
        if (event.type === 'running' || event.type === 'runner_ready') {
          cancel()
          resolve()
        } else if (event.type === 'error' || event.type === 'stopping' || event.type === 'stopped') {
          cancel()
          reject(event.type === 'error' ? event.error : new Error(event.reason || 'Runner stopped before ready.'))
        }
      })

      cancel = () => {
        clearTimeout(timer)
        off()
      }
    })

    return { cancel, promise }
  }

  async function handleRunnerReady(payload: {
    capabilities?: null | RunnerCapabilities
    capabilities_health?: null | RunnerCapabilitiesHealth
    probe_failed?: boolean | null
    version?: null | string
  }): Promise<void> {
    const server = wsServer

    if (!server || !server.getStatus().connected || state.phase === 'stopping') {
      return
    }

    const generation = ++toolsGeneration
    log('[runner-bridge] runner_ready received')
    const wasRunning = state.phase === 'running'
    cachedTools = null
    handshakePending = true

    setState({
      capabilities: payload.capabilities ?? null,
      capabilitiesHealth: payload.capabilities_health ?? null,
      phase: 'starting',
      probeFailed: payload.probe_failed ?? null,
      runnerVersion: payload.version ?? null
    })

    if (wasRunning) {
      publish({ reason: 'Runner renewed its handshake.', type: 'stopped' })
    }

    await enqueueConfig(() => completeHandshake(server, generation))
  }

  async function completeHandshake(server: RunnerWsServer, generation: number): Promise<void> {
    for (let attempt = 0; attempt < HANDSHAKE_ATTEMPTS; attempt++) {
      if (isStale(server, generation)) {
        return
      }

      try {
        await pushConfig(server, true)

        if (isStale(server, generation)) {
          return
        }

        const tools = await requestTools(server)

        if (isStale(server, generation)) {
          return
        }

        cachedTools = tools
        toolsRefreshPending = false
        handshakePending = false
        setState({ lastError: null, phase: 'running' })
        publishReady(hasBeenReady ? 'runner_ready' : 'running')
        hasBeenReady = true

        return
      } catch (error) {
        if (isStale(server, generation)) {
          return
        }

        log(`[runner-bridge] handshake attempt ${attempt + 1} failed: ${errorMessage(error)}`)

        if (attempt + 1 === HANDSHAKE_ATTEMPTS) {
          throw fail('stopped', error)
        }

        await sleep(250 * (attempt + 1))
      }
    }
  }

  // 握手与运行中推送、清单读取共用队列；配置已成功但清单失败时，下次保存仍重试清单。
  function syncConfig(): Promise<void> {
    const server = wsServer
    const generation = toolsGeneration

    return enqueueConfig(async () => {
      if (!server || isStale(server, generation)) {
        return
      }

      if (handshakePending) {
        await completeHandshake(server, generation)

        return
      }

      if (state.phase !== 'running') {
        return
      }

      let pushed: boolean

      try {
        pushed = await pushConfig(server)
      } catch (error) {
        if (!isStale(server, generation)) {
          handshakePending = true
          fail('stopped', error)
        }

        throw error
      }

      if (isStale(server, generation) || state.phase !== 'running') {
        return
      }

      if (pushed) {
        toolsRefreshPending = true
      }

      if (!toolsRefreshPending) {
        return
      }

      const tools = await fetchTools(server)

      if (tools === null || isStale(server, generation) || state.phase !== 'running') {
        return
      }

      toolsRefreshPending = false

      if (JSON.stringify(tools) === JSON.stringify(cachedTools)) {
        return
      }

      cachedTools = tools
      log(`[runner-bridge] tool list changed after config update (${tools.length} tools)`)
      publishReady('runner_ready')
    })
  }

  async function requestTools(server: RunnerWsServer): Promise<Record<string, unknown>[]> {
    const result = await server.call<{ tools?: Record<string, unknown>[] }>('get_tools', {}, { timeoutMs: 10_000 })

    return result?.tools ?? []
  }

  async function fetchTools(server: RunnerWsServer): Promise<Record<string, unknown>[] | null> {
    try {
      const tools = await requestTools(server)
      log(`[runner-bridge] got ${tools.length} tools from runner`)

      if (tools.length > 0) {
        const names = tools.map(t => t?.name).filter(Boolean)

        log(`[runner-bridge] tool names: ${names.join(', ') || '(unparseable schemas)'}`)
      }

      return tools
    } catch (error: unknown) {
      const msg = errorMessage(error)
      log(`[runner-bridge] get_tools failed: ${msg}`)

      return null
    }
  }

  async function stop({ reason }: { reason?: string } = {}): Promise<{
    errors?: string[]
    noop?: boolean
    ok: boolean
  }> {
    if (state.phase === 'idle' || (state.phase === 'stopped' && !wsServer && !runnerProcess)) {
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
    toolsGeneration++
    handshakePending = false
    cachedTools = null
    setState({ phase: 'stopping' })
    publish({ reason, type: 'stopping' })
    log(`[runner-bridge] stop reason=${reason || 'unspecified'}`)

    const errors: string[] = []
    const tasks: Promise<unknown>[] = []

    if (wsServer) {
      tasks.push(
        wsServer.stop().catch(e => {
          errors.push(errorMessage(e))
        })
      )
    }

    if (runnerProcess) {
      const pid = runnerProcess.getStatus().pid

      tasks.push(
        runnerProcess.stop({ reason: reason || 'desktop-stop' }).then(
          result => {
            if (!result.ok) {
              errors.push(`Runner process pid=${pid} is still alive after SIGKILL.`)
            }
          },
          e => {
            errors.push(errorMessage(e))
          }
        )
      )
    }

    // 旧启动可能仍在等待就绪或回滚；收尾前不得让新实例复用这些资源。
    await Promise.all([...tasks, starting?.catch(() => {})])

    cleanupEndpointFile()
    detachSubs()
    wsServer = null
    runnerProcess = null
    cachedTools = null
    setState({ phase: 'stopped', stoppedAt: Date.now() })
    publish({ errors, reason, type: 'stopped' })

    return { errors, ok: errors.length === 0 }
  }

  async function dispatch<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    opts: { id?: number | string; timeoutMs?: number } = {}
  ): Promise<T> {
    if (!wsServer || !wsServer.getStatus()?.connected) {
      throw new RunnerNotConnectedError('Runner is not connected.')
    }

    if (
      method !== 'spiritagent.cancel' &&
      method !== 'spiritagent.call_result' &&
      (state.phase !== 'running' || handshakePending || cachedTools === null)
    ) {
      throw new RunnerNotConnectedError(`Runner is not ready: ${state.lastError || state.phase}.`)
    }

    return wsServer.call<T>(method, params, opts)
  }

  function getTools(): Record<string, unknown>[] {
    return state.phase === 'running' && wsServer?.getStatus().connected ? (cachedTools ?? []) : []
  }

  return {
    dispatch,
    getStatus,
    getTools,
    onEvent,
    start,
    stop,
    syncConfig
  }
}
