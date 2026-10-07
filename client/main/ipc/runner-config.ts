import type { IpcMain, WebContents } from 'electron'

import { IPC, type RunnerConfigPatch } from '@ipc/contracts'

import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

interface RunnerConfigIpcDeps {
  ipcMain: IpcMain
  /** 仅工作台等授权窗可读写完整本机配置；缺省时全部拒绝。 */
  isAuthorizedSender?: (event: { sender: WebContents }) => boolean
}

const ACCESS_DENIED = { error: 'runner config access is restricted to the workbench window', ok: false } as const

export function registerRunnerConfigIpc({ ipcMain, isAuthorizedSender }: RunnerConfigIpcDeps): void {
  ipcMain.handle(IPC.invoke.runnerConfigRead, event => {
    if (!isAuthorizedSender?.(event)) {
      return ACCESS_DENIED
    }

    try {
      return { config: store.read(), ok: true }
    } catch (error: unknown) {
      return { error: errorMessage(error), ok: false }
    }
  })

  ipcMain.handle(IPC.invoke.runnerConfigPatch, async (event, patch?: RunnerConfigPatch) => {
    if (!isAuthorizedSender?.(event)) {
      return ACCESS_DENIED
    }

    if (!patch || !Array.isArray(patch.path) || patch.path.length === 0) {
      return { error: 'patch.path must be a non-empty array', ok: false }
    }

    const op = patch.op ?? 'set'

    if (op !== 'set' && op !== 'delete') {
      return { error: `unknown op: ${op}`, ok: false }
    }

    return store.patch(patch.path, { op, value: patch.value })
  })
}
