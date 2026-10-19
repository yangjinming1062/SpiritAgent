import type { IpcMain, WebContents } from 'electron'

import { type DesktopApiOperationKey, IPC, type IpcInvokeContract, type SpiritAgentApiRequest } from '@ipc/contracts'

import { type ApiArguments, apiErrorFromResponse } from '../../../shared/api'
import { ApiError } from '../../../shared/errors'
import { isRecord } from '../../../shared/gateway'
import type { DesktopGateway } from '../backend/gateway'
import type { BackendHttp } from '../backend/http'
import { assertApiOperationAllowed } from '../security/api-allowlist'
import type { BackendConnection } from '../shared/backend-port'
import { dataUrlFromBuffer } from '../shared/mime'
import { sendToSender } from '../shared/utils'

import { registerApiResult } from './api-result'
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

      if (
        error instanceof ApiError &&
        error.isLoginExpired &&
        connection.token &&
        current?.token === connection.token
      ) {
        sendToSender(sender, IPC.event.authSessionExpired, current.sessionId)
      }

      throw error
    }
  }
}

interface BackendAssetReaderDeps {
  assetDiskCache: AssetDiskCache
  assetFetchTimeoutMs: number
  ensureBackend: () => Promise<BackendConnection>
  fetchImpl?: typeof globalThis.fetch
  getCurrentAuth: GetCurrentAuth
  getSelectedAccountId: () => string | null
}

interface ConnectionIpcDeps {
  assetDiskCache: AssetDiskCache
  ensureBackend: () => Promise<BackendConnection>
  invokeApi: BackendHttp['invoke']
  getGateway: () => DesktopGateway | null
  getCurrentAuth: GetCurrentAuth
  getSelectedAccountId: () => string | null
  ipcMain: IpcMain
  readAsset: ReturnType<typeof createBackendAssetReader>
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

export function createBackendAssetReader({
  assetDiskCache,
  assetFetchTimeoutMs,
  ensureBackend,
  fetchImpl = globalThis.fetch,
  getCurrentAuth,
  getSelectedAccountId
}: BackendAssetReaderDeps): (sender: WebContents, request?: AssetRequest) => Promise<CachedAsset> {
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
          timeoutMs: assetFetchTimeoutMs,
          token: connection.token || undefined
        })

        assertCurrentAuth()

        return asset
      }

      const res = await fetchImpl(resolveBackendAssetUrl(raw, connection.baseUrl), {
        headers: connection.token ? { Authorization: `Bearer ${connection.token}` } : {},
        signal: AbortSignal.timeout(assetFetchTimeoutMs)
      })

      if (!res.ok) {
        throw await apiErrorFromResponse(res)
      }

      const buffer = Buffer.from(await res.arrayBuffer())
      assertCurrentAuth()

      return {
        buffer,
        mime: res.headers.get('content-type') || 'application/octet-stream'
      }
    })
  }

  return readAsset
}

export function registerConnectionIpc({
  assetDiskCache,
  ensureBackend,
  invokeApi,
  getGateway,
  getCurrentAuth,
  getSelectedAccountId,
  ipcMain,
  readAsset
}: ConnectionIpcDeps): void {
  const callBackend = createBackendCaller({ ensureBackend, getCurrentAuth })

  registerApiResult(ipcMain, IPC.invoke.api, async (event, request: SpiritAgentApiRequest) => {
    assertApiOperationAllowed(request?.operation)

    const auth = getCurrentAuth()

    if (!auth || request.authSessionId !== auth.sessionId || !isRecord(request.input)) {
      throw new Error('API authentication changed before dispatch')
    }

    return callBackend(event.sender, connection => {
      const current = getCurrentAuth()

      if (current?.sessionId !== auth?.sessionId || connection.token !== (current?.token ?? null)) {
        throw new Error('API authentication changed while resolving backend')
      }

      if (request.operation === 'sessions.snapshot') {
        const inputPath: unknown = request.input.path
        const sessionId = isRecord(inputPath) ? inputPath.session_id : undefined

        if (typeof sessionId !== 'string' || !sessionId) {
          throw new Error('A conversation is required')
        }

        const load = (streamId?: string) =>
          invokeApi(connection.baseUrl, connection.token || undefined, 'sessions.snapshot', {
            path: { session_id: sessionId },
            query: streamId ? { stream_id: streamId } : {}
          })

        return getGateway()?.synchronize(auth.sessionId, sessionId, load) ?? load()
      }

      return invokeApi<DesktopApiOperationKey>(
        connection.baseUrl,
        connection.token || undefined,
        request.operation,
        ...([request.input] as ApiArguments<DesktopApiOperationKey>)
      )
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
