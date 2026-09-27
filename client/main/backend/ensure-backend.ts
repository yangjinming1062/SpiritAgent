import type { BackendConnection } from '../shared/backend-port'

import type { BackendHttp } from './http'

interface EnsureBackendDeps {
  appName: string
  backendHttp: Pick<BackendHttp, 'resolveRemoteBackend' | 'waitForSpiritAgent'>
  bootProgress: {
    advance: (phase: string, message: string, progress: number) => void
    update: (payload: {
      error: null | string
      message: string
      phase: string
      progress: number
      running: boolean
    }) => void
  }
  getAuthToken: () => string | null
  getCurrentBaseUrl: () => string | null
}

export function createEnsureBackend(deps: EnsureBackendDeps): {
  ensureBackend: () => Promise<BackendConnection>
  resetBackendCache: () => void
} {
  let cachedBaseUrl: string | null = null
  let pendingBackend: Promise<string> | null = null
  let requestedBaseUrl: string | null | undefined
  let generation = 0

  function resetBackendCache(): void {
    generation++
    cachedBaseUrl = null
    pendingBackend = null
  }

  function assertCurrent(startGeneration: number): void {
    if (startGeneration !== generation || requestedBaseUrl !== deps.getCurrentBaseUrl()) {
      throw new Error('Backend connection was reset during resolve.')
    }
  }

  async function resolveBackend(startGeneration: number): Promise<string> {
    const token = deps.getAuthToken()
    const currentBaseUrl = deps.getCurrentBaseUrl()
    deps.bootProgress.advance('backend.resolve', `Resolving ${deps.appName} backend`, 8)
    const baseUrl = currentBaseUrl ?? (await deps.backendHttp.resolveRemoteBackend())?.baseUrl
    assertCurrent(startGeneration)

    if (!baseUrl) {
      throw new Error(`No remote ${deps.appName} backend configured.`)
    }

    deps.bootProgress.advance('backend.remote', `Connecting to remote ${deps.appName} backend at ${baseUrl}`, 24)
    await deps.backendHttp.waitForSpiritAgent(baseUrl, token || undefined)
    assertCurrent(startGeneration)

    deps.bootProgress.update({
      error: null,
      message: `Remote ${deps.appName} backend is ready`,
      phase: 'backend.ready',
      progress: 94,
      running: true
    })
    cachedBaseUrl = baseUrl

    return baseUrl
  }

  async function ensureBackend(): Promise<BackendConnection> {
    const currentBaseUrl = deps.getCurrentBaseUrl()

    if (requestedBaseUrl !== currentBaseUrl) {
      resetBackendCache()
      requestedBaseUrl = currentBaseUrl
    }

    // 凭据按请求读取；后端切换无需等待异步的账户变更广播。
    if (cachedBaseUrl) {
      return { baseUrl: cachedBaseUrl, token: deps.getAuthToken() }
    }

    const startGeneration = generation

    if (!pendingBackend) {
      const pending = resolveBackend(startGeneration).finally(() => {
        // reset 后的新请求不属于本次解析。
        if (pendingBackend === pending) {
          pendingBackend = null
        }
      })

      pendingBackend = pending
    }

    const baseUrl = await pendingBackend
    assertCurrent(startGeneration)

    return { baseUrl, token: deps.getAuthToken() }
  }

  return { ensureBackend, resetBackendCache }
}
