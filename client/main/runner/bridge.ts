import crypto from 'node:crypto'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import type { BackendSessionLike } from '../shared/backend-port'
import { atomicWriteFile, errorMessage, RunnerNotConnectedError } from '../shared/utils'

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
  pushConfig: () => Promise<unknown> | void
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
  refreshTools: () => Promise<void>
  start: (args?: RunnerBridgeStartOptions) => Promise<RunnerBridgeStatus>
  stop: (options?: { reason?: string }) => Promise<{ errors?: string[]; noop?: boolean; ok: boolean }>
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

    const task = startRuntime(args)
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
      if (ev.type === 'exit') {
        if (state.phase === 'running') {
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
        void handleRunnerReady(ev)
      } else if (ev.type === 'disconnected') {
        toolsGeneration++
        cachedTools = null

        if (state.phase === 'running') {
          fail('stopped', new Error('Runner disconnected from WS server.'))
        }
      } else if (ev.type === 'error') {
        log(`[runner-bridge] ws server error: ${errorMessage(ev.error)}`)
      } else if (ev.type === 'notification') {
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

    try {
      await wsInstance.start({ path: endpoint.path })
      log(`[runner-bridge] WS server listening on ${endpoint.transport} ${endpoint.path}`)
      await writeEndpointFile({ ...endpoint, token: authToken })
      assertCurrentStart()

      rollbackReason = 'process-start'
      await processInstance.start({ authToken, endpointPath: endpoint.path })
      assertCurrentStart()

      rollbackReason = 'ready-timeout'
      await processInstance.waitForReady({ timeoutMs: args.readyTimeoutMs })
      assertCurrentStart()
    } catch (error) {
      await rollback(rollbackReason)

      if (gen === opGeneration) {
        throw fail('error', error)
      }

      throw error
    }

    return getStatus()
  }

  async function handleRunnerReady(payload: {
    capabilities?: null | RunnerCapabilities
    capabilities_health?: null | RunnerCapabilitiesHealth
    probe_failed?: boolean | null
    version?: null | string
  }): Promise<void> {
    const server = wsServer

    if (!server || !server.getStatus().connected || state.phase === 'stopping' || state.phase === 'error') {
      return
    }

    const generation = ++toolsGeneration
    const reconnecting = state.phase !== 'starting'
    log('[runner-bridge] runner_ready received')

    runnerProcess?.signalReady()

    setState({
      capabilities: payload.capabilities ?? null,
      capabilitiesHealth: payload.capabilities_health ?? null,
      probeFailed: payload.probe_failed ?? null,
      runnerVersion: payload.version ?? null
    })

    try {
      await pushConfig()
    } catch (err: unknown) {
      const msg = errorMessage(err)
      log(`[runner-bridge] config push failed: ${msg}`)
    }

    if (isStale(server, generation)) {
      return
    }

    const tools = await _fetchTools(server)

    if (isStale(server, generation)) {
      return
    }

    cachedTools = tools
    setState({ lastError: null, phase: 'running' })
    publishReady(reconnecting ? 'runner_ready' : 'running')
  }

  // 运行中配置变化后重新读取工具清单（终端等工具的说明随 Runner 当前配置生成）；有变化时按重连同样发布，由宿主重新同步。读取失败保留原清单，不能因一次查询失败撤销执行资格。
  async function refreshTools(): Promise<void> {
    const server = wsServer

    if (state.phase !== 'running' || !server || !server.getStatus().connected) {
      return
    }

    const generation = toolsGeneration
    let tools: Record<string, unknown>[]

    try {
      tools = await requestTools(server)
    } catch (error: unknown) {
      log(`[runner-bridge] get_tools refresh failed: ${errorMessage(error)}`)

      return
    }

    // 期间的断连、停止或新握手以它们自己的清单为准。
    if (
      isStale(server, generation) ||
      state.phase !== 'running' ||
      JSON.stringify(tools) === JSON.stringify(cachedTools)
    ) {
      return
    }

    cachedTools = tools
    log(`[runner-bridge] tool list changed after config update (${tools.length} tools)`)
    publishReady('runner_ready')
  }

  async function requestTools(server: RunnerWsServer): Promise<Record<string, unknown>[]> {
    const result = await server.call<{ tools?: Record<string, unknown>[] }>('get_tools', {}, { timeoutMs: 10_000 })

    return result?.tools ?? []
  }

  async function _fetchTools(server: RunnerWsServer): Promise<Record<string, unknown>[]> {
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

      return []
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
    refreshTools,
    start,
    stop
  }
}
