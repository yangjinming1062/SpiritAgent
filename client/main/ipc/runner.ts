import type { BrowserWindow, IpcMain } from 'electron'

import { type DesktopRunnerState, IPC, type RunnerCallOutcome, type RunnerCallRequest } from '@ipc/contracts'
import { sleep } from '@runtime'

import type { RunnerBridge, RunnerBridgeEvent, RunnerBridgeOptions, RunnerBridgeStatus } from '../runner/bridge'
import type { CreateRunnerProcessOptions, RunnerProcess } from '../runner/process'
import type { ReverseRpcOptions } from '../runner/reverse-rpc'
import type { CreateRunnerWsServerOptions, RunnerWsServer } from '../runner/rpc-ws'
import { assertGatewayHost } from '../security/ipc-trust'
import type { BackendSessionPort } from '../shared/backend-port'
import * as store from '../shared/lib/runner-config-store'
import { errorMessage, RunnerNotConnectedError, RunnerRpcError, sendToWindow } from '../shared/utils'

export interface RunnerHostOptions {
  createReverseRpc: (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>
  createRunnerBridge: (options: RunnerBridgeOptions) => RunnerBridge
  createRunnerProcess: (options: CreateRunnerProcessOptions) => RunnerProcess
  createRunnerWsServer: (options: CreateRunnerWsServerOptions) => RunnerWsServer
  ensureBackendSession: () => BackendSessionPort
  fileExists?: (p: string) => boolean
  getMainWindow: () => BrowserWindow | null | undefined
  rememberLog: (chunk: string) => void
  spiritagentHome?: null | string
  taggedLogger: (tag: string) => (msg: string) => void
}

export interface RunnerHost {
  autoStart: () => void
  autoStop: () => Promise<void>
  getBridge: () => null | RunnerBridge
  registerIpc: (ipcMain: IpcMain) => void
  restartForCurrentSession: () => Promise<void>
}

// 模型派发的设备调用上限：须长于 Runner 工具自身的最长时限（终端前台上限 600 秒），并短于 Backend 的等待上限（`ipc_future_timeout_seconds`），工具自己的超时结果才能带着已有输出先到达模型。
const TOOL_CALL_TIMEOUT_MS = 11 * 60_000

// Runner 已给出确定结局的拒绝：工具报错或执行前被拒（failed）、同一 call_id 的参数冲突、非法标识；其余错误（取消、超时、断连、他处持有、日志判定未知）都不能证明工具没有产生副作用。
const DEFINITE_FAILURE_DISPOSITIONS = new Set(['conflict', 'failed', 'invalid_call_id'])

// 这些节只由桌面消费；未知配置仍参与比较，新增工具配置不会被遗漏。
const DESKTOP_CONFIG_SECTIONS = new Set(['companion', 'language', 'shortcuts', 'sync', 'ui'])

function runnerCallOutcomeFromError(error: unknown): RunnerCallOutcome {
  if (error instanceof RunnerNotConnectedError) {
    return { status: 'not_executed' }
  }

  if (error instanceof RunnerRpcError && error.disposition && DEFINITE_FAILURE_DISPOSITIONS.has(error.disposition)) {
    return { error: error.message, status: 'failed' }
  }

  return { status: 'unknown' }
}

/** Runner 桥的唯一持有者：懒创建、登录自动启停，并向 IPC 与外部读者暴露窄接口。 */
export function createRunnerHost(options: RunnerHostOptions): RunnerHost {
  let runnerBridge: null | RunnerBridge = null
  let appliedConfig: null | string = null
  let configGeneration = 0
  // 在途的模型派发调用：call_id → 本次 Runner 请求 id，供按调用取消（spiritagent.cancel 的 req_id）。
  const inflightCalls = new Map<string, string>()
  let nextCallRequestId = 1

  function ensureRunnerBridge(): RunnerBridge {
    if (runnerBridge) {
      return runnerBridge
    }

    const pushConfig = async (server: RunnerWsServer, force = false): Promise<boolean> => {
      if (!runnerBridge) {
        return false
      }

      if (force) {
        configGeneration++
        appliedConfig = null
      }

      const config = store.read()

      const nextConfig = JSON.stringify(
        Object.fromEntries(Object.entries(config).filter(([key]) => !DESKTOP_CONFIG_SECTIONS.has(key)))
      )

      if (nextConfig === appliedConfig) {
        return false
      }

      const generation = configGeneration
      await server.call('spiritagent.config.update', { config }, { timeoutMs: 5000 })

      if (generation === configGeneration) {
        appliedConfig = nextConfig
      }

      return true
    }

    runnerBridge = options.createRunnerBridge({
      spiritagentHome: options.spiritagentHome,
      log: options.taggedLogger('[runner-bridge]'),
      processFactory: () =>
        options.createRunnerProcess({
          spiritagentHome: options.spiritagentHome,
          devPython: process.env.SPIRITAGENT_DESKTOP_PYTHON || null,
          executable: process.env.SPIRITAGENT_DESKTOP_RUNNER_EXECUTABLE || null,
          fileExists: options.fileExists,
          log: options.taggedLogger('[runner]'),
          repoRoot: process.env.SPIRITAGENT_DESKTOP_RUNNER_REPO_ROOT || null
        }),
      pushConfig,
      reverseRpcFactory: options.createReverseRpc,
      wsServerFactory: options.createRunnerWsServer
    })

    // 运行中保存配置后重新读取工具清单；握手时的推送由桥在读取清单前完成。
    store.setPushTarget(() => runnerBridge?.syncConfig())

    runnerBridge.onEvent((ev: RunnerBridgeEvent) => {
      if (ev.type === 'running' || ev.type === 'runner_ready') {
        sendToWindow(options.getMainWindow(), IPC.event.runnerStatus, { tools: ev.tools ?? [], type: ev.type })
      } else {
        configGeneration++
        appliedConfig = null
        sendToWindow(options.getMainWindow(), IPC.event.runnerStatus, { type: ev.type })
      }
    })

    return runnerBridge
  }

  async function startForCurrentSession(): Promise<{
    error?: string
    noop?: boolean
    ok: boolean
    reason?: string
    status?: RunnerBridgeStatus
  }> {
    const session = options.ensureBackendSession().getSession()

    if (!session?.hasToken) {
      return { ok: false, reason: 'no-session' }
    }

    const bridge = ensureRunnerBridge()
    const status = bridge.getStatus()

    if (status.phase === 'running' || status.phase === 'starting') {
      return { noop: true, ok: true, status }
    }

    try {
      const next = await bridge.start({
        backendSession: options.ensureBackendSession()
      })

      return { ok: true, status: next }
    } catch (error: unknown) {
      return { error: errorMessage(error), ok: false }
    }
  }

  async function stopCurrentSession({ reason }: { reason?: string } = {}): Promise<{
    errors?: string[]
    noop?: boolean
    ok: boolean
  }> {
    if (!runnerBridge) {
      return { noop: true, ok: true }
    }

    return runnerBridge.stop({ reason: reason || 'desktop-stop' })
  }

  function autoStart(): void {
    startForCurrentSession()
      .then(result => {
        if (!result?.ok && !result?.noop) {
          options.rememberLog(`[runner-bridge] auto-start failed: ${result.error || 'unknown'}`)
        }
      })
      .catch((error: unknown) => {
        options.rememberLog(`[runner-bridge] auto-start error: ${errorMessage(error)}`)
      })
  }

  async function autoStop(): Promise<void> {
    await stopCurrentSession({ reason: 'session-cleared' }).catch((error: unknown) => {
      options.rememberLog(`[runner-bridge] auto-stop failed: ${errorMessage(error)}`)
    })
  }

  async function restartForCurrentSession(): Promise<void> {
    try {
      await stopCurrentSession({ reason: 'account-switch' })
      const result = await startForCurrentSession()

      if (!result.ok && !result.noop) {
        options.rememberLog(
          `[runner-bridge] account switch start failed: ${result.error || result.reason || 'unknown'}`
        )
      }
    } catch (error) {
      options.rememberLog(`[runner-bridge] account switch restart failed: ${errorMessage(error)}`)
    }
  }

  function registerIpc(ipcMain: IpcMain): void {
    ipcMain.handle(IPC.invoke.runnerGetTools, async () => {
      const deadline = Date.now() + 6000

      while (Date.now() < deadline) {
        const bridge = runnerBridge

        if (bridge) {
          const tools = bridge.getTools()

          if (tools.length > 0) {
            return tools
          }

          const status = bridge.getStatus()

          if (
            status.phase === 'error' ||
            status.phase === 'stopped' ||
            status.phase === 'stopping' ||
            status.phase === 'idle'
          ) {
            return []
          }
        }

        await sleep(100)
      }

      return runnerBridge?.getTools() || []
    })

    // 渲染层直调（窗口查询、点击预演、情境快照），不带 call_id，不记调用日志。
    ipcMain.handle(IPC.invoke.runnerInvoke, async (event, name: string, args?: Record<string, unknown>) => {
      assertGatewayHost(event.sender, options.getMainWindow(), 'runner:invoke')

      if (typeof name !== 'string' || !name) {
        throw new Error('runner:invoke requires a non-empty tool name')
      }

      return ensureRunnerBridge().dispatch('execute_tool', { args: args ?? {}, name })
    })

    // 模型派发的设备调用：call_id 透传给 Runner 调用日志（PROTOCOL「调用日志与未知结果」），失败按结局分类返回。
    ipcMain.handle(
      IPC.invoke.runnerDispatchCall,
      async (event, request: RunnerCallRequest): Promise<RunnerCallOutcome> => {
        assertGatewayHost(event.sender, options.getMainWindow(), 'runner:dispatch-call')

        const name = request?.name
        const callId = request?.callId

        if (typeof name !== 'string' || !name || typeof callId !== 'string' || !callId) {
          throw new Error('runner:dispatch-call requires a tool name and call_id')
        }

        const bridge = ensureRunnerBridge()
        const requestId = `tool_${nextCallRequestId++}`
        const params: Record<string, unknown> = { args: request.args ?? {}, call_id: callId, name }
        inflightCalls.set(callId, requestId)

        try {
          const dispatchOptions = { id: requestId, timeoutMs: TOOL_CALL_TIMEOUT_MS }

          const result = request.skillScope
            ? await bridge.dispatch(
                'execute_scoped_tool',
                { ...params, skill_scope: request.skillScope },
                dispatchOptions
              )
            : await bridge.dispatch('execute_tool', params, dispatchOptions)

          return { result, status: 'completed' }
        } catch (error: unknown) {
          return runnerCallOutcomeFromError(error)
        } finally {
          if (inflightCalls.get(callId) === requestId) {
            inflightCalls.delete(callId)
          }
        }
      }
    )

    ipcMain.handle(
      IPC.invoke.runnerGetState,
      async (): Promise<DesktopRunnerState> => ({ phase: runnerBridge?.getStatus().phase ?? 'idle' })
    )

    // 只取消指定调用：后端中断回合时逐个下发 tool.cancel，其他会话与定时任务的在途调用不受影响。
    ipcMain.handle(IPC.invoke.runnerCancel, async (event, callId: string) => {
      assertGatewayHost(event.sender, options.getMainWindow(), 'runner:cancel')

      const requestId = typeof callId === 'string' ? inflightCalls.get(callId) : undefined
      const bridge = runnerBridge

      if (!requestId || !bridge) {
        return { noop: true, ok: true }
      }

      const status = bridge.getStatus()

      if (!status.wsServer?.connected) {
        return { noop: true, ok: true }
      }

      return bridge.dispatch('spiritagent.cancel', { req_id: requestId })
    })
  }

  return { autoStart, autoStop, getBridge: () => runnerBridge, registerIpc, restartForCurrentSession }
}
