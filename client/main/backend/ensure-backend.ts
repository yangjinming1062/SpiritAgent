import type { SpiritAgentConnection } from '@ipc/contracts'

import type { BackendHttp } from './http'

interface EnsureBackendDeps {
  appName: string
  backendHttp: BackendHttp
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
  getWindowState: () => {
    isFullscreen: boolean
    nativeOverlayWidth: number
    windowButtonPosition: null | { x: number; y: number }
  }
}

function sameWindowButtonPosition(
  a: null | undefined | { x: number; y: number },
  b: null | undefined | { x: number; y: number }
): boolean {
  return !!a && !!b && a.x === b.x && a.y === b.y
}

/**
 * 后端连接缓存：token / 窗口态变化时失效重建；并发共享 in-flight Promise。
 * 从 entry.ts 拆出，避免入口同时持有连接策略与窗口装配。
 */
export function createEnsureBackend(deps: EnsureBackendDeps): {
  ensureBackend: () => Promise<SpiritAgentConnection>
  resetBackendCache: () => void
  setCachedWsUrl: (wsUrl: string) => void
} {
  let cachedBackend: SpiritAgentConnection | null = null
  let pendingBackend: Promise<SpiritAgentConnection> | null = null

  function resetBackendCache(): void {
    cachedBackend = null
    pendingBackend = null
  }

  function setCachedWsUrl(wsUrl: string): void {
    if (cachedBackend) {
      cachedBackend = { ...cachedBackend, wsUrl }
    }
  }

  async function ensureBackend(): Promise<SpiritAgentConnection> {
    if (cachedBackend) {
      const token = deps.getAuthToken()
      const tokenChanged = token !== cachedBackend.token
      const windowState = deps.getWindowState()

      if (
        !tokenChanged &&
        cachedBackend.isFullscreen === windowState.isFullscreen &&
        cachedBackend.nativeOverlayWidth === windowState.nativeOverlayWidth &&
        sameWindowButtonPosition(windowState.windowButtonPosition, cachedBackend.windowButtonPosition)
      ) {
        return cachedBackend
      }
    }

    // 并发调用共享一条 in-flight Promise,避免重复跑 boot phase
    if (pendingBackend) {
      return pendingBackend
    }

    pendingBackend = (async () => {
      try {
        if (cachedBackend) {
          const liveWindowState = deps.getWindowState()
          const wsBase = cachedBackend.baseUrl.replace(/^http/, 'ws')
          const token = deps.getAuthToken()
          const wsTicket = await deps.backendHttp.mintWsTicket(cachedBackend.baseUrl, token)
          cachedBackend = {
            ...cachedBackend,
            ...liveWindowState,
            token,
            wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`
          }

          return cachedBackend
        }

        deps.bootProgress.advance('backend.resolve', `Resolving ${deps.appName} backend`, 8)
        const remote = await deps.backendHttp.resolveRemoteBackend()

        if (!remote) {
          throw new Error(`No remote ${deps.appName} backend configured.`)
        }

        const token = deps.getAuthToken()
        deps.bootProgress.advance(
          'backend.remote',
          `Connecting to remote ${deps.appName} backend at ${remote.baseUrl}`,
          24
        )
        await deps.backendHttp.waitForSpiritAgent(remote.baseUrl, token || undefined)
        deps.bootProgress.update({
          error: null,
          message: `Remote ${deps.appName} backend is ready`,
          phase: 'backend.ready',
          progress: 94,
          running: true
        })
        const wsBase = remote.baseUrl.replace(/^http/, 'ws')
        const wsTicket = await deps.backendHttp.mintWsTicket(remote.baseUrl, token)
        cachedBackend = {
          baseUrl: remote.baseUrl,
          token,
          wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`,
          ...deps.getWindowState()
        }

        return cachedBackend
      } finally {
        pendingBackend = null
      }
    })()

    return pendingBackend
  }

  return { ensureBackend, resetBackendCache, setCachedWsUrl }
}
