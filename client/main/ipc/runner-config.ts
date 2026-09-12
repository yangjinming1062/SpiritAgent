import { IPC, type RunnerConfigPatch } from '@ipc/contracts'
import type { IpcMain, WebContents } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

interface RunnerConfigIpcDeps {
  ipcMain: IpcMain
  /** 仅工作台等授权窗可读写完整本机配置；缺省时全部拒绝。 */
  isAuthorizedSender?: (event: { sender: WebContents }) => boolean
}

export function registerRunnerConfigIpc({ ipcMain, isAuthorizedSender }: RunnerConfigIpcDeps): void {
  const assertAuthorized = (event: { sender: WebContents }): void => {
    if (!isAuthorizedSender?.(event)) {
      throw new Error('runner config access is restricted to the workbench window')
    }
  }

  ipcMain.handle(IPC.invoke.runnerConfigRead, async event => {
    try {
      assertAuthorized(event)
      const content = JSON.stringify(store.read(), null, 2)

      return { content, ok: true }
    } catch (error: unknown) {
      const msg = errorMessage(error)

      return { error: msg, ok: false }
    }
  })

  ipcMain.handle(IPC.invoke.runnerConfigWrite, async (event, newContent: unknown) => {
    try {
      assertAuthorized(event)
    } catch (error: unknown) {
      return { error: errorMessage(error), ok: false }
    }

    if (typeof newContent !== 'string') {
      return { error: 'config content must be a string', ok: false }
    }

    let obj: unknown

    try {
      obj = JSON.parse(newContent)
    } catch (error: unknown) {
      const msg = errorMessage(error)

      return { error: msg, ok: false }
    }

    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { error: 'config root must be a JSON object', ok: false }
    }

    return store.write(obj as Record<string, unknown>)
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
