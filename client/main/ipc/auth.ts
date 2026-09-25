import { type DesktopActivatePayload, IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import type { BackendSessionPort, SessionSnapshotPort } from '../shared/backend-port'
import { writeStoredBackendUrl } from '../shared/config'

interface AuthIpcDeps {
  autoStartBridge?: () => void
  autoStopBridge?: () => Promise<void>
  broadcastAuthChanged?: (session: null | SessionSnapshotPort) => Promise<void>
  buildClientContext?: () => { client_context?: unknown }
  ensureBackendSession: () => BackendSessionPort
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
  clearLocalAssetCaches?: () => Promise<void>
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

  async function publishChange(previous: null | SessionSnapshotPort, next: null | SessionSnapshotPort): Promise<void> {
    const changed = previous?.accountId !== next?.accountId

    if (next?.baseUrl) {
      await writeStoredBackendUrl(deps.spiritagentHome, next.baseUrl)
    }

    deps.resetBackendCache?.()

    if (changed) {
      await clearLocalAssetCaches?.().catch(() => {})
    }

    deps.rebuildTrayMenu?.()
    await deps.broadcastAuthChanged?.(next)

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
      const built = deps.buildClientContext?.() ?? {}
      const next = await session.switchAccount(accountId, { clientContext: built.client_context || null })

      if (next && previous?.sessionId !== next.sessionId) {
        await publishChange(previous, next)
      }

      return next
    })
  }

  function removeAccount(accountId: string): Promise<void> {
    return enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      await session.removeAccount(accountId)
      const next = session.getSession()

      if (previous?.accountId !== next?.accountId) {
        await publishChange(previous, next)
      } else {
        deps.rebuildTrayMenu?.()
      }
    })
  }

  ipcMain.handle(IPC.invoke.authActivate, (_event, payload: DesktopActivatePayload) =>
    enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const built = deps.buildClientContext?.() ?? {}
      const next = await session.activate({ ...(payload || {}), clientContext: built.client_context || null })

      if (next) {
        await publishChange(previous, next)
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
          await publishChange(previous, null)
        }

        throw error
      }
    })
  )

  ipcMain.handle(IPC.invoke.authLogout, (_event, expectedSessionId?: string) =>
    enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const result = await session.logout(expectedSessionId)

      if (!result.ignored && previous) {
        await publishChange(previous, null)
      }

      return result
    })
  )

  ipcMain.handle(IPC.invoke.authGetSession, () => deps.ensureBackendSession().getSession())

  return { removeAccount, switchAccount }
}
