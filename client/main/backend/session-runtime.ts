import type { SafeStorage } from 'electron'

import type { BackendSessionPort, SessionSnapshotPort } from '../shared/backend-port'
import { buildClientContext } from '../shared/client-context'
import { errorMessage } from '../shared/utils'

export interface SessionRuntime {
  buildClientContext: () => ReturnType<typeof buildClientContext>
  ensureBackendSession: () => BackendSessionPort
  /** 当前会话 ID 与 token；任一缺失时为 null。 */
  getCurrentAuth: () => null | { sessionId: string; token: string }
  getSessionAfterRestore: () => Promise<null | SessionSnapshotPort>
}

export interface SessionRuntimeDeps {
  createSession: (options: {
    appVersion: string
    defaultBaseUrl: null | string
    fetchImpl: (url: string, options?: RequestInit) => Promise<Response>
    log: (chunk: string) => void
    safeStorage: SafeStorage
    userDataDir: string
  }) => BackendSessionPort
  desktopVersion: () => string
  fetchImpl: (url: string, options?: RequestInit) => Promise<Response>
  log: (chunk: string) => void
  onRestored: (snapshot: null | SessionSnapshotPort) => void
  readStoredBackendUrl: () => null | string
  safeStorage: SafeStorage
  spiritagentHome: string
  userDataDir: string
}

/** 懒创建并缓存后端会话；首次 ensure 触发 restore，结果经 `onRestored` 交回装配层。 */
export function createSessionRuntime(deps: SessionRuntimeDeps): SessionRuntime {
  let session: null | BackendSessionPort = null
  let restorePromise: Promise<void> = Promise.resolve()

  function ensureBackendSession(): BackendSessionPort {
    if (session) {
      return session
    }

    session = deps.createSession({
      appVersion: deps.desktopVersion(),
      defaultBaseUrl: deps.readStoredBackendUrl(),
      fetchImpl: deps.fetchImpl,
      log: deps.log,
      safeStorage: deps.safeStorage,
      userDataDir: deps.userDataDir
    })

    restorePromise = session.restoreSession().then(deps.onRestored, (error: unknown) => {
      deps.log(`[session] restore failed: ${errorMessage(error)}`)
      deps.onRestored(null)
    })

    return session
  }

  async function getSessionAfterRestore(): Promise<null | SessionSnapshotPort> {
    const current = ensureBackendSession()

    await restorePromise

    return current.getSession()
  }

  return {
    buildClientContext: () =>
      buildClientContext({
        desktopVersion: deps.desktopVersion(),
        spiritagentHome: deps.spiritagentHome
      }),
    ensureBackendSession,
    getCurrentAuth: () => {
      const current = ensureBackendSession()
      const sessionId = current.getSession()?.sessionId
      const token = current.getToken()

      return sessionId && token ? { sessionId, token } : null
    },
    getSessionAfterRestore
  }
}
