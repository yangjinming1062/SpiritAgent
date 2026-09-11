import crypto from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import type { ReadableStream } from 'node:stream/web'

import { dataUrlFromBuffer, mimeTypeForPath } from '../shared/mime'
import { HttpError } from '../shared/utils'

const MAX_CACHE_FILES = 200
const MAX_CACHE_BYTES = 512 * 1024 * 1024
const DEFAULT_TIMEOUT_MS = 15_000
const SIGNED_QUERY_KEYS = new Set(['expires', 'sig', 'token', 't', 'timestamp'])

interface AssetMeta {
  contentHash?: string
  etag?: string
  key: string
  mime: string
  size: number
  writtenAt: number
}

export interface CachedAsset {
  buffer: Buffer
  dataUrl: string
  filePath: string
  fromCache: boolean
  mime: string
}

export interface AssetDiskCacheOptions {
  defaultFetchFn?: typeof globalThis.fetch
  spiritagentHome?: null | string
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
  clear: () => Promise<void>
  ensureCached: (opts: EnsureAssetOptions) => Promise<CachedAsset>
  get: (rawUrl: string, contentHash?: string) => Promise<CachedAsset | null>
  sweep: () => Promise<void>
}

function normalizeAssetKey(rawUrl: string, contentHash?: string): string {
  if (contentHash && contentHash.trim()) {
    return contentHash.trim()
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

export function createAssetDiskCache({ defaultFetchFn, spiritagentHome }: AssetDiskCacheOptions): AssetDiskCache {
  if (!spiritagentHome) {
    throw new Error('createAssetDiskCache: spiritagentHome is required')
  }

  const cacheDir = path.resolve(spiritagentHome, 'cache', 'assets')
  const inFlightDownloads = new Map<string, Promise<CachedAsset>>()
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
    } catch {
      await fsp.unlink(tmp).catch(() => {})
    }
  }

  async function get(rawUrl: string, contentHash?: string): Promise<CachedAsset | null> {
    const key = normalizeAssetKey(rawUrl, contentHash)
    const binPath = getBinPath(key)

    try {
      const [buffer, meta] = await Promise.all([fsp.readFile(binPath), readMeta(key)])

      if (!buffer.byteLength || !meta || buffer.byteLength !== meta.size) {
        return null
      }

      const mime = meta?.mime || mimeTypeForPath(rawUrl) || 'application/octet-stream'
      fsp.utimes(binPath, new Date(), new Date()).catch(() => {})

      return {
        buffer,
        dataUrl: dataUrlFromBuffer(buffer, mime),
        filePath: binPath,
        fromCache: true,
        mime
      }
    } catch {
      return null
    }
  }

  async function sweep(): Promise<void> {
    try {
      await ensureDir()
      const names = await fsp.readdir(cacheDir)
      const metaFiles = names.filter(name => name.endsWith('.meta.json'))

      const entries = await Promise.all(
        metaFiles.map(async name => {
          const key = name.slice(0, -'.meta.json'.length)
          const binPath = getBinPath(key)

          const [statBin, meta] = await Promise.all([fsp.stat(binPath).catch(() => null), readMeta(key)])

          if (!statBin?.isFile() || statBin.size <= 0 || !meta) {
            return null
          }

          return {
            binPath,
            metaPath: path.join(cacheDir, name),
            size: statBin.size,
            writtenAt: meta.writtenAt || statBin.mtimeMs
          }
        })
      )

      const files = entries
        .filter((entry): entry is NonNullable<typeof entry> => entry !== null)
        .sort((left, right) => left.writtenAt - right.writtenAt)

      let totalBytes = files.reduce((sum, file) => sum + file.size, 0)
      let count = files.length

      for (const file of files) {
        if (count <= MAX_CACHE_FILES && totalBytes <= MAX_CACHE_BYTES) {
          break
        }

        await Promise.all([fsp.unlink(file.binPath).catch(() => {}), fsp.unlink(file.metaPath).catch(() => {})])
        totalBytes -= file.size
        count -= 1
      }
    } catch (err) {
      console.warn('[asset-disk-cache] sweep failed', err)
    }
  }

  async function clear(): Promise<void> {
    epoch += 1
    inFlightDownloads.clear()
    await fsp.rm(cacheDir, { recursive: true, force: true })
    await ensureDir()
  }

  async function download(opts: EnsureAssetOptions): Promise<CachedAsset> {
    const downloadEpoch = epoch
    await ensureDir()

    const { baseUrl, contentHash, fetchFn = defaultFetchFn || globalThis.fetch, rawUrl, timeoutMs, token } = opts

    if (!rawUrl) {
      throw new Error('asset url is required')
    }

    const key = normalizeAssetKey(rawUrl, contentHash)
    const binPath = getBinPath(key)
    const partialPath = getPartialPath(key)
    const localCached = await get(rawUrl, contentHash)

    if (downloadEpoch !== epoch) {
      throw new Error('asset cache cleared')
    }

    if (localCached && (contentHash || opts.preferCache)) {
      return localCached
    }

    const targetUrl = resolveBackendAssetUrl(rawUrl, baseUrl)
    const effectiveTimeout = timeoutMs ?? DEFAULT_TIMEOUT_MS

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

      const controller = new AbortController()

      const timer = setTimeout(() => {
        controller.abort(new Error(`Asset fetch timed out after ${effectiveTimeout}ms`))
      }, effectiveTimeout)

      try {
        return await fetchFn(targetUrl, {
          headers,
          signal: controller.signal
        })
      } catch (fetchErr) {
        if (retryCount < 1) {
          await new Promise(resolve => setTimeout(resolve, 150))

          return executeFetch(retryCount + 1)
        }

        throw fetchErr
      } finally {
        clearTimeout(timer)
      }
    }

    let res: Response

    try {
      res = await executeFetch()
    } catch (networkErr) {
      if (localCached) {
        console.warn(
          `[asset-disk-cache] Network fetch failed for ${rawUrl}; serving local stale cache fallback:`,
          networkErr
        )

        return localCached
      }

      throw networkErr
    }

    if (downloadEpoch !== epoch) {
      throw new Error('asset cache cleared')
    }

    if (res.status === 304 && localCached) {
      const now = Date.now()

      const meta = (await readMeta(key)) || {
        key,
        mime: localCached.mime,
        size: localCached.buffer.byteLength,
        writtenAt: now
      }

      meta.writtenAt = now
      await writeMeta(key, meta)
      fsp.utimes(binPath, new Date(), new Date()).catch(() => {})

      return localCached
    }

    if (!res.ok) {
      if (isAuthFailureStatus(res.status) || !localCached) {
        const text = await res.text().catch(() => '')
        throw new HttpError(res.status, `${res.status} ${rawUrl}: ${text || res.statusText}`)
      }

      console.warn(`[asset-disk-cache] Remote returned status ${res.status} for ${rawUrl}; using stale cache fallback`)

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

      await pipeline(readableNodeStream, writeStream)
    } catch (streamErr) {
      await fsp.unlink(partialPath).catch(() => {})

      if (localCached) {
        console.warn(`[asset-disk-cache] Stream error for ${rawUrl}; serving local stale cache fallback:`, streamErr)

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

      throw new Error(`empty asset body: ${rawUrl}`)
    }

    if (downloadEpoch !== epoch) {
      await fsp.unlink(partialPath).catch(() => {})
      throw new Error('asset cache cleared')
    }

    await fsp.rename(partialPath, binPath)

    const now = Date.now()

    await writeMeta(key, {
      contentHash,
      etag,
      key,
      mime,
      size: stat.size,
      writtenAt: now
    })

    const buffer = await fsp.readFile(binPath)
    sweep().catch(() => {})

    return {
      buffer,
      dataUrl: dataUrlFromBuffer(buffer, mime),
      filePath: binPath,
      fromCache: false,
      mime
    }
  }

  async function ensureCached(opts: EnsureAssetOptions): Promise<CachedAsset> {
    const raw = String(opts?.rawUrl || '')
    const key = normalizeAssetKey(raw, opts?.contentHash)

    if (inFlightDownloads.has(key)) {
      return await inFlightDownloads.get(key)!
    }

    const promise = download(opts).finally(() => {
      if (inFlightDownloads.get(key) === promise) {
        inFlightDownloads.delete(key)
      }
    })

    inFlightDownloads.set(key, promise)

    return await promise
  }

  return {
    clear,
    ensureCached,
    get,
    sweep
  }
}
