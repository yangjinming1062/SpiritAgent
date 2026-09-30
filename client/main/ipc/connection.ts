import { IPC, type IpcInvokeContract, type SpiritAgentApiRequest } from '@ipc/contracts'
import type { BrowserWindow, IpcMain, WebContents } from 'electron'

import { assertApiRequestAllowed } from '../security/api-allowlist'
import { isSenderWindow } from '../security/ipc-trust'
import type { BackendConnection } from '../shared/backend-port'
import { dataUrlFromBuffer } from '../shared/mime'
import { HttpError, isUnauthorized, sendToSender } from '../shared/utils'

import type { AssetDiskCache, CachedAsset } from './asset-disk-cache'

export type GetCurrentAuth = () => null | { sessionId: string; token: string }

/** 401 且失败请求所用 token 仍是当前 token 时，通知发起窗口进入会话过期流程；换号前的迟到 401 不影响新会话。 */
export function createAuthExpiryNotifier(
  getCurrentAuth: GetCurrentAuth
): (error: unknown, requestToken: null | string | undefined, sender: WebContents) => void {
  return (error, requestToken, sender) => {
    const current = getCurrentAuth()

    if (isUnauthorized(error) && requestToken && current?.token === requestToken) {
      sendToSender(sender, IPC.event.authSessionExpired, current.sessionId)
    }
  }
}

interface ConnectionIpcDeps {
  assetDiskCache: AssetDiskCache
  defaultFetchTimeoutMs?: number
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
  defaultFetchTimeoutMs = 15_000,
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
  const notifyAuthExpiredOn401 = createAuthExpiryNotifier(getCurrentAuth)

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

    const connection = await ensureBackend()
    assertCurrentAuth()

    try {
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

      // 绝对 URL 只取路径，凭据始终发往当前后端。
      const { pathname, search } = new URL(raw, connection.baseUrl)

      const res = await fetchImpl(`${connection.baseUrl}${pathname}${search}`, {
        headers: connection.token ? { Authorization: `Bearer ${connection.token}` } : {},
        signal: AbortSignal.timeout(defaultFetchTimeoutMs)
      })

      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new HttpError(res.status, `${res.status} ${pathname}: ${text || res.statusText}`)
      }

      const buffer = Buffer.from(await res.arrayBuffer())
      assertCurrentAuth()

      return {
        buffer,
        mime: res.headers.get('content-type') || 'application/octet-stream'
      }
    } catch (error) {
      notifyAuthExpiredOn401(error, connection.token, sender)

      throw error
    }
  }

  ipcMain.handle(IPC.invoke.gatewayWsUrl, async event => {
    if (!isSenderWindow(event.sender, getMainWindow())) {
      throw new Error('gatewayWsUrl is restricted to the gateway host window')
    }

    const auth = getCurrentAuth()

    if (!auth) {
      throw new Error('An authenticated session is required for the gateway')
    }

    const connection = await ensureBackend()

    const assertCurrentAuth = (): void => {
      const current = getCurrentAuth()

      if (current?.sessionId !== auth.sessionId || current.token !== auth.token || connection.token !== auth.token) {
        throw new Error('Gateway authentication changed while issuing a ticket')
      }
    }

    try {
      assertCurrentAuth()
      const ticket = await mintWsTicket(connection.baseUrl, auth.token)
      assertCurrentAuth()

      return `${connection.baseUrl.replace(/^http/, 'ws')}/api/chat/ws?ticket=${encodeURIComponent(ticket)}`
    } catch (error) {
      notifyAuthExpiredOn401(error, connection.token, event.sender)

      throw error
    }
  })

  ipcMain.handle(IPC.invoke.api, async (event, request: SpiritAgentApiRequest) => {
    assertApiRequestAllowed(request?.path, request?.method)

    const connection = await ensureBackend()
    const timeoutMs = resolvePathTimeoutMs(request?.path, request?.method, defaultFetchTimeoutMs)
    const url = `${connection.baseUrl}${request.path}`

    try {
      return await fetchJson(url, connection.token || undefined, {
        body: request?.body,
        method: request?.method,
        timeoutMs
      })
    } catch (error: unknown) {
      notifyAuthExpiredOn401(error, connection.token, event.sender)

      throw error
    }
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
