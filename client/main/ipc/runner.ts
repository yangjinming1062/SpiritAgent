import type { MemoryToolScope } from '@ipc/contracts'
import { type DesktopRunnerState, type DesktopRunnerStatusEvent, IPC } from '@ipc/contracts'
import type { BrowserWindow, IpcMain } from 'electron'

import type {
  RunnerBridge,
  RunnerBridgeEvent,
  RunnerBridgeOptions,
  RunnerBridgeStartOptions,
  RunnerBridgeStatus
} from '../runner/bridge'
import type { CreateRunnerProcessOptions, RunnerProcess } from '../runner/process'
import type { ReverseRpcOptions } from '../runner/reverse-rpc'
import type { CreateRunnerWsServerOptions, RunnerWsServer } from '../runner/rpc-ws'
import type { BackendSessionPort } from '../shared/backend-port'
import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

export interface RunnerHostOptions {
  createReverseRpc: (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>
  createRunnerBridge: (options: RunnerBridgeOptions) => RunnerBridge
  createRunnerProcess: (options: CreateRunnerProcessOptions) => RunnerProcess
  createRunnerWsServer: (options: CreateRunnerWsServerOptions) => RunnerWsServer
  ensureBackendSession: () => BackendSessionPort
  fileExists?: (p: string) => boolean
  getMainWindow?: () => BrowserWindow | null | undefined
  rememberLog: (chunk: string) => void
  spiritagentHome?: null | string
  taggedLogger: (tag: string) => (msg: string) => void
}

export interface RunnerHost {
  autoStart: () => void
  autoStop: () => void
  getBridge: () => null | RunnerBridge
  registerIpc: (ipcMain: IpcMain) => void
}

/** Runner 桥的唯一持有者：懒创建、登录自动启停，并向 IPC 与外部读者暴露窄接口。 */
export function createRunnerHost(options: RunnerHostOptions): RunnerHost {
  let runnerBridge: null | RunnerBridge = null

  function ensureRunnerBridge(): RunnerBridge {
    if (runnerBridge) {
      return runnerBridge
    }

    const pushConfig = () => {
      if (!runnerBridge) {
        return Promise.resolve()
      }

      return runnerBridge.dispatch('spiritagent.config.update', { config: store.read() })
    }

    runnerBridge = options.createRunnerBridge({
      spiritagentHome: options.spiritagentHome,
      log: options.taggedLogger('[runner-bridge]'),
      processFactory: (args?: RunnerBridgeStartOptions) =>
        options.createRunnerProcess({
          spiritagentHome: options.spiritagentHome,
          devPython: process.env.SPIRITAGENT_DESKTOP_PYTHON || null,
          executable: args?.executable || process.env.SPIRITAGENT_DESKTOP_RUNNER_EXECUTABLE || null,
          fileExists: options.fileExists,
          log: options.taggedLogger('[runner]'),
          repoRoot: process.env.SPIRITAGENT_DESKTOP_RUNNER_REPO_ROOT || null
        }),
      pushConfig,
      reverseRpcFactory: ({ backendSession, log: rpcLog }: ReverseRpcOptions) =>
        options.createReverseRpc({
          backendSession,
          log: rpcLog || options.taggedLogger('[runner-reverse]')
        }),
      wsServerFactory: ({ authToken, log: wsLog, onReverseRpc }: CreateRunnerWsServerOptions) =>
        options.createRunnerWsServer({
          authToken,
          log: wsLog || options.taggedLogger('[runner-ws]'),
          onReverseRpc
        })
    })

    store.setPushTarget(pushConfig)

    runnerBridge.onEvent?.((ev: RunnerBridgeEvent) => {
      const win = options.getMainWindow?.()

      if (win && !win.isDestroyed()) {
        const payload: DesktopRunnerStatusEvent = { type: ev.type }
        win.webContents.send(IPC.event.runnerStatus, payload)
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
        backendSession: options.ensureBackendSession(),
        readyTimeoutMs: 8_000
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

  function autoStop(): void {
    stopCurrentSession({ reason: 'session-cleared' }).catch((error: unknown) => {
      options.rememberLog(`[runner-bridge] auto-stop failed: ${errorMessage(error)}`)
    })
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

        await new Promise(resolve => setTimeout(resolve, 100))
      }

      return runnerBridge?.getTools() || []
    })

    ipcMain.handle(
      IPC.invoke.runnerInvoke,
      async (_event, name: string, args?: Record<string, unknown>, skillScope?: MemoryToolScope, callId?: string) => {
        if (typeof name !== 'string' || !name) {
          throw new Error('runner:invoke requires a non-empty tool name')
        }

        const bridge = ensureRunnerBridge()

        // call_id 透传给 runner 的调用日志（PROTOCOL §2.5）：runner 据此查询/认领已有执行，
        // 中断后凭记录区分「已执行」与「从未开始」，避免盲目重跑本机副作用。
        const invokeParams: Record<string, unknown> = { name, args: args ?? {} }

        if (callId) {
          invokeParams.call_id = callId
        }

        if (skillScope) {
          return bridge.dispatch('execute_scoped_tool', { ...invokeParams, skill_scope: skillScope })
        }

        return bridge.dispatch('execute_tool', invokeParams)
      }
    )

    ipcMain.handle(IPC.invoke.runnerGetState, async (): Promise<DesktopRunnerState> => {
      const bridge = runnerBridge

      if (!bridge) {
        return { phase: 'idle' }
      }

      return { phase: bridge.getStatus().phase }
    })

    ipcMain.handle(IPC.invoke.runnerCancel, async () => {
      const bridge = runnerBridge

      if (!bridge) {
        return { noop: true, ok: true }
      }

      const status = bridge.getStatus()

      if (status.phase !== 'running' || !status.wsServer?.connected) {
        return { noop: true, ok: true }
      }

      try {
        return await bridge.dispatch('spiritagent.cancel', {})
      } catch {
        return { noop: true, ok: false }
      }
    })
  }

  return { autoStart, autoStop, getBridge: () => runnerBridge, registerIpc }
}
