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
  function currentUserId(): null | number {
    const id = ensureBackendSession().getSession()?.user?.id

    return typeof id === 'number' && Number.isFinite(id) && id > 0 ? id : null
  }

  ipcMain.handle(IPC.invoke.sessionHistoryGet, async (_event, sessionId: string) => {
    const userId = currentUserId()

    if (!userId) {
      return null
    }

    return sessionHistoryDiskCache.get(userId, sessionId)
  })

  ipcMain.handle(IPC.invoke.sessionHistorySave, async (_event, sessionId: string, snapshot: SessionHistorySnapshot) => {
    const userId = currentUserId()

    if (!userId) {
      return
    }

    await sessionHistoryDiskCache.save(userId, sessionId, snapshot)
  })

  ipcMain.handle(IPC.invoke.sessionHistoryRemove, async (_event, sessionId: string) => {
    const userId = currentUserId()

    if (!userId) {
      return
    }

    await sessionHistoryDiskCache.remove(userId, sessionId)
  })
}
