import { useEffect, useState } from 'react'

import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { trimOldest } from '@/shared/lib/trim-oldest'
import { backendApi, fileApi } from '@/shared/platform/desktop'
import type { ChatMediaItem } from '@protocol'

// 图片结果与在途读取分别缓存，淘汰旧图片不影响仍在等待的消费者。
const MAX_IMAGE_SRC_ENTRIES = 80
const imageSrcCache = new Map<string, string>()
const imageSrcRequests = new Map<string, Promise<string | null>>()

function setImageSrc(url: string, dataUrl: string): void {
  imageSrcCache.set(url, dataUrl)
  trimOldest(imageSrcCache, MAX_IMAGE_SRC_ENTRIES)
}

registerStorageClearHandler(() => {
  imageSrcCache.clear()
  imageSrcRequests.clear()
})

// 本地绝对路径（Windows 盘符 / UNC / POSIX 根）：不经过后端资产通道，需要主进程直接读盘。后端的 `/api/…` 相对路径同样以 `/` 开头，使用处另行排除。
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

/** 图片本地路径读盘，其余走后端资产通道。 */
function resolveImageSrc(url: string): string | Promise<string | null> {
  const cached = imageSrcCache.get(url) ?? imageSrcRequests.get(url)

  if (cached) {
    return cached
  }

  const epoch = currentClearEpoch()

  const load = (async () => {
    const dataUrl = await (LOCAL_PATH_RE.test(url) && !url.startsWith('/api/')
      ? fileApi().readFileDataUrl(url)
      : backendApi().apiAsset({ url, preferCache: true }))

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

/** data URL 直接使用；图片经共享缓存解析，影音取字节转 blob URL，组件卸载时回收。 */
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
        let src: string | null

        if (item.url.startsWith('data:')) {
          src = item.url
        } else if (item.type === 'image') {
          src = await resolveImageSrc(item.url)
        } else {
          const buf = await backendApi().apiAssetBuffer({ url: item.url, preferCache: true })

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
          src = objectUrl
        }

        if (!isCurrent()) {
          return
        }

        if (!src) {
          throw new Error('Media asset returned no data')
        }

        setResolved({ url: item.url, type: item.type, state: { status: 'ready', src } })
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
