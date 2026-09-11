import { type DesktopActivatePayload, IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import type { BackendSession, SessionSnapshot } from '../backend/session'
import { writeStoredBackendUrl } from '../shared/config'

interface AuthIpcDeps {
  autoStartBridge?: () => void
  autoStopBridge?: () => void
  broadcastAuthChanged?: (session: null | SessionSnapshot) => void
  buildClientContext?: () => { client_context?: unknown }
  ensureBackendSession: () => BackendSession
  rebuildTrayMenu?: () => void
  resetBackendCache?: () => void
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
}): void {
  ipcMain.handle(IPC.invoke.authActivate, async (_event, payload: DesktopActivatePayload) => {
    const session = deps.ensureBackendSession()
    const built = deps.buildClientContext?.() ?? {}

    const enriched = {
      ...(payload || {}),
      clientContext: built.client_context || null
    }

    const result = await session.activate(enriched)
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    deps.broadcastAuthChanged?.(session.getSession())

    if (result) {
      deps.autoStartBridge?.()

      if (result.baseUrl) {
        await writeStoredBackendUrl(deps.spiritagentHome, result.baseUrl)
      }
    }

    return result
  })

  ipcMain.handle(IPC.invoke.authRefresh, async () => {
    const session = deps.ensureBackendSession()
    const built = deps.buildClientContext?.() ?? {}
    const enriched = { clientContext: built.client_context || null }

    const result = await session.refresh(enriched)
    deps.resetBackendCache?.()
    deps.broadcastAuthChanged?.(session.getSession())

    return result
  })

  ipcMain.handle(IPC.invoke.authLogout, async () => {
    const session = deps.ensureBackendSession()
    const result = await session.logout()
    await clearLocalAssetCaches?.().catch(() => {})
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    deps.broadcastAuthChanged?.(session.getSession())
    deps.autoStopBridge?.()

    return result
  })

  ipcMain.handle(IPC.invoke.authGetSession, async () => {
    return deps.ensureBackendSession().getSession()
  })
}
