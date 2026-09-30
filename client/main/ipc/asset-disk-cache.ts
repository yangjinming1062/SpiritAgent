import crypto from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import type { ReadableStream } from 'node:stream/web'

import { mimeTypeForPath } from '../shared/mime'
import { HttpError } from '../shared/utils'

const DEFAULT_TIMEOUT_MS = 15_000
const SIGNED_QUERY_KEYS = new Set(['expires', 'sig', 'token', 't', 'timestamp'])

interface AssetMeta {
  contentHash?: string
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
  initialAccountId: Promise<string | null>
  spiritagentHome: string
}

export interface EnsureAssetOptions {
  preferCache?: boolean
  baseUrl?: string
  contentHash?: string
  fetchFn?: typeof globalThis.fetch
  rawUrl: string
  timeoutMs?: number
  token?: string
}

export interface AssetDiskCache {
  clear: (accountId: string) => Promise<void>
  ensureCached: (opts: EnsureAssetOptions & { accountId: string }) => Promise<CachedAsset>
  get: (accountId: string, rawUrl: string, contentHash?: string) => Promise<CachedAsset | null>
}

function normalizeAssetKey(rawUrl: string, contentHash?: string): string {
  if (contentHash && contentHash.trim()) {
    const hash = contentHash.trim().toLowerCase()

    if (!/^[a-f0-9]{64}$/.test(hash)) {
      throw new Error('asset contentHash must be a SHA-256 digest')
    }

    return hash
  }

  try {
    const parsed = new URL(rawUrl, 'http://127.0.0.1:8000')
    const searchParams = new URLSearchParams(parsed.search)

    for (const key of [...searchParams.keys()]) {
      if (SIGNED_QUERY_KEYS.has(key.toLowerCase())) {
        searchParams.delete(key)
      }
    }

    searchParams.sort()
    const query = searchParams.toString()
    const normalized = `${parsed.host}${parsed.pathname}${query ? `?${query}` : ''}`

    return crypto.createHash('sha1').update(normalized).digest('hex')
  } catch {
    return crypto.createHash('sha1').update(String(rawUrl)).digest('hex')
  }
}

function isAuthFailureStatus(status: number): boolean {
  return status === 401 || status === 403
}

// 只取 pathname+search 拼回受信 baseUrl，丢弃绝对 URL 的 host，避免 Authorization 外泄。
function resolveBackendAssetUrl(rawUrl: string, baseUrl?: null | string): string {
  if (!baseUrl) {
    return rawUrl
  }

  const { pathname, search } = new URL(rawUrl, baseUrl)

  return `${baseUrl}${pathname}${search}`
}

export function createAssetDiskCache({
  defaultFetchFn,
  initialAccountId,
  spiritagentHome
}: AssetDiskCacheOptions): AssetDiskCache {
  const root = path.resolve(spiritagentHome, 'cache', 'assets')
  const accounts = new Map<string, ReturnType<typeof createAccountAssetCache>>()
  let migration: Promise<void> | null = null

  // 原平铺缓存只归属启动时已选账户；迁移后不再按旧目录回退，避免跨账户命中。
  async function migrateLegacyCache(): Promise<void> {
    const accountId = await initialAccountId

    if (!accountId || !/^[a-f0-9]{64}$/.test(accountId)) {
      return
    }

    let entries

    try {
      entries = await fsp.readdir(root, { withFileTypes: true })
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        return
      }

      throw error
    }

    const files = entries.filter(entry => entry.isFile())

    if (files.length > 0) {
      const target = path.join(root, accountId)
      await fsp.mkdir(target, { recursive: true })

      for (const file of files) {
        await fsp.rename(path.join(root, file.name), path.join(target, file.name))
      }
    }
  }

  async function forAccount(accountId: string) {
    if (!/^[a-f0-9]{64}$/.test(accountId)) {
      throw new Error('Invalid asset cache account')
    }

    await (migration ??= migrateLegacyCache())
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
    get: async (accountId, rawUrl, contentHash) => (await forAccount(accountId)).get(rawUrl, contentHash)
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

  function getPartialPath(key: string): string {
    return path.join(cacheDir, `${key}.partial`)
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

  async function writeMeta(key: string, meta: AssetMeta): Promise<void> {
    const tmp = `${getMetaPath(key)}.${process.pid}.${Date.now()}.tmp`

    try {
      await fsp.writeFile(tmp, JSON.stringify(meta), 'utf8')
      await fsp.rename(tmp, getMetaPath(key))
    } catch (error) {
      await fsp.unlink(tmp).catch(() => {})

      throw error
    }
  }

  async function readCached(rawUrl: string, contentHash?: string): Promise<CachedAsset | null> {
    const key = normalizeAssetKey(rawUrl, contentHash)
    const binPath = getBinPath(key)

    try {
      const [buffer, meta] = await Promise.all([fsp.readFile(binPath), readMeta(key)])

      if (!buffer.byteLength || !meta || buffer.byteLength !== meta.size) {
        return null
      }

      const mime = meta?.mime || mimeTypeForPath(rawUrl) || 'application/octet-stream'

      return {
        buffer,
        mime
      }
    } catch {
      return null
    }
  }

  async function get(rawUrl: string, contentHash?: string): Promise<CachedAsset | null> {
    const readEpoch = epoch
    await clearing
    const asset = await readCached(rawUrl, contentHash)

    return readEpoch === epoch ? asset : null
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

    const { baseUrl, contentHash, fetchFn = defaultFetchFn || globalThis.fetch, rawUrl, timeoutMs, token } = opts

    if (!rawUrl) {
      throw new Error('asset url is required')
    }

    const key = normalizeAssetKey(rawUrl, contentHash)
    const binPath = getBinPath(key)
    const partialPath = getPartialPath(key)
    const localCached = await readCached(rawUrl, contentHash)
    cancellation.throwIfAborted()

    if (localCached && (contentHash || opts.preferCache)) {
      return localCached
    }

    const targetUrl = resolveBackendAssetUrl(rawUrl, baseUrl)
    // 错误与日志只带路径：签名参数（expires / sig）不得进入日志。
    const assetPath = new URL(rawUrl, 'http://127.0.0.1').pathname
    const effectiveTimeout = timeoutMs ?? DEFAULT_TIMEOUT_MS
    const signal = AbortSignal.any([cancellation, AbortSignal.timeout(effectiveTimeout)])

    async function executeFetch(retryCount = 0): Promise<Response> {
      const headers: Record<string, string> = {}

      if (token) {
        if (!baseUrl) {
          throw new Error('Authorization requires a trusted baseUrl; refusing to send token to arbitrary URL')
        }

        headers.Authorization = `Bearer ${token}`
      }

      if (localCached) {
        const meta = await readMeta(key)

        if (meta?.etag) {
          headers['If-None-Match'] = meta.etag
        }
      }

      try {
        return await fetchFn(targetUrl, {
          headers,
          signal
        })
      } catch (fetchErr) {
        if (retryCount < 1 && !signal.aborted) {
          await new Promise(resolve => setTimeout(resolve, 150))
          signal.throwIfAborted()

          return executeFetch(retryCount + 1)
        }

        throw fetchErr
      }
    }

    let res: Response

    try {
      res = await executeFetch()
    } catch (networkErr) {
      cancellation.throwIfAborted()

      if (localCached) {
        console.warn(
          `[asset-disk-cache] Network fetch failed for ${assetPath}; serving local stale cache fallback:`,
          networkErr
        )

        return localCached
      }

      throw networkErr
    }

    cancellation.throwIfAborted()

    if (res.status === 304 && localCached) {
      return localCached
    }

    if (!res.ok) {
      if (isAuthFailureStatus(res.status) || !localCached) {
        const text = await res.text().catch(() => '')
        throw new HttpError(res.status, `${res.status} ${assetPath}: ${text || res.statusText}`)
      }

      console.warn(
        `[asset-disk-cache] Remote returned status ${res.status} for ${assetPath}; using stale cache fallback`
      )

      return localCached
    }

    const mime = res.headers.get('content-type') || mimeTypeForPath(rawUrl) || 'application/octet-stream'
    const rawEtag = res.headers.get('etag')
    const rawSha = res.headers.get('x-content-sha256')
    const etag = rawSha || (rawEtag ? rawEtag.replace(/"/g, '') : undefined)

    try {
      const writeStream = fs.createWriteStream(partialPath)
      const bodyStream = res.body
      let readableNodeStream: Readable

      if (bodyStream && typeof (bodyStream as { getReader?: unknown }).getReader === 'function') {
        readableNodeStream = Readable.fromWeb(bodyStream as unknown as ReadableStream)
      } else if (bodyStream && Symbol.asyncIterator in bodyStream) {
        readableNodeStream = Readable.from(bodyStream)
      } else {
        const arrayBuf = await res.arrayBuffer()
        readableNodeStream = Readable.from(Buffer.from(arrayBuf))
      }

      await pipeline(readableNodeStream, writeStream, { signal })
    } catch (streamErr) {
      await fsp.unlink(partialPath).catch(() => {})
      cancellation.throwIfAborted()

      if (localCached) {
        console.warn(`[asset-disk-cache] Stream error for ${assetPath}; serving local stale cache fallback:`, streamErr)

        return localCached
      }

      throw streamErr
    }

    const stat = await fsp.stat(partialPath).catch(() => null)

    if (!stat?.isFile() || stat.size <= 0) {
      await fsp.unlink(partialPath).catch(() => {})

      if (localCached) {
        return localCached
      }

      throw new Error(`empty asset body: ${assetPath}`)
    }

    cancellation.throwIfAborted()

    await fsp.rename(partialPath, binPath)

    await writeMeta(key, {
      contentHash,
      etag,
      key,
      mime,
      size: stat.size
    })

    const buffer = await fsp.readFile(binPath)
    cancellation.throwIfAborted()

    return {
      buffer,
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
    const key = normalizeAssetKey(raw, opts?.contentHash)

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
