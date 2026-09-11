import fsp from 'node:fs/promises'

import {
  type DesktopBootProgress,
  IPC,
  type SpiritAgentApiRequest,
  type SpiritAgentConnection,
  type SpiritAgentConnectionPublic
} from '@ipc/contracts'
import type { IpcMain, WebContents } from 'electron'

import { dataUrlFromBuffer } from '../shared/mime'
import { HttpError, isUnauthorized, sendToSender } from '../shared/utils'

import type { AssetDiskCache } from './asset-disk-cache'
import type { ModelDiskCache } from './model-disk-cache'

// 鉴权失效广播：按结构化 status 判断，不解析错误文案。
function notifyAuthExpiredOn401(error: unknown, connection: SpiritAgentConnection, sender: WebContents): void {
  if (isUnauthorized(error) && connection.token) {
    sendToSender(sender, IPC.event.authSessionExpired)
  }
}

function isCompanionIdentityAsset(rawUrl: string, baseUrl: string): boolean {
  try {
    const { pathname } = new URL(rawUrl, baseUrl)

    return (
      pathname.includes('/api/companion/avatar/') ||
      pathname.includes('/api/companion/asset/') ||
      pathname.includes('/companion-avatars/')
    )
  } catch {
    return false
  }
}

async function runCachedAsset<T>(
  sender: WebContents,
  connection: SpiritAgentConnection,
  run: () => Promise<T>
): Promise<T> {
  try {
    return await run()
  } catch (error: unknown) {
    notifyAuthExpiredOn401(error, connection, sender)

    throw error
  }
}

// 对后端发起拉取并处理 401/错误包装——apiAsset / apiAssetBuffer 复用。
async function fetchFromBackend({
  rawUrl,
  connection,
  sender,
  fetchImpl,
  timeoutMs
}: {
  rawUrl: string
  connection: SpiritAgentConnection
  sender: WebContents
  fetchImpl?: typeof globalThis.fetch
  timeoutMs: number
}): Promise<Response> {
  const { pathname, search } = new URL(rawUrl, connection.baseUrl)
  const pathAndQuery = `${pathname}${search}`
  const caller = fetchImpl || globalThis.fetch

  const res = await caller(`${connection.baseUrl}${pathAndQuery}`, {
    headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
    signal: AbortSignal.timeout(timeoutMs)
  })

  if (!res.ok) {
    if (res.status === 401 && connection.token) {
      sendToSender(sender, IPC.event.authSessionExpired)
    }

    const text = await res.text().catch(() => '')

    throw new HttpError(res.status, `${res.status} ${pathname}: ${text || res.statusText}`)
  }

  return res
}

interface ConnectionIpcDeps {
  assetDiskCache?: null | AssetDiskCache
  defaultFetchTimeoutMs?: number
  ensureBackend: () => Promise<SpiritAgentConnection>
  fetchImpl?: typeof globalThis.fetch
  fetchJson: (
    url: string,
    token?: string,
    options?: { body?: unknown; method?: string; timeoutMs?: number }
  ) => Promise<unknown>
  getBootProgressState: () => DesktopBootProgress
  ipcMain: IpcMain
  mintWsTicket?: (baseUrl: string, token: string | null) => Promise<string | null>
  modelDiskCache?: null | ModelDiskCache
  resolvePathTimeoutMs: (path?: string, method?: string, fallbackMs?: number) => number
  resolveTimeoutMs: (timeoutMs?: number | string | null, fallbackMs?: number) => number
  setCachedWsUrl?: (wsUrl: string) => void
}

export function registerConnectionIpc({
  assetDiskCache,
  defaultFetchTimeoutMs = 15_000,
  ensureBackend,
  fetchImpl,
  fetchJson,
  getBootProgressState,
  ipcMain,
  mintWsTicket,
  modelDiskCache,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  setCachedWsUrl
}: ConnectionIpcDeps): void {
  ipcMain.handle(IPC.invoke.connection, async (): Promise<SpiritAgentConnectionPublic> => {
    const { token: _token, ...publicConnection } = await ensureBackend()

    return publicConnection
  })
  ipcMain.handle(IPC.invoke.gatewayWsUrl, async () => {
    const connection = await ensureBackend()

    if (mintWsTicket && connection.token) {
      const fresh = await mintWsTicket(connection.baseUrl, connection.token).catch(() => null)

      if (fresh) {
        const wsBase = connection.baseUrl.replace(/^http/, 'ws')
        const freshWsUrl = `${wsBase}/api/chat/ws?ticket=${fresh}`
        setCachedWsUrl?.(freshWsUrl)

        return freshWsUrl
      }
    }

    return connection.wsUrl
  })
  ipcMain.handle(IPC.invoke.bootProgressGet, async () => getBootProgressState())

  ipcMain.handle(IPC.invoke.api, async (_event, request: SpiritAgentApiRequest) => {
    const connection = await ensureBackend()
    const fallback = resolvePathTimeoutMs(request?.path, request?.method, defaultFetchTimeoutMs)
    const timeoutMs = resolveTimeoutMs(request?.timeoutMs, fallback)
    const url = `${connection.baseUrl}${request.path}`

    try {
      return await fetchJson(url, connection.token || undefined, {
        body: request?.body,
        method: request?.method,
        timeoutMs
      })
    } catch (error: unknown) {
      notifyAuthExpiredOn401(error, connection, _event.sender)

      throw error
    }
  })

  ipcMain.handle(
    IPC.invoke.apiAsset,
    async (_event, request?: { preferCache?: boolean; cacheOnly?: boolean; contentHash?: string; url?: string }) => {
      const raw = String(request?.url || '')

      if (!raw) {
        throw new Error('asset url is required')
      }

      if (request?.cacheOnly) {
        if (!assetDiskCache) {
          throw new Error('asset cache is unavailable')
        }

        const cached = await assetDiskCache.get(raw, request?.contentHash)

        if (!cached) {
          throw new Error('asset cache miss')
        }

        return cached.dataUrl
      }

      const connection = await ensureBackend()

      const identityCache = assetDiskCache

      if (identityCache && (request?.preferCache || isCompanionIdentityAsset(raw, connection.baseUrl))) {
        return await runCachedAsset(_event.sender, connection, async () => {
          const cached = await identityCache.ensureCached({
            preferCache: request?.preferCache,
            baseUrl: connection.baseUrl,
            contentHash: request?.contentHash,
            fetchFn: fetchImpl,
            rawUrl: raw,
            timeoutMs: defaultFetchTimeoutMs,
            token: connection.token || undefined
          })

          return cached.dataUrl
        })
      }

      const res = await fetchFromBackend({
        rawUrl: raw,
        connection,
        fetchImpl,
        sender: _event.sender,
        timeoutMs: defaultFetchTimeoutMs
      })

      const mime = res.headers.get('content-type') || 'application/octet-stream'

      return dataUrlFromBuffer(Buffer.from(await res.arrayBuffer()), mime)
    }
  )

  ipcMain.handle(
    IPC.invoke.apiAssetModelUrl,
    async (_event, request: { contentHash?: string; url: string }): Promise<string> => {
      const connection = await ensureBackend()
      const raw = String(request.url || '')

      if (!raw) {
        throw new Error('asset url is required')
      }

      if (!modelDiskCache) {
        throw new Error('apiAssetModelUrl requires the model disk cache; ensure spiritagentHome is configured')
      }

      try {
        const cached = await modelDiskCache.ensureCached({
          baseUrl: connection.baseUrl,
          contentHash: request.contentHash,
          fetchFn: fetchImpl,
          token: connection.token || undefined,
          url: raw
        })

        const normalizedPath = cached.filePath.replace(/\\/g, '/')

        return `spiritagent-media:///${normalizedPath}`
      } catch (error: unknown) {
        // 与兄弟 handler `apiAsset` / `apiAssetBuffer` 对齐:401 触发广播,
        // 渲染层 `onSessionExpired` 监听器可触发重新登录。
        notifyAuthExpiredOn401(error, connection, _event.sender)

        throw error
      }
    }
  )

  ipcMain.handle(
    IPC.invoke.apiAssetBuffer,
    async (_event, request?: { preferCache?: boolean; contentHash?: string; url?: string }) => {
      const connection = await ensureBackend()
      const raw = String(request?.url || '')

      if (!raw) {
        throw new Error('asset url is required')
      }

      const { pathname } = new URL(raw, connection.baseUrl)

      const isModel = pathname.includes('/model/file/') || pathname.includes('/companion-models/')

      if (modelDiskCache && isModel) {
        const cached = await modelDiskCache.ensureCached({
          baseUrl: connection.baseUrl,
          contentHash: request?.contentHash,
          fetchFn: fetchImpl,
          token: connection.token || undefined,
          url: raw
        })

        return await fsp.readFile(cached.filePath)
      }

      const identityCache = assetDiskCache

      if (identityCache && (request?.preferCache || isCompanionIdentityAsset(raw, connection.baseUrl))) {
        return await runCachedAsset(_event.sender, connection, async () => {
          const cached = await identityCache.ensureCached({
            preferCache: request?.preferCache,
            baseUrl: connection.baseUrl,
            contentHash: request?.contentHash,
            fetchFn: fetchImpl,
            rawUrl: raw,
            timeoutMs: defaultFetchTimeoutMs,
            token: connection.token || undefined
          })

          return cached.buffer
        })
      }

      const res = await fetchFromBackend({
        rawUrl: raw,
        connection,
        fetchImpl,
        sender: _event.sender,
        timeoutMs: defaultFetchTimeoutMs
      })

      return Buffer.from(await res.arrayBuffer())
    }
  )
}
