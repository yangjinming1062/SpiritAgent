import { OpfsBlobCache } from '@/shared/lib/opfs-blob-cache'
import { registerStorageClearHandler } from '@/shared/lib/storage'

const glbCache = new OpfsBlobCache({
  dirName: 'glb-cache',
  blobSuffix: '.glb',
  maxFiles: 5,
  maxBytes: 512 * 1024 * 1024,
  logTag: 'glb-opfs-cache'
})

function clearGlbCache(): Promise<void> {
  return glbCache.clear()
}

registerStorageClearHandler(clearGlbCache)

/** GLB fetcher：走 spiritagent-media:// URL 桥（主进程命中磁盘缓存可直接 200）。
 *  abort 时返回 null，错误抛给上层走 throwOnError=false 吞掉。 */

async function glbFetcher(
  url: string,
  contentHash: string | null | undefined,
  signal: AbortSignal
): Promise<ArrayBuffer | null> {
  const mediaUrl = await window.spiritagent.apiAssetModelUrl({
    url,
    contentHash: contentHash || undefined
  })

  if (signal.aborted) {
    return null
  }

  // eslint-disable-next-line no-restricted-syntax -- URL 是主进程铸造的 spiritagent-media:// 自定义协议，非后端相对路径
  const res = await fetch(mediaUrl, { signal })

  if (!res.ok) {
    throw new Error(`Media protocol fetch failed with status ${res.status}`)
  }

  return await res.arrayBuffer()
}

// 键是 contentHash 而非 URL —— 后端的签名 URL 查询串会轮换。
export async function fetchGlbWithCache(
  url: string,
  contentHash?: string,
  signal?: AbortSignal
): Promise<ArrayBuffer | null> {
  return glbCache.fetchWithCache({
    contentHash,
    fetcher: sig => glbFetcher(url, contentHash, sig),
    signal,
    url
  })
}
