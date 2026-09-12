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
  // reset 时递增：在途 resolve 完成后若代数已变则不写回缓存。
  let generation = 0

  function resetBackendCache(): void {
    generation++
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

    const startGeneration = generation
    // IIFE 的 finally 需要引用自身判断归属，只能用 let 分两步赋值
    let mine: null | Promise<SpiritAgentConnection> = null

    mine = (async () => {
      try {
        if (cachedBackend) {
          const liveWindowState = deps.getWindowState()
          const wsBase = cachedBackend.baseUrl.replace(/^http/, 'ws')
          const token = deps.getAuthToken()
          const wsTicket = await deps.backendHttp.mintWsTicket(cachedBackend.baseUrl, token)

          if (startGeneration !== generation) {
            throw new Error('Backend connection was reset during resolve.')
          }

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

        if (startGeneration !== generation) {
          throw new Error('Backend connection was reset during resolve.')
        }

        deps.bootProgress.update({
          error: null,
          message: `Remote ${deps.appName} backend is ready`,
          phase: 'backend.ready',
          progress: 94,
          running: true
        })
        const wsBase = remote.baseUrl.replace(/^http/, 'ws')
        const wsTicket = await deps.backendHttp.mintWsTicket(remote.baseUrl, token)

        if (startGeneration !== generation) {
          throw new Error('Backend connection was reset during resolve.')
        }

        cachedBackend = {
          baseUrl: remote.baseUrl,
          token,
          wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`,
          ...deps.getWindowState()
        }

        return cachedBackend
      } finally {
        // 只清自己这条 pending，避免被 reset 后启动的并发 resolve 被误抹掉。
        if (mine && pendingBackend === mine) {
          pendingBackend = null
        }
      }
    })()

    pendingBackend = mine

    return mine
  }

  return { ensureBackend, resetBackendCache, setCachedWsUrl }
}
