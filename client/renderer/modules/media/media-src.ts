import { useEffect, useState } from 'react'

import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

// 图片结果与在途读取分别缓存，淘汰旧图片不影响仍在等待的消费者。
const MAX_IMAGE_SRC_ENTRIES = 80
const imageSrcCache = new Map<string, string>()
const imageSrcRequests = new Map<string, Promise<string | null>>()

function setImageSrc(url: string, dataUrl: string): void {
  if (imageSrcCache.size >= MAX_IMAGE_SRC_ENTRIES) {
    const oldestKey = imageSrcCache.keys().next().value

    if (oldestKey !== undefined) {
      imageSrcCache.delete(oldestKey)
    }
  }

  imageSrcCache.set(url, dataUrl)
}

registerStorageClearHandler(() => {
  imageSrcCache.clear()
  imageSrcRequests.clear()
})

// 本地绝对路径（Windows 盘符 / UNC / POSIX 根）：这些 URL 不经过后端资产通道，需要主进程直接读盘。后端媒体是 HTTP(S) URL 或相对路径，落不进这三个形态。
const LOCAL_PATH_RE = /^(?:[a-zA-Z]:[\\/]|\\\\|\/(?!\/))/

// apiAssetBuffer 只回字节不回 Content-Type，视频 blob 的 mime 由 URL 扩展名推导。
const MEDIA_MIME_BY_EXT: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.mov': 'video/quicktime',
  '.webm': 'video/webm',
  '.mp3': 'audio/mpeg',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg',
  '.m4a': 'audio/mp4',
  '.aac': 'audio/aac',
  '.flac': 'audio/flac'
}

/** 把媒体 URL 解析为渲染端可用 src：data URL 零开销直用；本地路径读盘；其余走后端资产通道。 */
function resolveImageSrc(url: string): string | Promise<string | null> {
  if (url.startsWith('data:')) {
    return url
  }

  const cached = imageSrcCache.get(url) ?? imageSrcRequests.get(url)

  if (cached) {
    return cached
  }

  const epoch = currentClearEpoch()

  const load = (async () => {
    const dataUrl = await (LOCAL_PATH_RE.test(url) && !url.startsWith('/api/')
      ? window.spiritagent.readFileDataUrl(url)
      : window.spiritagent.apiAsset({ url, preferCache: true }))

    if (epoch !== currentClearEpoch()) {
      return null
    }

    if (dataUrl) {
      setImageSrc(url, dataUrl)
    }

    return dataUrl
  })().finally(() => {
    if (imageSrcRequests.get(url) === load) {
      imageSrcRequests.delete(url)
    }
  })

  imageSrcRequests.set(url, load)

  return load
}

type MediaSrcState = { status: 'failed' } | { status: 'loading' } | { status: 'ready'; src: string }

const LOADING: MediaSrcState = { status: 'loading' }

/** 把后端媒体 URL 解析为渲染端可用 src：图片走 data URL 通道；视频取字节转 blob URL，组件卸载时回收。 */
export function useResolvedMediaSrc(item: ChatMediaItem): MediaSrcState {
  const [resolved, setResolved] = useState<{ url: string; type: ChatMediaItem['type']; state: MediaSrcState } | null>(
    null
  )

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    const epoch = currentClearEpoch()
    const isCurrent = (): boolean => !cancelled && epoch === currentClearEpoch()

    if (!item.url) {
      return
    }
    void (async () => {
      try {
        if (item.type === 'image') {
          const dataUrl = await resolveImageSrc(item.url)

          if (!isCurrent()) {
            return
          }

          if (!dataUrl) {
            throw new Error('Media asset returned no data')
          }

          setResolved({ url: item.url, type: item.type, state: { status: 'ready', src: dataUrl } })
        } else {
          const buf = await window.spiritagent.apiAssetBuffer({ url: item.url, preferCache: true })

          if (!isCurrent()) {
            return
          }

          const clean = item.url.split(/[?#]/)[0]
          const ext = clean.slice(clean.lastIndexOf('.')).toLowerCase()
          // 拷贝进全新 ArrayBuffer —— IPC 返回的 Uint8Array 类型上可能是 SharedArrayBuffer 视图，不满足 BlobPart。
          objectUrl = URL.createObjectURL(
            new Blob([new Uint8Array(buf)], {
              type: MEDIA_MIME_BY_EXT[ext] || (item.type === 'audio' ? 'audio/mpeg' : 'video/mp4')
            })
          )
          setResolved({ url: item.url, type: item.type, state: { status: 'ready', src: objectUrl } })
        }
      } catch (err) {
        if (isCurrent()) {
          log.warn('media', 'Media source could not be loaded:', err)
          setResolved({ url: item.url, type: item.type, state: { status: 'failed' } })
        }
      }
    })()

    return () => {
      cancelled = true

      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [item.type, item.url])

  return resolved?.url === item.url && resolved.type === item.type ? resolved.state : LOADING
}
