import { IPC, type RunnerConfigPatch } from '@ipc/contracts'
import type { IpcMain, WebContents } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

interface RunnerConfigIpcDeps {
  ipcMain: IpcMain
  /** 仅工作台等授权窗可读写完整本机配置；缺省时全部拒绝。 */
  isAuthorizedSender?: (event: { sender: WebContents }) => boolean
}

// IPC 支持循环引用、BigInt 等值，落盘配置仅接受 JSON 数据。
function isJsonValue(value: unknown, ancestors = new Set<object>()): boolean {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') {
    return true
  }

  if (typeof value === 'number') {
    return Number.isFinite(value)
  }

  if (typeof value !== 'object' || ancestors.has(value)) {
    return false
  }

  if (
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) !== Object.prototype &&
    Object.getPrototypeOf(value) !== null
  ) {
    return false
  }

  ancestors.add(value)
  const valid = Object.values(value).every(item => isJsonValue(item, ancestors))
  ancestors.delete(value)

  return valid
}

export function registerRunnerConfigIpc({ ipcMain, isAuthorizedSender }: RunnerConfigIpcDeps): void {
  const assertAuthorized = (event: { sender: WebContents }): void => {
    if (!isAuthorizedSender?.(event)) {
      throw new Error('runner config access is restricted to the workbench window')
    }
  }

  ipcMain.handle(IPC.invoke.runnerConfigRead, event => {
    try {
      assertAuthorized(event)

      return { config: store.read(), ok: true }
    } catch (error: unknown) {
      const msg = errorMessage(error)

      return { error: msg, ok: false }
    }
  })

  ipcMain.handle(IPC.invoke.runnerConfigWrite, async (event, config: unknown) => {
    try {
      assertAuthorized(event)
    } catch (error: unknown) {
      return { error: errorMessage(error), ok: false }
    }

    if (!config || typeof config !== 'object' || Array.isArray(config) || !isJsonValue(config)) {
      return { error: 'config must be a JSON object', ok: false }
    }

    return store.write(config)
  })

  ipcMain.handle(IPC.invoke.runnerConfigPatch, async (event, patch?: RunnerConfigPatch) => {
    try {
      assertAuthorized(event)
    } catch (error: unknown) {
      return { error: errorMessage(error), ok: false }
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
