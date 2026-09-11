import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

function formatRendererLog(payload?: { args?: unknown[]; level?: string; scope?: string }): string {
  const { args, scope = 'general' } = payload ?? {}

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

  return `[renderer:${scope}] ${parts.join(' ')}`
}

export function registerLogIpc({ ipcMain, log }: { ipcMain: IpcMain; log: (msg: string) => void }): void {
  ipcMain.handle(IPC.invoke.logEmit, (_event, payload?: { args?: unknown[]; level?: string; scope?: string }) => {
    log(formatRendererLog(payload))
  })
}
