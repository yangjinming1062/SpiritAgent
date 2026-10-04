import { IPC, type IpcInvokeContract, type SpiritAgentApiRequest } from '@ipc/contracts'
import type { BrowserWindow, IpcMain, WebContents } from 'electron'

import { assertApiRequestAllowed } from '../security/api-allowlist'
import { assertGatewayHost } from '../security/ipc-trust'
import type { BackendConnection } from '../shared/backend-port'
import { dataUrlFromBuffer } from '../shared/mime'
import { httpErrorFromResponse, isUnauthorized, sendToSender } from '../shared/utils'

import { type AssetDiskCache, type CachedAsset, resolveBackendAssetUrl } from './asset-disk-cache'

export type GetCurrentAuth = () => null | { sessionId: string; token: string }

/** 取得后端连接并执行调用；401 且失败请求所用 token 仍是当前 token 时，通知发起窗口进入会话过期流程，错误照常抛出；换号前的迟到 401 不影响新会话。 */
export function createBackendCaller({
  ensureBackend,
  getCurrentAuth
}: {
  ensureBackend: () => Promise<BackendConnection>
  getCurrentAuth: GetCurrentAuth
}): <T>(sender: WebContents, call: (connection: BackendConnection) => Promise<T>) => Promise<T> {
  return async (sender, call) => {
    const connection = await ensureBackend()

    try {
      return await call(connection)
    } catch (error) {
      const current = getCurrentAuth()

      if (isUnauthorized(error) && connection.token && current?.token === connection.token) {
        sendToSender(sender, IPC.event.authSessionExpired, current.sessionId)
      }

      throw error
    }
  }
}

interface ConnectionIpcDeps {
  assetDiskCache: AssetDiskCache
  defaultFetchTimeoutMs: number
  ensureBackend: () => Promise<BackendConnection>
  fetchImpl?: typeof globalThis.fetch
  fetchJson: (
    url: string,
    token?: string,
    options?: { body?: unknown; method?: string; timeoutMs?: number }
  ) => Promise<unknown>
  getCurrentAuth: GetCurrentAuth
  getSelectedAccountId: () => string | null
  getMainWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  mintWsTicket: (baseUrl: string, token: string) => Promise<string>
  resolvePathTimeoutMs: (path?: string, method?: string, fallbackMs?: number) => number
}

type AssetRequest = Parameters<IpcInvokeContract['spiritagent:api:asset-buffer']>[0]

function assetUrl(request?: AssetRequest): string {
  const raw = String(request?.url || '')

  if (!raw) {
    throw new Error('asset url is required')
  }

  return raw
}

function isCompanionIdentityAsset(rawUrl: string, baseUrl: string): boolean {
  const { pathname } = new URL(rawUrl, baseUrl)

  return pathname.includes('/api/companion/asset/')
}

export function registerConnectionIpc({
  assetDiskCache,
  defaultFetchTimeoutMs,
  ensureBackend,
  fetchImpl = globalThis.fetch,
  fetchJson,
  getCurrentAuth,
  getSelectedAccountId,
  getMainWindow,
  ipcMain,
  mintWsTicket,
  resolvePathTimeoutMs
}: ConnectionIpcDeps): void {
  const callBackend = createBackendCaller({ ensureBackend, getCurrentAuth })

  async function readAsset(sender: WebContents, request?: AssetRequest): Promise<CachedAsset> {
    const raw = assetUrl(request)
    const accountId = getSelectedAccountId()
    const authSessionId = getCurrentAuth()?.sessionId

    const assertCurrentAuth = (): void => {
      if (getCurrentAuth()?.sessionId !== authSessionId || getSelectedAccountId() !== accountId) {
        throw new Error('Asset authentication changed while loading')
      }
    }

    if (request?.preferCache && accountId) {
      // 缓存命中无需等待后端就绪，离线启动也能恢复本机资产。
      const cached = await assetDiskCache.get(accountId, raw, request.contentHash)
      assertCurrentAuth()

      if (cached) {
        return cached
      }
    }

    return callBackend(sender, async connection => {
      assertCurrentAuth()

      if (request?.preferCache || isCompanionIdentityAsset(raw, connection.baseUrl)) {
        if (!accountId) {
          throw new Error('An account is required to cache assets')
        }

        const asset = await assetDiskCache.ensureCached({
          accountId,
          preferCache: request?.preferCache,
          baseUrl: connection.baseUrl,
          contentHash: request?.contentHash,
          fetchFn: fetchImpl,
          rawUrl: raw,
          timeoutMs: defaultFetchTimeoutMs,
          token: connection.token || undefined
        })

        assertCurrentAuth()

        return asset
      }

      const res = await fetchImpl(resolveBackendAssetUrl(raw, connection.baseUrl), {
        headers: connection.token ? { Authorization: `Bearer ${connection.token}` } : {},
        signal: AbortSignal.timeout(defaultFetchTimeoutMs)
      })

      if (!res.ok) {
        throw await httpErrorFromResponse(res, new URL(raw, connection.baseUrl).pathname)
      }

      const buffer = Buffer.from(await res.arrayBuffer())
      assertCurrentAuth()

      return {
        buffer,
        mime: res.headers.get('content-type') || 'application/octet-stream'
      }
    })
  }

  ipcMain.handle(IPC.invoke.gatewayWsUrl, async event => {
    assertGatewayHost(event.sender, getMainWindow(), 'gatewayWsUrl')

    const auth = getCurrentAuth()

    if (!auth) {
      throw new Error('An authenticated session is required for the gateway')
    }

    return callBackend(event.sender, async connection => {
      const assertCurrentAuth = (): void => {
        const current = getCurrentAuth()

        if (current?.sessionId !== auth.sessionId || current.token !== auth.token || connection.token !== auth.token) {
          throw new Error('Gateway authentication changed while issuing a ticket')
        }
      }

      assertCurrentAuth()
      const ticket = await mintWsTicket(connection.baseUrl, auth.token)
      assertCurrentAuth()

      return `${connection.baseUrl.replace(/^http/, 'ws')}/api/chat/ws?ticket=${encodeURIComponent(ticket)}`
    })
  })

  ipcMain.handle(IPC.invoke.api, async (event, request: SpiritAgentApiRequest) => {
    assertApiRequestAllowed(request?.path, request?.method)

    const auth = getCurrentAuth()

    if (request?.authSessionId !== undefined && request.authSessionId !== auth?.sessionId) {
      throw new Error('API authentication changed before dispatch')
    }

    return callBackend(event.sender, connection => {
      const current = getCurrentAuth()

      if (current?.sessionId !== auth?.sessionId || connection.token !== (current?.token ?? null)) {
        throw new Error('API authentication changed while resolving backend')
      }

      return fetchJson(`${connection.baseUrl}${request.path}`, connection.token || undefined, {
        body: request?.body,
        method: request?.method,
        timeoutMs: resolvePathTimeoutMs(request?.path, request?.method, defaultFetchTimeoutMs)
      })
    })
  })

  ipcMain.handle(
    IPC.invoke.apiAsset,
    async (event, request?: Parameters<IpcInvokeContract['spiritagent:api:asset']>[0]) => {
      const accountId = getSelectedAccountId()

      const asset = request?.cacheOnly
        ? accountId
          ? await assetDiskCache.get(accountId, assetUrl(request), request.contentHash)
          : null
        : await readAsset(event.sender, request)

      return asset && getSelectedAccountId() === accountId ? dataUrlFromBuffer(asset.buffer, asset.mime) : null
    }
  )
  ipcMain.handle(IPC.invoke.apiAssetBuffer, async (event, request?: AssetRequest) => {
    const asset = await readAsset(event.sender, request)

    return asset.buffer
  })
}
