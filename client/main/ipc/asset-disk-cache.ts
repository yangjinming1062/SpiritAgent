import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import path from 'node:path'

import { sleep } from '@runtime'

import { apiErrorFromResponse } from '../../../shared/api'
import { ASSET_FETCH_TIMEOUT_MS } from '../security/hardening'
import { mimeTypeForPath } from '../shared/mime'
import { atomicWriteFile, isAccountId } from '../shared/utils'

interface AssetMeta {
  etag?: string
  key: string
  mime: string
  size: number
}

export interface CachedAsset {
  buffer: Buffer
  mime: string
}

export interface AssetDiskCacheOptions {
  defaultFetchFn?: typeof globalThis.fetch
  spiritagentHome: string
}

export interface EnsureAssetOptions {
  preferCache?: boolean
  baseUrl?: string
  fetchFn?: typeof globalThis.fetch
  rawUrl: string
  timeoutMs?: number
  token?: string
}

export interface AssetDiskCache {
  clear: (accountId: string) => Promise<void>
  ensureCached: (opts: EnsureAssetOptions & { accountId: string }) => Promise<CachedAsset>
  get: (accountId: string, rawUrl: string) => Promise<CachedAsset | null>
}

function normalizeAssetKey(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl, 'http://127.0.0.1:8000')
    const { searchParams } = parsed

    searchParams.delete('expires')
    searchParams.delete('sig')
    searchParams.sort()
    const query = searchParams.toString()

    return crypto.hash('sha1', `${parsed.host}${parsed.pathname}${query ? `?${query}` : ''}`)
  } catch {
    return crypto.hash('sha1', String(rawUrl))
  }
}

function isAuthFailureStatus(status: number): boolean {
  return status === 401 || status === 403
}

// 只取 pathname+search 拼回受信 baseUrl，丢弃绝对 URL 的 host，避免 Authorization 外泄。
export function resolveBackendAssetUrl(rawUrl: string, baseUrl?: null | string): string {
  if (!baseUrl) {
    return rawUrl
  }

  const { pathname, search } = new URL(rawUrl, baseUrl)

  return `${baseUrl}${pathname}${search}`
}

export function createAssetDiskCache({ defaultFetchFn, spiritagentHome }: AssetDiskCacheOptions): AssetDiskCache {
  const root = path.resolve(spiritagentHome, 'cache', 'assets')
  const accounts = new Map<string, ReturnType<typeof createAccountAssetCache>>()

  async function forAccount(accountId: string) {
    if (!isAccountId(accountId)) {
      throw new Error('Invalid asset cache account')
    }

    let cache = accounts.get(accountId)

    if (!cache) {
      cache = createAccountAssetCache(path.join(root, accountId), defaultFetchFn)
      accounts.set(accountId, cache)
    }

    return cache
  }

  return {
    clear: async accountId => (await forAccount(accountId)).clear(),
    ensureCached: async opts => (await forAccount(opts.accountId)).ensureCached(opts),
    get: async (accountId, rawUrl) => (await forAccount(accountId)).get(rawUrl)
  }
}

function createAccountAssetCache(cacheDir: string, defaultFetchFn?: typeof globalThis.fetch) {
  const inFlightDownloads = new Map<string, { controller: AbortController; promise: Promise<CachedAsset> }>()
  let clearing: Promise<void> | null = null
  let epoch = 0

  async function ensureDir(): Promise<void> {
    await fsp.mkdir(cacheDir, { recursive: true })
  }

  function getBinPath(key: string): string {
    return path.join(cacheDir, `${key}.bin`)
  }

  function getMetaPath(key: string): string {
    return path.join(cacheDir, `${key}.meta.json`)
  }

  async function readMeta(key: string): Promise<AssetMeta | null> {
    try {
      const raw = await fsp.readFile(getMetaPath(key), 'utf8')
      const parsed = JSON.parse(raw) as AssetMeta

      if (typeof parsed?.size === 'number' && typeof parsed?.mime === 'string') {
        return parsed
      }

      return null
    } catch {
      return null
    }
  }

  // meta 与字节同批读出，调用方复用同一份 etag。
  async function readCachedEntry(rawUrl: string): Promise<{ asset: CachedAsset; meta: AssetMeta } | null> {
    const key = normalizeAssetKey(rawUrl)
    const binPath = getBinPath(key)

    try {
      const [buffer, meta] = await Promise.all([fsp.readFile(binPath), readMeta(key)])

      if (!buffer.byteLength || !meta || buffer.byteLength !== meta.size) {
        return null
      }

      return { asset: { buffer, mime: meta.mime || mimeTypeForPath(rawUrl) }, meta }
    } catch {
      return null
    }
  }

  async function get(rawUrl: string): Promise<CachedAsset | null> {
    const readEpoch = epoch
    await clearing
    const entry = await readCachedEntry(rawUrl)

    return readEpoch === epoch ? (entry?.asset ?? null) : null
  }

  function clear(): Promise<void> {
    if (clearing) {
      return clearing
    }

    epoch += 1
    const downloads = [...inFlightDownloads.values()]
    inFlightDownloads.clear()

    for (const download of downloads) {
      download.controller.abort(new Error('asset cache cleared'))
    }

    // 旧写入完成后再删目录；新下载等待清理，避免旧任务删除或恢复新账户的文件。
    clearing = (async () => {
      await Promise.allSettled(downloads.map(download => download.promise))
      await fsp.rm(cacheDir, { recursive: true, force: true })
      await ensureDir()
    })().finally(() => {
      clearing = null
    })

    return clearing
  }

  async function download(opts: EnsureAssetOptions, cancellation: AbortSignal): Promise<CachedAsset> {
    await ensureDir()

    const { baseUrl, fetchFn = defaultFetchFn || globalThis.fetch, rawUrl, timeoutMs, token } = opts

    if (!rawUrl) {
      throw new Error('asset url is required')
    }

    const key = normalizeAssetKey(rawUrl)
    const binPath = getBinPath(key)
    const cachedEntry = await readCachedEntry(rawUrl)
    const localCached = cachedEntry?.asset ?? null
    cancellation.throwIfAborted()

    if (localCached && opts.preferCache) {
      return localCached
    }

    const targetUrl = resolveBackendAssetUrl(rawUrl, baseUrl)
    // 错误与日志只带路径：签名参数（expires / sig）不得进入日志。
    const assetPath = new URL(rawUrl, 'http://127.0.0.1').pathname
    const effectiveTimeout = timeoutMs ?? ASSET_FETCH_TIMEOUT_MS
    const signal = AbortSignal.any([cancellation, AbortSignal.timeout(effectiveTimeout)])

    // 请求头和正文共享总期限；传输中断最多重试一次，取消和超时不重试。
    async function executeDownload(): Promise<{ body: Buffer | null; response: Response }> {
      const headers: Record<string, string> = {}

      if (token) {
        if (!baseUrl) {
          throw new Error('Authorization requires a trusted baseUrl; refusing to send token to arbitrary URL')
        }

        headers.Authorization = `Bearer ${token}`
      }

      const cachedEtag = cachedEntry?.meta.etag

      if (cachedEtag) {
        headers['If-None-Match'] = cachedEtag
      }

      const request = async (): Promise<{ body: Buffer | null; response: Response }> => {
        const response = await fetchFn(targetUrl, { headers, signal })

        if (!response.ok) {
          return { body: null, response }
        }

        const body = Buffer.from(await response.arrayBuffer())

        if (body.byteLength <= 0) {
          throw new Error(`empty asset body: ${assetPath}`)
        }

        return { body, response }
      }

      try {
        return await request()
      } catch {
        signal.throwIfAborted()
        await sleep(150)
        signal.throwIfAborted()

        return request()
      }
    }

    // 网络/正文/落盘失败统一降级本地旧缓存；未命中或已取消则抛给调用方。
    const serveStaleOrThrow = (err: unknown, label: string): CachedAsset => {
      cancellation.throwIfAborted()

      if (localCached) {
        console.warn(`[asset-disk-cache] ${label} for ${assetPath}; serving local stale cache fallback:`, err)

        return localCached
      }

      throw err
    }

    let res: Response
    let body: Buffer | null

    try {
      const downloaded = await executeDownload()
      res = downloaded.response
      body = downloaded.body
    } catch (networkErr) {
      return serveStaleOrThrow(
        new Error(
          signal.aborted
            ? `Asset download timed out after ${effectiveTimeout}ms: ${assetPath}`
            : `Asset download interrupted: ${assetPath}`,
          { cause: networkErr }
        ),
        'Download failed'
      )
    }

    cancellation.throwIfAborted()

    if (res.status === 304 && localCached) {
      return localCached
    }

    if (!res.ok) {
      if (isAuthFailureStatus(res.status) || !localCached) {
        throw await apiErrorFromResponse(res)
      }

      console.warn(
        `[asset-disk-cache] Remote returned status ${res.status} for ${assetPath}; using stale cache fallback`
      )

      return localCached
    }

    const mime = res.headers.get('content-type') || mimeTypeForPath(rawUrl)
    const rawEtag = res.headers.get('etag')
    const rawSha = res.headers.get('x-content-sha256')
    const etag = rawSha || (rawEtag ? rawEtag.replace(/"/g, '') : undefined)

    if (!body) {
      throw new Error(`empty asset body: ${assetPath}`)
    }

    cancellation.throwIfAborted()

    try {
      await atomicWriteFile(binPath, body)

      await atomicWriteFile(
        getMetaPath(key),
        JSON.stringify({ etag, key, mime, size: body.byteLength } satisfies AssetMeta)
      )
    } catch (writeErr) {
      return serveStaleOrThrow(writeErr, 'Write error')
    }

    cancellation.throwIfAborted()

    return {
      buffer: body,
      mime
    }
  }

  async function ensureCached(opts: EnsureAssetOptions): Promise<CachedAsset> {
    const requestEpoch = epoch
    await clearing

    if (requestEpoch !== epoch) {
      throw new Error('asset cache cleared')
    }

    const raw = String(opts?.rawUrl || '')
    const key = normalizeAssetKey(raw)

    const pending = inFlightDownloads.get(key)

    if (pending) {
      return pending.promise
    }

    const controller = new AbortController()

    const promise = download(opts, controller.signal).finally(() => {
      if (inFlightDownloads.get(key)?.promise === promise) {
        inFlightDownloads.delete(key)
      }
    })

    inFlightDownloads.set(key, { controller, promise })

    return await promise
  }

  return {
    clear,
    ensureCached,
    get
  }
}
