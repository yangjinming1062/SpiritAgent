import { type DesktopRunnerState, IPC } from '@ipc/contracts'
import type { BrowserWindow, IpcMain } from 'electron'

import type { BackendSession } from '../backend/session'
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
import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

interface RunnerIpcDeps {
  createReverseRpc: (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>
  createRunnerBridge: (options: RunnerBridgeOptions) => RunnerBridge
  createRunnerProcess: (options: CreateRunnerProcessOptions) => RunnerProcess
  createRunnerWsServer: (options: CreateRunnerWsServerOptions) => RunnerWsServer
  spiritagentHome?: null | string
  ensureBackendSession: () => BackendSession
  fileExists?: (p: string) => boolean
  getMainWindow?: () => BrowserWindow | null | undefined
  rememberLog: (chunk: string) => void
  runnerBridge?: null | RunnerBridge
  taggedLogger: (tag: string) => (msg: string) => void
}

function ensureRunnerBridge(deps: RunnerIpcDeps): RunnerBridge {
  if (deps.runnerBridge) {
    return deps.runnerBridge
  }

  const pushConfig = () => {
    const bridge = deps.runnerBridge

    if (!bridge) {
      return Promise.resolve()
    }

    return bridge.dispatch('spiritagent.config.update', { config: store.read() })
  }

  deps.runnerBridge = deps.createRunnerBridge({
    spiritagentHome: deps.spiritagentHome,
    log: deps.taggedLogger('[runner-bridge]'),
    processFactory: (args?: RunnerBridgeStartOptions) =>
      deps.createRunnerProcess({
        spiritagentHome: deps.spiritagentHome,
        devPython: process.env.SPIRITAGENT_DESKTOP_PYTHON || null,
        executable: args?.executable || process.env.SPIRITAGENT_DESKTOP_RUNNER_EXECUTABLE || null,
        fileExists: deps.fileExists,
        log: deps.taggedLogger('[runner]'),
        repoRoot: process.env.SPIRITAGENT_DESKTOP_RUNNER_REPO_ROOT || null
      }),
    pushConfig,
    reverseRpcFactory: ({ backendSession, log: rpcLog }: ReverseRpcOptions) =>
      deps.createReverseRpc({
        backendSession,
        log: rpcLog || deps.taggedLogger('[runner-reverse]')
      }),
    wsServerFactory: ({ authToken, log: wsLog, onReverseRpc }: CreateRunnerWsServerOptions) =>
      deps.createRunnerWsServer({
        authToken,
        log: wsLog || deps.taggedLogger('[runner-ws]'),
        onReverseRpc
      })
  })

  store.setPushTarget(pushConfig)

  deps.runnerBridge.onEvent?.((ev: RunnerBridgeEvent) => {
    const win = deps.getMainWindow?.()

    if (win && !win.isDestroyed()) {
      win.webContents.send(IPC.event.runnerStatus, ev)
    }
  })

  return deps.runnerBridge
}

async function startRunnerBridgeForCurrentSession(
  deps: RunnerIpcDeps
): Promise<{ error?: string; noop?: boolean; ok: boolean; reason?: string; status?: RunnerBridgeStatus }> {
  const session = deps.ensureBackendSession().getSession()

  if (!session?.hasToken) {
    return { ok: false, reason: 'no-session' }
  }

  const bridge = ensureRunnerBridge(deps)
  const status = bridge.getStatus()

  if (status.phase === 'running' || status.phase === 'starting') {
    return { noop: true, ok: true, status }
  }

  try {
    const next = await bridge.start({
      backendSession: deps.ensureBackendSession(),
      readyTimeoutMs: 8_000
    })

    return { ok: true, status: next }
  } catch (error: unknown) {
    const msg = errorMessage(error)

    return { error: msg, ok: false }
  }
}

async function stopRunnerBridgeForCurrentSession(
  deps: RunnerIpcDeps,
  { reason }: { reason?: string } = {}
): Promise<{ errors?: string[]; noop?: boolean; ok: boolean }> {
  if (!deps.runnerBridge) {
    return { noop: true, ok: true }
  }

  return deps.runnerBridge.stop({ reason: reason || 'desktop-stop' })
}

export function autoStartBridge(deps: RunnerIpcDeps): void {
  startRunnerBridgeForCurrentSession(deps)
    .then(result => {
      if (!result?.ok && !result?.noop) {
        deps.rememberLog(`[runner-bridge] auto-start failed: ${result.error || 'unknown'}`)
      }
    })
    .catch((error: unknown) => {
      const msg = errorMessage(error)
      deps.rememberLog(`[runner-bridge] auto-start error: ${msg}`)
    })
}

export function autoStopBridge(deps: RunnerIpcDeps): void {
  stopRunnerBridgeForCurrentSession(deps, { reason: 'session-cleared' }).catch((error: unknown) => {
    const msg = errorMessage(error)
    deps.rememberLog(`[runner-bridge] auto-stop failed: ${msg}`)
  })
}

export function registerRunnerIpc({ deps, ipcMain }: { deps: RunnerIpcDeps; ipcMain?: IpcMain }): void {
  if (!ipcMain) {
    return
  }

  ipcMain.handle(IPC.invoke.runnerInvoke, async (_event, name: string, args?: Record<string, unknown>) => {
    if (typeof name !== 'string' || !name) {
      throw new Error('runner:invoke requires a non-empty tool name')
    }

    const bridge = ensureRunnerBridge(deps)

    return bridge.invoke(name, args && typeof args === 'object' ? args : {})
  })

  ipcMain.handle(IPC.invoke.runnerGetState, async (): Promise<DesktopRunnerState> => {
    const bridge = deps.runnerBridge

    if (!bridge) {
      return { phase: 'idle' }
    }

    const status = bridge.getStatus()

    return {
      capabilities: status.capabilities ?? null,
      capabilitiesHealth: status.capabilitiesHealth ?? null,
      lastError: status.lastError ?? null,
      phase: status.phase,
      probeFailed: status.probeFailed ?? null,
      runnerVersion: status.runnerVersion ?? null,
      startedAt: status.startedAt ?? null,
      stoppedAt: status.stoppedAt ?? null
    }
  })

  ipcMain.handle(IPC.invoke.runnerCancel, async () => {
    const bridge = deps.runnerBridge

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
