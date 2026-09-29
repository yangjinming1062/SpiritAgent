import { IPC, type IpcInvokeContract } from '@ipc/contracts'
import type { IpcMain } from 'electron'

type RendererLogPayload = Parameters<IpcInvokeContract['spiritagent:log:emit']>[0]

function formatRendererLog(payload?: RendererLogPayload): string {
  const { args, level = 'info', scope = 'general' } = payload ?? {}

  const parts = (Array.isArray(args) ? args : [args]).map(a => {
    if (a == null) {
      return String(a)
    }

    if (typeof a === 'object' && 'message' in a && typeof (a as { message?: unknown }).message === 'string') {
      return (a as { message: string }).message
    }

    if (typeof a === 'object') {
      try {
        return JSON.stringify(a)
      } catch {
        return String(a)
      }
    }

    return String(a)
  })

  return `[renderer:${scope}] ${level}: ${parts.join(' ')}`
}

export function registerLogIpc({ ipcMain, log }: { ipcMain: IpcMain; log: (msg: string) => void }): void {
  ipcMain.handle(IPC.invoke.logEmit, (_event, payload?: RendererLogPayload) => {
    log(formatRendererLog(payload))
  })
}
