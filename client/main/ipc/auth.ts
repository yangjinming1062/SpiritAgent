import { type DesktopActivatePayload, type DesktopLogoutPayload, IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import type { BackendSessionPort, SessionSnapshotPort } from '../shared/backend-port'
import { writeStoredBackendUrl } from '../shared/config'
import { errorMessage } from '../shared/utils'

interface AuthIpcDeps {
  autoStartBridge?: () => void
  autoStopBridge?: () => Promise<void>
  broadcastAuthChanged?: (session: null | SessionSnapshotPort, clearAccountCache?: boolean) => Promise<void>
  buildClientContext?: () => { client_context?: unknown }
  ensureBackendSession: () => BackendSessionPort
  getSessionAfterRestore: () => Promise<null | SessionSnapshotPort>
  log: (message: string) => void
  rebuildTrayMenu?: () => void
  resetBackendCache?: () => void
  restartBridge?: () => Promise<void>
  spiritagentHome?: null | string
}

export function registerAuthIpc({
  clearLocalAssetCaches,
  deps,
  ipcMain
}: {
  clearLocalAssetCaches: () => Promise<void>
  deps: AuthIpcDeps
  ipcMain: IpcMain
}): {
  removeAccount: (accountId: string) => Promise<void>
  switchAccount: (accountId: string) => Promise<null | SessionSnapshotPort>
} {
  let actions: Promise<unknown> = Promise.resolve()

  function enqueueAction<T>(operation: () => Promise<T>): Promise<T> {
    const next = actions.then(operation, operation)
    actions = next.catch(() => {})

    return next
  }

  async function publishChange(
    previous: null | SessionSnapshotPort,
    next: null | SessionSnapshotPort,
    options: { cacheAction?: 'clear' | 'retain'; selectedAccountId?: null | string } = {}
  ): Promise<void> {
    const previousAccountId = previous?.accountId ?? options.selectedAccountId ?? null
    const changed = previousAccountId !== next?.accountId
    const clearAccountCache = options.cacheAction === 'clear' || (changed && options.cacheAction !== 'retain')

    if (next?.baseUrl) {
      await writeStoredBackendUrl(deps.spiritagentHome, next.baseUrl)
    }

    deps.resetBackendCache?.()

    if (clearAccountCache) {
      try {
        await clearLocalAssetCaches()
      } catch (error) {
        deps.log(`[auth] cache cleanup failed: ${errorMessage(error)}`)
      }
    }

    deps.rebuildTrayMenu?.()
    await deps.broadcastAuthChanged?.(next, clearAccountCache)

    if (!next) {
      await deps.autoStopBridge?.()
    } else if (changed && previous) {
      await deps.restartBridge?.()
    } else {
      deps.autoStartBridge?.()
    }
  }

  function switchAccount(accountId: string): Promise<null | SessionSnapshotPort> {
    return enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const selectedAccountId = session.getSelectedAccountId()
      const built = deps.buildClientContext?.() ?? {}
      const next = await session.switchAccount(accountId, { clientContext: built.client_context || null })

      if (next && previous?.sessionId !== next.sessionId) {
        await publishChange(previous, next, { selectedAccountId })
      }

      return next
    })
  }

  function removeAccount(accountId: string): Promise<void> {
    return enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const selectedAccountId = session.getSelectedAccountId()
      await session.removeAccount(accountId)
      const next = session.getSession()
      const selectedAccountAfter = session.getSelectedAccountId()

      if (previous?.accountId !== next?.accountId || selectedAccountId !== selectedAccountAfter) {
        await publishChange(previous, next, { selectedAccountId })
      } else {
        deps.rebuildTrayMenu?.()
      }
    })
  }

  ipcMain.handle(IPC.invoke.authActivate, (_event, payload: DesktopActivatePayload) =>
    enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const selectedAccountId = session.getSelectedAccountId()
      const built = deps.buildClientContext?.() ?? {}
      const next = await session.activate({ ...(payload || {}), clientContext: built.client_context || null })

      if (next) {
        await publishChange(previous, next, { selectedAccountId })
      }

      return next
    })
  )

  ipcMain.handle(IPC.invoke.authRefresh, () =>
    enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const built = deps.buildClientContext?.() ?? {}

      try {
        const next = await session.refresh({ clientContext: built.client_context || null })
        deps.resetBackendCache?.()
        deps.rebuildTrayMenu?.()
        await deps.broadcastAuthChanged?.(next)

        return next
      } catch (error) {
        if (previous && !session.getSession()) {
          await publishChange(previous, null, { cacheAction: 'retain' })
        }

        throw error
      }
    })
  )

  ipcMain.handle(IPC.invoke.authLogout, (_event, payload: DesktopLogoutPayload) =>
    enqueueAction(async () => {
      const reason = payload?.reason
      const expectedSessionId = payload?.expectedSessionId

      if (
        (reason !== 'user' && reason !== 'expired') ||
        (expectedSessionId !== undefined && typeof expectedSessionId !== 'string') ||
        (reason === 'expired' && !expectedSessionId)
      ) {
        throw new Error('Invalid logout request.')
      }

      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const result = await session.logout(expectedSessionId)

      if (!result.ignored || (reason === 'expired' && !session.getSession())) {
        await publishChange(previous, null, { cacheAction: reason === 'expired' ? 'retain' : 'clear' })
      }

      return result
    })
  )

  ipcMain.handle(IPC.invoke.authGetSession, () => deps.getSessionAfterRestore())

  return { removeAccount, switchAccount }
}
