import type { SafeStorage } from 'electron'

import type { BackendSessionPort, SessionSnapshotPort } from '../shared/backend-port'
import type { buildClientContext as BuildClientContextFn } from '../shared/client-context'

export interface SessionRuntime {
  buildClientContext: () => ReturnType<typeof BuildClientContextFn>
  ensureBackendSession: () => BackendSessionPort
  getSessionAfterRestore: () => Promise<null | SessionSnapshotPort>
  rewireAuthToken: () => void
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
  errorMessage: (error: unknown) => string
  fetchImpl: (url: string, options?: RequestInit) => Promise<Response>
  getTokenSetter: (fn: () => string | null) => void
  log: (chunk: string) => void
  onRestored: (snapshot: null | SessionSnapshotPort) => void
  readStoredBackendUrl: () => null | string
  safeStorage: SafeStorage
  spiritagentHome: string
  userDataDir: string
}

/**
 * 懒创建并缓存后端会话；首次 ensure 触发 restore，结果经 `onRestored` 交回装配层。
 * `rewireAuthToken` 把主进程动态 token 读取接到当前会话实例。
 */
export function createSessionRuntime(
  deps: SessionRuntimeDeps,
  buildClientContextFn: typeof BuildClientContextFn
): SessionRuntime {
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
      deps.log(`[session] restore failed: ${deps.errorMessage(error)}`)
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
      buildClientContextFn({
        desktopVersion: deps.desktopVersion(),
        spiritagentHome: deps.spiritagentHome
      }),
    ensureBackendSession,
    getSessionAfterRestore,
    rewireAuthToken: () => {
      deps.getTokenSetter(() => ensureBackendSession().getToken() ?? null)
    }
  }
}
