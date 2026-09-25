import { IPC, type SessionHistorySnapshot } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import type { BackendSessionPort } from '../shared/backend-port'

import type { SessionHistoryDiskCache } from './session-history-disk-cache'

interface SessionHistoryIpcDeps {
  ensureBackendSession: () => BackendSessionPort
  ipcMain: IpcMain
  sessionHistoryDiskCache: SessionHistoryDiskCache
}

export function registerSessionHistoryIpc({
  ensureBackendSession,
  ipcMain,
  sessionHistoryDiskCache
}: SessionHistoryIpcDeps): void {
  function currentAccountId(authSessionId: string): null | string {
    const current = ensureBackendSession().getSession()

    return current?.sessionId === authSessionId ? current.accountId : null
  }

  ipcMain.handle(IPC.invoke.sessionHistoryGet, async (_event, sessionId: string, authSessionId: string) => {
    const accountId = currentAccountId(authSessionId)

    if (!accountId) {
      return null
    }

    return sessionHistoryDiskCache.get(accountId, sessionId)
  })

  ipcMain.handle(
    IPC.invoke.sessionHistorySave,
    async (_event, sessionId: string, snapshot: SessionHistorySnapshot, authSessionId: string) => {
      const accountId = currentAccountId(authSessionId)

      if (!accountId) {
        return
      }

      await sessionHistoryDiskCache.save(accountId, sessionId, snapshot)
    }
  )

  ipcMain.handle(IPC.invoke.sessionHistoryRemove, async (_event, sessionId: string, authSessionId: string) => {
    const accountId = currentAccountId(authSessionId)

    if (!accountId) {
      return
    }

    await sessionHistoryDiskCache.remove(accountId, sessionId)
  })
}
