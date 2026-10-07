import type { IpcMain, WebContents } from 'electron'

import {
  type DesktopActivatePayload,
  type DesktopAuthBroadcast,
  type DesktopAuthSnapshot,
  type DesktopLogoutPayload,
  IPC
} from '@ipc/contracts'

import type { BackendSessionPort, SessionSnapshotPort } from '../shared/backend-port'
import { writeStoredBackendUrl } from '../shared/config'
import { broadcastToAllWindows, createSerialQueue, errorMessage } from '../shared/utils'

interface AuthBroadcasterDeps {
  autoStartBridge: () => void
  configSync: { handleAuthUserChanged: (accountId: null | string) => Promise<void> }
  ensureBackendSession: () => BackendSessionPort
  log: (message: string) => void
  rebuildTrayMenu: () => void
  resetPlaybackClaims: () => void
}

export interface AuthBroadcaster {
  /** 首次调用会懒建会话并触发凭据恢复；已有 token 时自动启动 Runner。 */
  autoStartBridgeIfSignedIn: () => void
  /** 先重建托盘菜单（账户列表随之更新），再向全部窗口广播；已被其他会话取代时不广播。 */
  broadcastAuthChanged: (snapshot: null | SessionSnapshotPort, removedAccountId?: string) => Promise<void>
  /** 会话恢复回调：直接广播，不进鉴权操作队列；广播后仍是当前会话才自动启动 Runner。 */
  onSessionRestored: (snapshot: null | SessionSnapshotPort) => void
}

export function createAuthBroadcaster(deps: AuthBroadcasterDeps): AuthBroadcaster {
  let playbackClaimAccountId: null | string = null

  function isCurrentSession(snapshot: SessionSnapshotPort): boolean {
    return deps.ensureBackendSession().getSession()?.sessionId === snapshot.sessionId
  }

  async function broadcastAuthChanged(snapshot: null | SessionSnapshotPort, removedAccountId?: string): Promise<void> {
    deps.rebuildTrayMenu()

    const live = snapshot?.hasToken ? snapshot : null

    const authSnapshot: DesktopAuthSnapshot | null = live && {
      accountId: live.accountId,
      baseUrl: live.baseUrl,
      hasToken: live.hasToken,
      sessionId: live.sessionId,
      tokenExpiresAt: live.tokenExpiresAt,
      user: live.user?.username ? { username: live.user.username } : null
    }

    const payload: DesktopAuthBroadcast = {
      authenticated: live !== null,
      removedAccountId,
      snapshot: authSnapshot
    }

    // 身份变化时配置同步丢弃上个身份未上云的待写；登录或换号后水合新身份。
    const nextAccountId = live?.accountId ?? null

    if (playbackClaimAccountId !== nextAccountId) {
      deps.resetPlaybackClaims()
      playbackClaimAccountId = nextAccountId
    }

    await deps.configSync.handleAuthUserChanged(nextAccountId)

    if (snapshot && !isCurrentSession(snapshot)) {
      return
    }

    broadcastToAllWindows(IPC.event.authChanged, payload)
  }

  function onSessionRestored(snapshot: null | SessionSnapshotPort): void {
    if (!snapshot || !isCurrentSession(snapshot)) {
      deps.rebuildTrayMenu()

      return
    }

    void broadcastAuthChanged(snapshot)
      .then(() => {
        if (isCurrentSession(snapshot)) {
          deps.autoStartBridge()
        }
      })
      .catch(error => deps.log(`[session] restored auth broadcast failed: ${errorMessage(error)}`))
  }

  function autoStartBridgeIfSignedIn(): void {
    if (deps.ensureBackendSession().getSession()?.hasToken) {
      deps.autoStartBridge()
    }
  }

  return { autoStartBridgeIfSignedIn, broadcastAuthChanged, onSessionRestored }
}

interface AuthIpcDeps {
  autoStartBridge: () => void
  autoStopBridge: () => Promise<void>
  broadcastAuthChanged: (session: null | SessionSnapshotPort, removedAccountId?: string) => Promise<void>
  buildClientContext: () => { client_context?: unknown }
  ensureBackendSession: () => BackendSessionPort
  getSessionAfterRestore: () => Promise<null | SessionSnapshotPort>
  log: (message: string) => void
  onAccountIdentityChanged: () => Promise<void>
  resetBackendCache: () => void
  restartBridge: () => Promise<void>
  spiritagentHome: string
}

export function registerAuthIpc({
  clearAccountCaches,
  deps,
  ipcMain
}: {
  clearAccountCaches: (accountId: string) => Promise<void>
  deps: AuthIpcDeps
  ipcMain: IpcMain
}): {
  removeAccount: (accountId: string) => Promise<void>
  switchAccount: (accountId: string) => Promise<null | SessionSnapshotPort>
} {
  const enqueueAction = createSerialQueue()

  async function publishChange(
    previous: null | SessionSnapshotPort,
    next: null | SessionSnapshotPort,
    options: { removedAccountId?: string; selectedAccountId?: null | string } = {}
  ): Promise<void> {
    const previousAccountId = previous?.accountId ?? options.selectedAccountId ?? null
    const changed = previousAccountId !== (next?.accountId ?? null)

    if (next?.baseUrl && !(await writeStoredBackendUrl(deps.spiritagentHome, next.baseUrl))) {
      deps.log('[auth] saving backend URL to desktop-config.json failed')
    }

    deps.resetBackendCache()

    try {
      await deps.broadcastAuthChanged(next, options.removedAccountId)
    } finally {
      if (changed) {
        try {
          await deps.onAccountIdentityChanged()
        } catch (error) {
          deps.log(`[auth] returning to sprite after account change failed: ${errorMessage(error)}`)
        }
      }
    }

    if (!next) {
      await deps.autoStopBridge()
    } else if (changed && previous) {
      await deps.restartBridge()
    } else {
      deps.autoStartBridge()
    }
  }

  function switchAccount(accountId: string): Promise<null | SessionSnapshotPort> {
    return enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const selectedAccountId = session.getSelectedAccountId()
      const built = deps.buildClientContext()
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

      try {
        await clearAccountCaches(accountId)
      } finally {
        // 凭据已移除：即使缓存删除失败，窗口和 Runner 也必须离开旧账户。
        if (previous?.accountId !== next?.accountId || selectedAccountId !== selectedAccountAfter) {
          await publishChange(previous, next, { removedAccountId: accountId, selectedAccountId })
        } else {
          await deps.broadcastAuthChanged(next, accountId)
        }
      }
    })
  }

  ipcMain.handle(IPC.invoke.authActivate, (_event, payload: DesktopActivatePayload) =>
    enqueueAction(async () => {
      const session = deps.ensureBackendSession()
      const previous = session.getSession()
      const selectedAccountId = session.getSelectedAccountId()
      const built = deps.buildClientContext()
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
      const built = deps.buildClientContext()

      try {
        const next = await session.refresh({ clientContext: built.client_context || null })
        deps.resetBackendCache()
        await deps.broadcastAuthChanged(next)

        return next
      } catch (error) {
        if (previous && !session.getSession()) {
          await publishChange(previous, null)
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
        await publishChange(previous, null)
      }

      return result
    })
  )

  ipcMain.handle(IPC.invoke.authGetSession, () => deps.getSessionAfterRestore())

  return { removeAccount, switchAccount }
}

interface DesktopAccountIpcDeps {
  ensureBackendSession: () => BackendSessionPort
  /** 桌面模式离开与窗口切换；presentation 由入口晚绑定，读取得在调用时发生。 */
  onLeavingDesktop: () => Promise<void>
  /** 打开主窗口并触发与托盘「打开」一致的激活流程。 */
  openMainWindow: () => void
  switchAccount: (accountId: string) => Promise<null | SessionSnapshotPort>
  isDesktopSender: (sender: WebContents) => boolean
  quitApp: () => void
}

export function registerDesktopAccountIpc({ deps, ipcMain }: { deps: DesktopAccountIpcDeps; ipcMain: IpcMain }): void {
  const assertDesktopAccountSender = (sender: WebContents): void => {
    if (!deps.isDesktopSender(sender)) {
      throw new Error('仅桌面入口允许此操作。')
    }
  }

  ipcMain.handle(IPC.invoke.desktopAccounts, event => {
    assertDesktopAccountSender(event.sender)

    return deps.ensureBackendSession().listAccounts()
  })

  ipcMain.handle(IPC.invoke.desktopSwitchAccount, async (event, id: unknown) => {
    assertDesktopAccountSender(event.sender)

    if (typeof id !== 'string') {
      throw new Error('无效账户。')
    }

    await deps.onLeavingDesktop()
    await deps.switchAccount(id)
  })

  ipcMain.handle(IPC.invoke.desktopAddAccount, async event => {
    assertDesktopAccountSender(event.sender)
    await deps.onLeavingDesktop()
    deps.openMainWindow()
  })

  ipcMain.handle(IPC.invoke.desktopQuit, event => {
    assertDesktopAccountSender(event.sender)
    deps.quitApp()
  })
}
