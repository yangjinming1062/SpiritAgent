import { IPC, type RunnerConfigPatch } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { errorMessage } from '../shared/utils'

export function registerRunnerConfigIpc({ ipcMain }: { ipcMain: IpcMain }): void {
  ipcMain.handle(IPC.invoke.runnerConfigRead, async () => {
    try {
      const content = JSON.stringify(store.read(), null, 2)

      return { content, ok: true }
    } catch (error: unknown) {
      const msg = errorMessage(error)

      return { error: msg, ok: false }
    }
  })

  ipcMain.handle(IPC.invoke.runnerConfigWrite, async (_event, newContent: unknown) => {
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

  ipcMain.handle(IPC.invoke.runnerConfigPatch, async (_event, patch?: RunnerConfigPatch) => {
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
